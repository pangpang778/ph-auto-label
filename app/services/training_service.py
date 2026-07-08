"""Training domain service.

Business logic for training jobs, split config, dataset building, and the
background run_training_job. Flask-context-free (runs in a background thread);
paths resolve via PATHS. Cross-domain calls into the models domain go through
models_service (closed-world contract, api-freeze.md §2).
"""
import csv
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
from app.repositories.annotation_repo import read_annotations, read_classes
from app.repositories.train_jobs_repo import TRAIN_JOBS_LOCK, read_train_jobs, upsert_train_job, write_train_jobs
from app.repositories.training_splits_repo import TRAINING_SPLITS_LOCK, load_split_profile, read_training_splits, write_training_splits
from app.services import models_service
from training_artifacts import resolve_artifact


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
        test_set = annotated[n_train + n_val:] or annotated[:1]
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
    return {
        "train": shuffled[:n_train],
        "val": shuffled[n_train:n_train + n_val],
        "test": shuffled[n_train + n_val:] or shuffled[:1],
    }



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



def append_train_log(job: dict, message: str) -> None:
    log_path = job.get("log_path")
    if not log_path:
        return
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{now_iso()}] {message}\n")
    job["log_tail"] = read_log_tail(log_path, 50)



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
            with Image.open(src) as img:
                width, height = img.size

            lines = []
            for ann in annotations.get(image_name, []):
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



def _extract_metrics_from_results_csv(results_csv: str) -> dict:
    if not os.path.exists(results_csv):
        return {}
    metrics = {}
    try:
        with open(results_csv, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            return {}
        row = rows[-1]
        for key, value in row.items():
            try:
                metrics[key.strip()] = float(value)
            except Exception:
                continue
    except Exception:
        return {}
    return metrics



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



def run_training_job(job_id: str, root_path: str = "") -> None:
    jobs = read_train_jobs()
    job = next((x for x in jobs if x.get("id") == job_id), None)
    if not job:
        return
    job_dir = os.path.join(PATHS['train_work'], job_id)
    os.makedirs(job_dir, exist_ok=True)
    job["log_path"] = os.path.join(job_dir, "train.log")
    job["status"] = "running"
    job["progress"] = 5
    job["message"] = "Preparing training dataset..."
    job["epoch"] = 0
    job["total_epochs"] = int(job.get("epochs", 50))
    job["updated_at"] = now_iso()
    append_train_log(job, "Preparing training dataset...")
    upsert_train_job(job)

    try:
        dataset = build_yolo_training_dataset(job_dir, job.get("split_config"))
        job["dataset_dir"] = dataset["dataset_root"]
        job["annotated_images"] = dataset["annotated_images"]
        job["total_images"] = dataset["total_images"]
        job["candidate_images"] = dataset["candidate_images"]
        job["split_counts"] = dataset["split_counts"]
        job["split_config"] = dataset["split_config"]
        job["progress"] = 20
        job["message"] = "Dataset prepared. Starting model training..."
        job["updated_at"] = now_iso()
        append_train_log(job, f"Dataset prepared with split counts: {dataset['split_counts']}")
        upsert_train_job(job)

        from ultralytics import YOLO

        base_model = job.get("base_model") or "yolo11n.pt"
        job["resolved_base_model"] = base_model
        train_project = os.path.join(job_dir, "runs")
        model = YOLO(base_model)
        job["progress"] = 45
        job["message"] = "Training in progress..."
        job["updated_at"] = now_iso()
        append_train_log(job, f"Training started: base_model={base_model}, device={job.get('device', 'cpu')}, epochs={job.get('epochs', 50)}")
        upsert_train_job(job)

        persist_state = {"last_time": 0.0, "last_epoch": 0}
        total_epochs = int(job.get("epochs", 50))
        persist_every = max(1, total_epochs // 50)

        def on_train_epoch_end(trainer):
            epoch = int(getattr(trainer, "epoch", 0) or 0) + 1
            job["epoch"] = min(epoch, total_epochs)
            job["total_epochs"] = total_epochs
            job["progress"] = min(95, 25 + int((job["epoch"] / max(total_epochs, 1)) * 70))
            job["message"] = f"Training epoch {job['epoch']}/{total_epochs}"
            append_train_log(job, job["message"])
            now = time.time()
            if epoch % persist_every == 0 or now - persist_state["last_time"] >= 5:
                job["updated_at"] = now_iso()
                upsert_train_job(job)
                persist_state["last_time"] = now
                persist_state["last_epoch"] = epoch

        try:
            model.add_callback("on_train_epoch_end", on_train_epoch_end)
        except Exception as callback_exc:
            append_train_log(job, f"Epoch callback unavailable: {callback_exc}")

        model.train(
            data=dataset["data_yaml"],
            epochs=int(job.get("epochs", 50)),
            imgsz=int(job.get("imgsz", 640)),
            batch=int(job.get("batch", 8)),
            device=job.get("device", "cpu"),
            workers=0,
            project=train_project,
            name="detector",
            exist_ok=True,
            patience=0,
            pretrained=True,
            verbose=True,
            cache=False,
            amp=False,
        )
        run_dir = os.path.join(train_project, "detector")
        best_path = os.path.join(run_dir, "weights", "best.pt")
        last_path = os.path.join(run_dir, "weights", "last.pt")
        if os.path.exists(best_path):
            trained_model = best_path
        elif os.path.exists(last_path):
            trained_model = last_path
        else:
            raise FileNotFoundError("Training finished but no model artifact found in weights/")

        version = models_service.next_model_version(job.get("mode", "incremental"))
        model_filename = f"{version}.pt"
        model_dst = os.path.join(models_service.get_models_dir(), model_filename)
        shutil.copy2(trained_model, model_dst)

        results_csv = os.path.join(run_dir, "results.csv")
        results_png = os.path.join(run_dir, "results.png")
        metrics = _extract_metrics_from_results_csv(results_csv)
        model_id = f"model_{uuid.uuid4().hex[:10]}"
        model_record = {
            "id": model_id,
            "version": version,
            "name": model_filename,
            "path": model_dst,
            "parent_model": job.get("base_model", ""),
            "mode": job.get("mode", "incremental"),
            "metrics": metrics,
            "created_at": now_iso(),
            "job_id": job_id,
            "status": "candidate",
            "results_csv": results_csv if os.path.exists(results_csv) else "",
            "results_png": results_png if os.path.exists(results_png) else "",
            "weights_path": trained_model,
            "run_dir": run_dir,
            "split_counts": job.get("split_counts", {}),
        }
        models_service.append_model_registry_record(model_record)

        # auto activate newest successful model
        models_service.set_active_model(model_id=model_id, model_name=model_filename, model_path=model_dst)

        job["status"] = "completed"
        job["progress"] = 100
        job["message"] = f"Training completed. Model {model_filename} is ready."
        job["artifact_path"] = model_dst
        job["weights_path"] = trained_model
        job["metrics"] = metrics
        job["model_id"] = model_id
        job["version"] = version
        job["run_dir"] = run_dir
        job["results_csv"] = results_csv if os.path.exists(results_csv) else ""
        job["results_png"] = results_png if os.path.exists(results_png) else ""
        job["epoch"] = int(job.get("epochs", 50))
        job["total_epochs"] = int(job.get("epochs", 50))
        job["updated_at"] = now_iso()
        append_train_log(job, f"Training completed. Model {model_filename} is ready.")
        upsert_train_job(job)

    except Exception as exc:
        job["status"] = "failed"
        job["progress"] = 100
        job["message"] = f"Training failed: {str(exc)}"
        job["error"] = traceback.format_exc()
        job["updated_at"] = now_iso()
        append_train_log(job, job["message"])
        upsert_train_job(job)



def _artifact_allowed_roots() -> list[str]:
    return [PATHS['train_work'], models_service.get_models_dir()]
