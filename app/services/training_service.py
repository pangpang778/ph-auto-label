"""Training domain service.

Business logic for training jobs, split config, dataset building, and the
background run_training_job. Flask-context-free (runs in a background thread);
paths resolve via PATHS. Cross-domain calls into the models domain go through
models_service (closed-world contract, api-freeze.md §2).
"""
import json
import math
import os
import random
import shutil
import threading
import uuid
from datetime import datetime
from io import StringIO

import cv2
import numpy as np
from PIL import Image

from app.common.config import PATHS
from app.common.json_store import read_json_file, write_json_file
from app.common.utils import now_iso
from app.services.annotation_service import read_annotations, read_classes
from app.repositories.train_jobs_repo import TRAIN_JOBS_LOCK, read_train_jobs, upsert_train_job, write_train_jobs
from app.repositories.training_splits_repo import TRAINING_SPLITS_LOCK, load_split_profile, read_training_splits, write_training_splits
from app.services import models_service
from app.services.training_job_runner import (
    _extract_metrics_from_results_csv,
    append_train_log,
    run_training_job,
)


def training_readiness() -> dict:
    annotations = read_annotations()
    total_images = 0
    annotated_images = 0
    valid_suffix = ('.png', '.jpg', '.jpeg', '.bmp')
    for name in os.listdir(PATHS['uploads']):
        if name.lower().endswith(valid_suffix):
            total_images += 1
            anns = annotations.get(name, [])
            if anns:
                annotated_images += 1
    return {
        "total_images": total_images,
        "annotated_images": annotated_images,
        "min_for_initial": 20,
        "ready_for_initial": annotated_images >= 20,
        "cuda": get_cuda_status(),
    }



def get_cuda_status() -> dict:
    """Return CUDA availability for the current Flask runtime environment."""
    status = {
        "available": False,
        "device_count": 0,
        "device_name": "",
        "torch_version": "",
        "error": "",
    }
    try:
        import torch

        status["torch_version"] = str(getattr(torch, "__version__", ""))
        status["available"] = bool(torch.cuda.is_available())
        status["device_count"] = int(torch.cuda.device_count() if status["available"] else 0)
        if status["available"] and status["device_count"] > 0:
            status["device_name"] = str(torch.cuda.get_device_name(0))
    except Exception as exc:
        status["error"] = str(exc)
    return status



def normalize_split_config(raw: dict | None) -> dict:
    raw = raw or {}
    train_ratio = float(raw.get("train_ratio", 0.8))
    val_ratio = float(raw.get("val_ratio", 0.15))
    test_ratio = float(raw.get("test_ratio", 0.05))
    total = train_ratio + val_ratio + test_ratio
    if abs(total - 100.0) <= 0.01:
        train_ratio /= 100.0
        val_ratio /= 100.0
        test_ratio /= 100.0
        total = train_ratio + val_ratio + test_ratio
    if min(train_ratio, val_ratio, test_ratio) <= 0 or abs(total - 1.0) > 0.001:
        raise ValueError("train_ratio, val_ratio and test_ratio must be positive and sum to 1.0 or 100")

    class_filter = raw.get("class_filter") or raw.get("selected_classes") or []
    if isinstance(class_filter, str):
        class_filter = [x.strip() for x in class_filter.replace("，", ",").split(",") if x.strip()]
    else:
        class_filter = [str(x).strip() for x in class_filter if str(x).strip()]

    assignments = raw.get("assignments") if isinstance(raw.get("assignments"), dict) else {}
    normalized_assignments = {}
    for split in ("train", "val", "test"):
        names = assignments.get(split, []) if assignments else []
        normalized_assignments[split] = [str(name) for name in names if str(name)]

    return {
        "profile_id": str(raw.get("profile_id") or "default"),
        "train_ratio": train_ratio,
        "val_ratio": val_ratio,
        "test_ratio": test_ratio,
        "sample_filter": str(raw.get("sample_filter") or "annotated"),
        "class_filter": class_filter,
        "assignments": normalized_assignments,
    }



def _image_matches_class_filter(image_name: str, annotations: dict, class_filter: list[str]) -> bool:
    if not class_filter:
        return True
    selected = set(class_filter)
    return any(ann.get("class") in selected for ann in annotations.get(image_name, []))



def collect_training_candidates(split_config: dict | None = None) -> dict:
    config = normalize_split_config(split_config)
    classes = read_classes()
    class_names = [c.get('name') for c in classes if c.get('name')]
    annotations = read_annotations()
    image_names = sorted(name for name in os.listdir(PATHS['uploads']) if name.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp')))
    annotated = [name for name in image_names if annotations.get(name)]
    filtered = [name for name in annotated if _image_matches_class_filter(name, annotations, config["class_filter"])]
    unannotated = [name for name in image_names if not annotations.get(name)]
    return {
        "class_names": class_names,
        "image_names": image_names,
        "annotations": annotations,
        "annotated": annotated,
        "candidates": filtered,
        "unannotated": unannotated,
        "config": config,
    }



def assign_training_splits(candidates: list[str], split_config: dict | None = None, legacy: bool = False) -> dict:
    if legacy:
        annotated = list(candidates)
        random.Random(42).shuffle(annotated)
        n = len(annotated)
        n_train = max(1, int(n * 0.8))
        n_val = max(1, int(n * 0.15))
        train_set = annotated[:n_train]
        val_set = annotated[n_train:n_train + n_val]
        # ponytail: too few candidates -> empty test set rather than reusing a
        # train image (silent train/test leak). n=20 still yields test=1.
        test_set = annotated[n_train + n_val:]
        return {"train": train_set, "val": val_set, "test": test_set}

    config = normalize_split_config(split_config)
    candidate_set = set(candidates)
    requested = config.get("assignments", {})
    if any(requested.get(split) for split in ("train", "val", "test")):
        assigned = {split: [name for name in requested.get(split, []) if name in candidate_set] for split in ("train", "val", "test")}
        used = set(assigned["train"] + assigned["val"] + assigned["test"])
        leftovers = [name for name in candidates if name not in used]
        assigned["train"].extend(leftovers)
        return assigned

    shuffled = list(candidates)
    random.Random(42).shuffle(shuffled)
    n = len(shuffled)
    n_train = max(1, int(n * config["train_ratio"]))
    n_val = max(1, int(n * config["val_ratio"]))
    if n_train + n_val >= n:
        n_val = max(1, n - n_train - 1) if n > 2 else max(0, n - n_train)
    train_set = shuffled[:n_train]
    val_set = shuffled[n_train:n_train + n_val]
    # ponytail: contiguous slices are structurally disjoint; the old
    # `or shuffled[:1]` fallback reused a train image when too few candidates
    # -> silent train/test leak. Empty test set is the correct outcome.
    test_set = shuffled[n_train + n_val:]
    return {"train": train_set, "val": val_set, "test": test_set}



def split_counts(splits: dict) -> dict:
    return {split: len(splits.get(split, [])) for split in ("train", "val", "test")}



def build_split_summary(split_config: dict | None = None, persist_assignments: bool = False) -> dict:
    candidates = collect_training_candidates(split_config)
    splits = assign_training_splits(candidates["candidates"], candidates["config"])
    config = dict(candidates["config"])
    if persist_assignments:
        config["assignments"] = splits
    return {
        "split_config": config,
        "counts": split_counts(splits),
        "class_options": candidates["class_names"],
        "candidate_totals": {
            "total_images": len(candidates["image_names"]),
            "annotated_images": len(candidates["annotated"]),
            "candidate_images": len(candidates["candidates"]),
            "unannotated_images": len(candidates["unannotated"]),
        },
        "updated_at": now_iso(),
    }



def save_split_profile(split_config: dict) -> dict:
    summary = build_split_summary(split_config, persist_assignments=True)
    profile_id = summary["split_config"].get("profile_id") or "default"
    with TRAINING_SPLITS_LOCK:
        profiles = read_json_file(PATHS['training_splits'], {})
        profiles[profile_id] = summary
        write_json_file(PATHS['training_splits'], profiles)
    return summary



def _annotation_to_bbox(ann: dict, width: int, height: int):
    points = ann.get('points', [])
    if not points:
        return None
    valid_points = []
    if isinstance(points, list):
        if points and isinstance(points[0], dict):
            for p in points:
                if p.get('x') is not None and p.get('y') is not None:
                    valid_points.append([float(p['x']), float(p['y'])])
        else:
            for p in points:
                if isinstance(p, (list, tuple)) and len(p) >= 2 and p[0] is not None and p[1] is not None:
                    valid_points.append([float(p[0]), float(p[1])])
    if not valid_points:
        return None
    arr = np.array(valid_points, dtype=np.float32)
    x_min, y_min = float(np.min(arr[:, 0])), float(np.min(arr[:, 1]))
    x_max, y_max = float(np.max(arr[:, 0])), float(np.max(arr[:, 1]))
    x_min = max(0.0, min(x_min, width))
    y_min = max(0.0, min(y_min, height))
    x_max = max(0.0, min(x_max, width))
    y_max = max(0.0, min(y_max, height))
    bw = x_max - x_min
    bh = y_max - y_min
    if bw <= 1e-6 or bh <= 1e-6:
        return None
    cx = (x_min + x_max) / 2.0 / max(width, 1)
    cy = (y_min + y_max) / 2.0 / max(height, 1)
    nw = bw / max(width, 1)
    nh = bh / max(height, 1)
    return cx, cy, nw, nh



def _write_yolo_label_file(label_path: str, anns: list, class_to_id: dict, image_path: str) -> None:
    with Image.open(image_path) as img:
        width, height = img.size
    lines = []
    for ann in anns:
        cls_name = ann.get("class")
        if cls_name not in class_to_id:
            continue
        box = _annotation_to_bbox(ann, width, height)
        if not box:
            continue
        cx, cy, nw, nh = box
        lines.append(f"{class_to_id[cls_name]} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}")
    with open(label_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))



def build_yolo_training_dataset(work_dir: str, split_config: dict | None = None) -> dict:
    candidates = collect_training_candidates(split_config)
    class_names = candidates["class_names"]
    if not class_names:
        raise RuntimeError("No classes found. Please create labels first.")
    class_to_id = {name: i for i, name in enumerate(class_names)}
    annotations = candidates["annotations"]
    training_images = candidates["annotated"] if split_config is None else candidates["candidates"]
    if len(training_images) < 20:
        raise RuntimeError("Need at least 20 annotated images before training.")

    splits = assign_training_splits(training_images, candidates["config"], legacy=split_config is None)
    dataset_root = os.path.join(work_dir, "dataset")
    for split in splits.keys():
        os.makedirs(os.path.join(dataset_root, split, "images"), exist_ok=True)
        os.makedirs(os.path.join(dataset_root, split, "labels"), exist_ok=True)

    for split, names in splits.items():
        for image_name in names:
            src = os.path.join(PATHS['uploads'], image_name)
            dst = os.path.join(dataset_root, split, "images", image_name)
            shutil.copy2(src, dst)
            label_path = os.path.join(dataset_root, split, "labels", os.path.splitext(image_name)[0] + ".txt")
            _write_yolo_label_file(label_path, annotations.get(image_name, []), class_to_id, src)

    data_yaml = os.path.join(dataset_root, "data.yaml")
    with open(data_yaml, "w", encoding="utf-8") as f:
        dataset_root_abs = os.path.abspath(dataset_root).replace("\\", "/")
        f.write(f"path: {dataset_root_abs}\n")
        f.write("train: train/images\n")
        f.write("val: val/images\n")
        f.write("test: test/images\n\n")
        f.write(f"nc: {len(class_names)}\n")
        f.write(f"names: {class_names}\n")

    return {
        "dataset_root": dataset_root,
        "data_yaml": data_yaml,
        "class_names": class_names,
        "total_images": len(candidates["image_names"]),
        "annotated_images": len(candidates["annotated"]),
        "candidate_images": len(training_images),
        "split_counts": split_counts(splits),
        "split_config": candidates["config"],
        "assignments": splits,
    }



def build_train_job(payload, mode, readiness, active):
    """Resolve split_config + base_model and build the queued training job dict.

    Raises ``ValueError`` on invalid split_config (handler maps to 400).
    """
    if payload.get("split_config"):
        split_config = normalize_split_config(payload.get("split_config"))
    else:
        split_config = load_split_profile(str(payload.get("split_profile_id") or "default"))
        split_config = normalize_split_config(split_config or {"profile_id": str(payload.get("split_profile_id") or "default")})
    base_model = payload.get("base_model")
    if not base_model:
        if mode == "incremental" and active.get("model_path") and os.path.exists(active.get("model_path")):
            base_model = active.get("model_path")
        else:
            base_model = "yolo11n.pt"
    job_id = f"train_{uuid.uuid4().hex[:10]}"
    epochs = int(payload.get("epochs", 30))
    return {
        "id": job_id,
        "mode": mode,
        "status": "queued",
        "progress": 0,
        "message": "Queued",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "base_model": base_model,
        "epochs": epochs,
        "imgsz": int(payload.get("imgsz", 640)),
        "batch": int(payload.get("batch", 8)),
        "device": resolve_training_device(payload.get("device", "auto")),
        "annotated_images": readiness["annotated_images"],
        "total_images": readiness["total_images"],
        "split_config": split_config,
        "split_counts": {},
        "log_path": os.path.join(PATHS['train_work'], job_id, "train.log"),
        "log_tail": "",
        "results_csv": "",
        "results_png": "",
        "weights_path": "",
        "run_dir": "",
        "epoch": 0,
        "total_epochs": epochs,
    }


def resolve_training_device(requested_device):
    """Resolve train device from user input. Supports 'auto' -> CUDA if available."""
    value = str(requested_device or "auto").strip().lower()
    if value in {"", "auto", "default"}:
        try:
            import torch
            if torch.cuda.is_available():
                return 0
        except Exception:
            pass
        return "cpu"
    return requested_device



def _artifact_allowed_roots() -> list[str]:
    return [PATHS['train_work'], models_service.get_models_dir()]



# ---------------------------------------------------------------------------
# Service-layer wrappers for the training blueprint.
#
# The blueprint must not reach past this layer into train_jobs_repo /
# training_splits_repo / read_json_file directly (layering contract). These
# thin delegators preserve the exact behavior and response shapes the route
# handlers and characterization tests depend on.
# ---------------------------------------------------------------------------

def list_train_jobs() -> list[dict]:
    """All train-job records (unsorted; callers sort as needed)."""
    return read_train_jobs()


def get_train_job(job_id: str) -> dict | None:
    """Return the job with ``id == job_id`` or ``None``."""
    return next((x for x in read_train_jobs() if x.get("id") == job_id), None)


def update_train_job(job: dict) -> None:
    """Upsert a single train-job record."""
    upsert_train_job(job)


def delete_split_profile(profile_id: str = "default") -> None:
    """Remove a persisted split profile. No-op if the profile is absent."""
    with TRAINING_SPLITS_LOCK:
        profiles = read_json_file(PATHS['training_splits'], {})
        profiles.pop(profile_id, None)
        write_json_file(PATHS['training_splits'], profiles)



def recover_orphaned_jobs() -> int:
    """Mark jobs left in ``status == 'running'`` as ``failed`` on startup.

    A crash mid-training leaves a job stuck in ``running`` forever. Called once
    at app startup (wired by the factory). Idempotent: only ``running`` jobs are
    touched - ``queued``/``completed``/``failed`` are left alone. Returns the
    number of jobs recovered. Never raises on a missing/empty store.
    """
    try:
        jobs = read_train_jobs()
    except Exception:
        return 0
    if not jobs:
        return 0
    recovered = 0
    changed = False
    for job in jobs:
        if job.get("status") == "running":
            job["status"] = "failed"
            job["message"] = "进程重启时中断"
            job["updated_at"] = now_iso()
            recovered += 1
            changed = True
    if changed:
        write_train_jobs(jobs)
    return recovered
