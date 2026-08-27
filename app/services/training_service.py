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
from app.common.path_safety import PathSafetyError, resolve_contained_path
from app.common.utils import now_iso
from app.services.annotation_service import read_annotations, read_classes
from app.repositories.train_jobs_repo import read_train_jobs, recover_orphaned_jobs_atomic, update_train_jobs, upsert_train_job
from app.repositories.training_splits_repo import (
    delete_split_profile_atomic,
    load_split_profile,
    update_split_profile,
)
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
    # Deterministic class ordering: sort by name so the class<->id mapping is
    # stable across rebuilds regardless of classes.json array order. Without
    # this, incremental training could remap ids and silently corrupt the
    # learned head. MEDIUM (class-id drift) fix.
    class_names = sorted(set(c.get('name') for c in classes if c.get('name')))
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
        # Requested assignments are treated as manually pinned items - they are
        # honored as-is, then the *leftover* (un-pinned) candidates are
        # deterministically shuffled and distributed across all three splits
        # by ratio. This lets newly added images reach val/test instead of the
        # old behavior where every leftover was dumped into train (val/test
        # froze once an assignment was persisted). C1 fix.
        assigned = {split: [name for name in requested.get(split, []) if name in candidate_set] for split in ("train", "val", "test")}
        used = set(assigned["train"] + assigned["val"] + assigned["test"])
        leftovers = [name for name in candidates if name not in used]
        random.Random(42).shuffle(leftovers)
        n = len(leftovers)
        n_train = round(n * config["train_ratio"])
        n_val = round(n * config["val_ratio"])
        assigned["train"].extend(leftovers[:n_train])
        assigned["val"].extend(leftovers[n_train:n_train + n_val])
        assigned["test"].extend(leftovers[n_train + n_val:])
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

    def _mutator(profiles):
        profiles[profile_id] = summary
        return profiles, summary

    update_split_profile(profile_id, _mutator)
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
    _write_data_yaml(data_yaml, dataset_root, class_names)

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


def _write_data_yaml(data_yaml: str, dataset_root: str, class_names: list[str]) -> None:
    """Write data.yaml via yaml.safe_dump (MEDIUM: avoid f-string YAML)."""
    import yaml
    dataset_root_abs = os.path.abspath(dataset_root).replace("\\", "/")
    payload = {
        "path": dataset_root_abs,
        "train": "train/images",
        "val": "val/images",
        "test": "test/images",
        "nc": len(class_names),
        "names": class_names,
    }
    with open(data_yaml, "w", encoding="utf-8") as f:
        # default_flow_style=False emits block style; sort_keys=False keeps
        # a stable, human-readable key order (Python dict insertion order).
        yaml.safe_dump(payload, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _check_incremental_class_order(base_model: str, class_names: list[str]) -> str | None:
    """Best-effort: warn if an incremental base model's class names disagree.

    Returns a warning string when the base model is a real weights file whose
    loaded ``names`` mapping is incompatible (different class set/order) with
    the current ``class_names``. Returns ``None`` when there is nothing to
    flag, or when reading the model fails (never blocks training).
    """
    if not base_model or not os.path.isfile(base_model):
        return None
    try:
        from ultralytics import YOLO
        names = YOLO(base_model).names
    except Exception:
        # Can't read names (e.g. base is a pretrained .pt without our head) -
        # not actionable, stay silent.
        return None
    if not isinstance(names, dict):
        return None
    base_names = [str(v) for v in names.values()]
    if sorted(base_names) != sorted(class_names):
        return (
            f"增量训练 base 模型类别集 {base_names} 与当前类别集 {class_names} 不一致，"
            "可能导致检测头重置或类别错位；建议保持类别集稳定。"
        )
    if base_names != class_names:
        return (
            f"增量训练 base 模型类别顺序 {base_names} 与当前 {class_names} 顺序不同，"
            "已按名称确定性排序对齐当前数据，请确认与历史训练一致。"
        )
    return None



def build_train_job(payload, mode, readiness, active):
    """Resolve split_config + base_model and build the queued training job dict.

    Raises ``ValueError`` on invalid split_config (handler maps to 400).
    """
    task_type = str(payload.get("task_type") or "detect").strip().lower()
    if task_type == "pseudo":
        return _build_pseudo_train_job(payload)
    if task_type == "depth":
        return _build_depth_train_job(payload)
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
    epochs = _validate_int_param(payload.get("epochs", 30), "epochs", 1, 1000)
    imgsz = _validate_imgsz(payload.get("imgsz", 640))
    batch = _validate_int_param(payload.get("batch", 8), "batch", 1, 512)
    job_id = f"train_{uuid.uuid4().hex[:10]}"
    job = {
        "id": job_id,
        "mode": mode,
        "status": "queued",
        "progress": 0,
        "message": "Queued",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "base_model": base_model,
        "epochs": epochs,
        "imgsz": imgsz,
        "batch": batch,
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
    # Best-effort incremental class-order check (MEDIUM). Non-fatal: only adds
    # a warning field; never blocks the job from queueing.
    if mode == "incremental":
        try:
            classes = read_classes()
            names = sorted(set(c.get('name') for c in classes if c.get('name')))
            warning = _check_incremental_class_order(base_model, names)
            if warning:
                job["class_order_warning"] = warning
        except Exception:
            pass
    return job


def _build_pseudo_train_job(payload):
    """伪标签生成任务（工单 05）：选视频 → MoGe 打标 → frames+npy 数据集。"""
    from plugins.video_inference import resolve_video_path
    raw_videos = payload.get("videos")
    if not isinstance(raw_videos, list) or not [v for v in raw_videos if str(v).strip()]:
        raise ValueError("伪标签生成至少需要一个视频")
    videos = []
    for name in raw_videos:
        name = str(name).strip()
        if not name:
            continue
        path = resolve_video_path(name)
        if not path:
            raise ValueError(f"视频不存在: {name}")
        videos.append({"name": name, "path": path})
    interval = float(payload.get("interval_s", 0.2))
    if not (0.05 <= interval <= 2.0):
        raise ValueError("interval_s 必须在 0.05-2.0 秒之间")
    job_id = f"train_{uuid.uuid4().hex[:10]}"
    return {
        "id": job_id,
        "task_type": "pseudo",
        "mode": "incremental",
        "status": "queued",
        "progress": 0,
        "message": "Queued",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "videos": videos,
        "interval_s": interval,
        "epochs": 0,
        "total_epochs": 0,
        "log_path": os.path.join(PATHS['train_work'], job_id, "train.log"),
        "log_tail": "",
    }


def _build_depth_train_job(payload):
    """深度蒸馏训练任务（工单 05）：消费伪标签数据集目录。"""
    dataset_dir = str(payload.get("dataset_dir") or "").strip()
    if not dataset_dir:
        raise ValueError("数据集目录不存在: (空)")
    try:
        # 客户端只能指向 train_work 下的伪标签产物（防任意服务器路径读取）
        dataset_dir = resolve_contained_path(PATHS['train_work'], dataset_dir)
    except PathSafetyError:
        raise ValueError(f"数据集目录必须在 train_work 下: {dataset_dir}")
    if not os.path.isdir(dataset_dir):
        raise ValueError(f"数据集目录不存在: {dataset_dir}")
    manifest = os.path.join(dataset_dir, "manifest.json")
    if not os.path.isfile(manifest):
        raise ValueError("所选目录缺少 manifest.json，请先完成伪标签生成任务")
    epochs = _validate_int_param(payload.get("epochs", 50), "epochs", 1, 500)
    batch = _validate_int_param(payload.get("batch", 32), "batch", 1, 64)
    job_id = f"train_{uuid.uuid4().hex[:10]}"
    return {
        "id": job_id,
        "task_type": "depth",
        "mode": "incremental",
        "status": "queued",
        "progress": 0,
        "message": "Queued",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "dataset_dir": dataset_dir,
        "epochs": epochs,
        "imgsz": 384,
        "batch": batch,
        "device": resolve_training_device(payload.get("device", "auto")),
        "split_counts": {},
        "total_epochs": epochs,
        "log_path": os.path.join(PATHS['train_work'], job_id, "train.log"),
        "log_tail": "",
    }


def _validate_int_param(raw, name: str, lo: int, hi: int) -> int:
    """Coerce ``raw`` to int and range-check it (H13). Raises ValueError -> 400."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} 参数必须为整数")
    if value < lo or value > hi:
        raise ValueError(f"{name} 必须在 {lo}-{hi}")
    return value


def _validate_imgsz(raw) -> int:
    """YOLO requires imgsz to be a positive multiple of 32 (H13)."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError("imgsz 参数必须为整数")
    if value <= 0 or value % 32 != 0:
        raise ValueError("imgsz 必须是 32 的正整数倍")
    return value


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


def insert_train_job_if_idle(job: dict) -> bool:
    """Atomic check-and-insert (H5 TOCTOU fix).

    Inserts ``job`` only if no existing job has ``status == "running"``.
    Returns True if inserted, False if a running job blocks. The check and
    insert happen under one ``update_train_jobs`` lock acquisition so two
    concurrent POST /api/train/start cannot both observe "no running job"
    and each launch a training thread. Only "running" is blocked (not
    "queued") so the back-to-back test flow with a no-op worker still works.
    """
    def _mutator(jobs):
        if any(j.get("status") == "running" for j in jobs):
            return None, False
        jobs.append(job)
        return jobs, True
    return update_train_jobs(_mutator)


def delete_split_profile(profile_id: str = "default") -> None:
    """Remove a persisted split profile. No-op if the profile is absent."""
    delete_split_profile_atomic(profile_id)



def recover_orphaned_jobs() -> int:
    """Mark jobs left ``running`` or ``queued`` as ``failed`` on startup.

    A crash mid-training (or mid-queue) leaves a job stuck forever. Called once
    at app startup (wired by the factory). Delegates to the atomic
    read-modify-write in the repo so concurrent restarts cannot double-recover
    or clobber each other. Returns the number of jobs recovered; never raises.
    """
    return recover_orphaned_jobs_atomic()
