"""Training job runner.

Owns the background ``run_training_job`` workflow: dataset build -> YOLO train
-> model registration -> job status updates. Runs in a background thread with
NO Flask request context: paths resolve via ``PATHS``; the thread launch
contract is ``run_training_job(job_id, root_path)``. Cross-domain writes go
through ``models_service`` (closed-world contract, .omc/plans/api-freeze.md §2).

The dataset builder + split helpers live in ``training_service`` and are
imported lazily inside the runner to avoid a load-time circular import
(``training_service`` re-exports ``run_training_job`` from this module).
"""
import csv
import os
import shutil
import time
import traceback
import uuid

from app.common.config import PATHS
from app.common.utils import now_iso
from app.repositories.train_jobs_repo import read_train_jobs, upsert_train_job
from app.services import models_service
from training_artifacts import read_log_tail


def append_train_log(job: dict, message: str) -> None:
    log_path = job.get("log_path")
    if not log_path:
        return
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{now_iso()}] {message}\n")
    job["log_tail"] = read_log_tail(log_path, 50)


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


def run_training_job(job_id: str, root_path: str = "") -> None:
    """Background training workflow. ``root_path`` is injected by the route
    handler for the thread-launch contract; path resolution uses ``PATHS``."""
    job = _load_job(job_id)
    if not job:
        return
    job_dir = _prepare_job_dir(job)
    try:
        dataset = _build_dataset_phase(job, job_dir)
        run_dir = _train_model_phase(job, job_dir, dataset)
        model_info = _register_trained_model(job, job_id, run_dir)
        _mark_completed(job, model_info)
    except Exception as exc:
        _mark_failed(job, exc)
        _cleanup_failed_dataset(job, job_dir)


def _cleanup_failed_dataset(job: dict, job_dir: str) -> None:
    """Remove the per-job dataset copy on failure (MEDIUM: artifact cleanup).

    Keeps ``train.log`` and ``runs/`` for post-mortem diagnostics; only the
    (often large) dataset image/label copy under ``job_dir/dataset`` is
    removed. Best-effort: never raises into the failure path.
    """
    try:
        dataset_dir = job.get("dataset_dir") or os.path.join(job_dir, "dataset")
        if dataset_dir and os.path.isdir(dataset_dir):
            shutil.rmtree(dataset_dir, ignore_errors=True)
    except Exception:
        pass


def _load_job(job_id: str) -> dict | None:
    jobs = read_train_jobs()
    return next((x for x in jobs if x.get("id") == job_id), None)


def _prepare_job_dir(job: dict) -> str:
    job_dir = os.path.join(PATHS['train_work'], job["id"])
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
    return job_dir


def _build_dataset_phase(job: dict, job_dir: str) -> dict:
    from app.services.training_service import build_yolo_training_dataset
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
    return dataset


def _train_model_phase(job: dict, job_dir: str, dataset: dict) -> str:
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
    _attach_epoch_callback(model, job)
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
    return os.path.join(train_project, "detector")


def _attach_epoch_callback(model, job: dict) -> None:
    total_epochs = int(job.get("epochs", 50))
    persist_every = max(1, total_epochs // 50)
    persist_state = {"last_time": 0.0, "last_epoch": 0}

    def on_train_epoch_end(trainer):
        epoch = int(getattr(trainer, "epoch", 0) or 0) + 1
        job["epoch"] = min(epoch, total_epochs)
        job["total_epochs"] = total_epochs
        # Clamp to the current progress so the first epoch callback never
        # regresses below the pre-train baseline (45). MEDIUM (progress
        # regression) fix: 25 + int(epoch/total*70) dips to ~39 on epoch 1 of
        # a 5-epoch run, which is below the 45 set in _train_model_phase.
        target = min(95, 25 + int((job["epoch"] / max(total_epochs, 1)) * 70))
        job["progress"] = max(int(job.get("progress", 0) or 0), target)
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


def _resolve_trained_model(run_dir: str) -> str:
    best_path = os.path.join(run_dir, "weights", "best.pt")
    last_path = os.path.join(run_dir, "weights", "last.pt")
    if os.path.exists(best_path):
        return best_path
    if os.path.exists(last_path):
        return last_path
    raise FileNotFoundError("Training finished but no model artifact found in weights/")


def _register_trained_model(job: dict, job_id: str, run_dir: str) -> dict:
    trained_model = _resolve_trained_model(run_dir)
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
    models_service.append_record(model_record)
    models_service.set_active(model_id=model_id, model_name=model_filename, model_path=model_dst)
    return {
        "model_id": model_id,
        "version": version,
        "model_filename": model_filename,
        "model_dst": model_dst,
        "trained_model": trained_model,
        "metrics": metrics,
        "results_csv": results_csv if os.path.exists(results_csv) else "",
        "results_png": results_png if os.path.exists(results_png) else "",
        "run_dir": run_dir,
    }


def _mark_completed(job: dict, model_info: dict) -> None:
    job["status"] = "completed"
    job["progress"] = 100
    job["message"] = f"Training completed. Model {model_info['model_filename']} is ready."
    job["artifact_path"] = model_info["model_dst"]
    job["weights_path"] = model_info["trained_model"]
    job["metrics"] = model_info["metrics"]
    job["model_id"] = model_info["model_id"]
    job["version"] = model_info["version"]
    job["run_dir"] = model_info["run_dir"]
    job["results_csv"] = model_info["results_csv"]
    job["results_png"] = model_info["results_png"]
    job["epoch"] = int(job.get("epochs", 50))
    job["total_epochs"] = int(job.get("epochs", 50))
    job["updated_at"] = now_iso()
    append_train_log(job, job["message"])
    upsert_train_job(job)


def _mark_failed(job: dict, exc: BaseException) -> None:
    job["status"] = "failed"
    # ponytail: 100 is reserved for completed; a failed job keeps its last
    # known progress (capped at 99) so status is the authoritative signal.
    job["progress"] = min(int(job.get("progress", 0) or 0), 99)
    job["message"] = f"Training failed: {str(exc)}"
    job["error"] = traceback.format_exc()
    job["updated_at"] = now_iso()
    append_train_log(job, job["message"])
    upsert_train_job(job)
