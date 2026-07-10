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
from app.repositories.train_jobs_repo import mutate_train_job, read_train_jobs
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


def _persist_job_delta(job_id: str, delta: dict) -> None:
    """Field-delta persist (H4): merge ``delta`` onto the CURRENT on-disk job
    record under the train-jobs lock. The runner keeps an in-memory ``job``
    dict for local reads, but persistence re-reads the current record and
    applies only ``delta`` - so a stale in-memory snapshot (e.g. ``status``
    carried from before crash-recovery) cannot overwrite fields the runner did
    not intend to change. Each checkpoint passes ONLY the fields it owns, so
    ``recover_orphaned_jobs_atomic`` marking a job ``failed`` survives a later
    epoch-callback persist (which does not include ``status``).
    """
    def _mutator(cur):
        return {**cur, **delta}, None
    mutate_train_job(job_id, _mutator)


def _prepare_job_dir(job: dict) -> str:
    job_dir = os.path.join(PATHS['train_work'], job["id"])
    os.makedirs(job_dir, exist_ok=True)
    job["log_path"] = os.path.join(job_dir, "train.log")
    append_train_log(job, "Preparing training dataset...")
    delta = {
        "status": "running",
        "progress": 5,
        "message": "Preparing training dataset...",
        "epoch": 0,
        "total_epochs": int(job.get("epochs", 50)),
        "log_path": job["log_path"],
        "log_tail": job.get("log_tail", ""),
        "updated_at": now_iso(),
    }
    job.update(delta)
    _persist_job_delta(job["id"], delta)
    return job_dir


def _build_dataset_phase(job: dict, job_dir: str) -> dict:
    from app.services.training_service import build_yolo_training_dataset
    dataset = build_yolo_training_dataset(job_dir, job.get("split_config"))
    append_train_log(job, f"Dataset prepared with split counts: {dataset['split_counts']}")
    delta = {
        "dataset_dir": dataset["dataset_root"],
        "annotated_images": dataset["annotated_images"],
        "total_images": dataset["total_images"],
        "candidate_images": dataset["candidate_images"],
        "split_counts": dataset["split_counts"],
        "split_config": dataset["split_config"],
        "progress": 20,
        "message": "Dataset prepared. Starting model training...",
        "log_tail": job.get("log_tail", ""),
        "updated_at": now_iso(),
    }
    job.update(delta)
    _persist_job_delta(job["id"], delta)
    return dataset


def _train_model_phase(job: dict, job_dir: str, dataset: dict) -> str:
    from ultralytics import YOLO
    base_model = job.get("base_model") or "yolo11n.pt"
    job["resolved_base_model"] = base_model
    train_project = os.path.join(job_dir, "runs")
    model = YOLO(base_model)
    append_train_log(job, f"Training started: base_model={base_model}, device={job.get('device', 'cpu')}, epochs={job.get('epochs', 50)}")
    delta = {
        "resolved_base_model": base_model,
        "progress": 45,
        "message": "Training in progress...",
        "log_tail": job.get("log_tail", ""),
        "updated_at": now_iso(),
    }
    job.update(delta)
    _persist_job_delta(job["id"], delta)
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
            # H4: persist ONLY epoch/progress/message/log_tail/updated_at - NOT
            # status - so recover_orphaned_jobs_atomic's "failed" survives a
            # late epoch callback from a stale runner snapshot.
            _persist_job_delta(job["id"], {
                "epoch": job["epoch"],
                "total_epochs": total_epochs,
                "progress": job["progress"],
                "message": job["message"],
                "log_tail": job.get("log_tail", ""),
                "updated_at": now_iso(),
            })
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
    results_csv = os.path.join(run_dir, "results.csv")
    results_png = os.path.join(run_dir, "results.png")
    metrics = _extract_metrics_from_results_csv(results_csv)
    model_id = f"model_{uuid.uuid4().hex[:10]}"
    # H6: copy weights to a temp name FIRST (no lock) so a copy failure leaves
    # no registry record; the atomic register then renames temp -> <version>.pt
    # and appends the record under one lock (version allocated from current
    # registry, so two concurrent completions cannot pick the same version).
    models_dir = models_service.get_models_dir()
    temp_weights_path = os.path.join(models_dir, f".tmp_{model_id}.pt")
    shutil.copy2(trained_model, temp_weights_path)
    base_record = {
        "id": model_id,
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
    try:
        version, model_filename, model_dst = models_service.register_trained_model_record(
            job.get("mode", "incremental"), base_record, temp_weights_path
        )
    except Exception:
        # Atomic registration failed: clean up the temp weights and re-raise so
        # the runner's _mark_failed handles the job (no dangling record).
        try:
            os.remove(temp_weights_path)
        except OSError:
            pass
        raise
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
    append_train_log(job, f"Training completed. Model {model_info['model_filename']} is ready.")
    delta = {
        "status": "completed",
        "progress": 100,
        "message": f"Training completed. Model {model_info['model_filename']} is ready.",
        "artifact_path": model_info["model_dst"],
        "weights_path": model_info["trained_model"],
        "metrics": model_info["metrics"],
        "model_id": model_info["model_id"],
        "version": model_info["version"],
        "run_dir": model_info["run_dir"],
        "results_csv": model_info["results_csv"],
        "results_png": model_info["results_png"],
        "epoch": int(job.get("epochs", 50)),
        "total_epochs": int(job.get("epochs", 50)),
        "log_tail": job.get("log_tail", ""),
        "updated_at": now_iso(),
    }
    job.update(delta)
    _persist_job_delta(job["id"], delta)


def _mark_failed(job: dict, exc: BaseException) -> None:
    append_train_log(job, f"Training failed: {str(exc)}")
    delta = {
        "status": "failed",
        # ponytail: 100 is reserved for completed; a failed job keeps its last
        # known progress (capped at 99) so status is the authoritative signal.
        "progress": min(int(job.get("progress", 0) or 0), 99),
        "message": f"Training failed: {str(exc)}",
        "error": traceback.format_exc(),
        "log_tail": job.get("log_tail", ""),
        "updated_at": now_iso(),
    }
    job.update(delta)
    _persist_job_delta(job["id"], delta)
