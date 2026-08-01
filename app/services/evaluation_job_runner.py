"""Evaluation job runner.

Owns the background ``run_evaluation_job`` workflow: resolve model file ->
locate/build a data.yaml with val+test splits -> run YOLO ``val`` on both
splits -> persist metrics/progress. Runs in a background thread with NO
Flask request context: paths resolve via ``PATHS`` (mirrors
:mod:`app.services.training_job_runner`). The thread-launch contract is
``run_evaluation_job(record)``.

Metric extraction is defensive: the ultralytics result-object API varies by
version, so EVERY field is wrapped in try/except with a safe default. The
runner must NEVER crash on a metrics-extraction error, and never raise out
of the thread entrypoint (failures are persisted as ``status="failed"``).
"""
import os

from app.common.config import PATHS
from app.common.utils import now_iso
from app.repositories import evaluation_repo
from app.repositories.train_jobs_repo import read_train_jobs
from app.services import models_service


def run_evaluation_job(record: dict) -> None:
    """Background evaluation workflow. ``record`` is the queued eval record.

    Flask-context-free (runs in a daemon thread): uses ``PATHS`` directly,
    never ``current_app``. Never raises - any exception is persisted as a
    ``failed`` status with ``error`` set.
    """
    try:
        _run(record)
    except Exception as exc:  # noqa: BLE001 - runner must never raise out
        _mark_failed(record, exc)


def _run(record: dict) -> None:
    # 1. Mark running.
    record["status"] = "running"
    record["progress"] = 10
    record["started_at"] = record.get("started_at") or now_iso()
    evaluation_repo.upsert_evaluation(record)

    # 2. Resolve the model file path from the registry.
    model_path = _resolve_model_path(record.get("model_id"))
    if not model_path:
        record["status"] = "failed"
        record["error"] = "model not found"
        record["completed_at"] = now_iso()
        evaluation_repo.upsert_evaluation(record)
        return

    # 3. Locate (or build) a data.yaml with val+test splits.
    data_yaml = _resolve_data_yaml(record)
    if not data_yaml:
        record["status"] = "failed"
        record["error"] = "no dataset with val+test splits available"
        record["completed_at"] = now_iso()
        evaluation_repo.upsert_evaluation(record)
        return

    # 4. Run YOLO val on both splits.
    from ultralytics import YOLO

    imgsz = int(record.get("run_meta", {}).get("imgsz", 640) or 640)
    model = YOLO(model_path)

    # val split.
    try:
        result = model.val(
            data=data_yaml, split="val", imgsz=imgsz, device="auto", verbose=False
        )
        record["val"] = _extract_metrics(result)
    except Exception as exc:  # noqa: BLE001 - extraction must not crash runner
        record["val"] = {"error": str(exc)}
    record["progress"] = 50
    evaluation_repo.upsert_evaluation(record)

    # test split.
    try:
        result = model.val(
            data=data_yaml, split="test", imgsz=imgsz, device="auto", verbose=False
        )
        record["test"] = _extract_metrics(result)
    except Exception as exc:  # noqa: BLE001 - extraction must not crash runner
        record["test"] = {"error": str(exc)}
    record["progress"] = 90
    evaluation_repo.upsert_evaluation(record)

    # 5. Complete.
    record["status"] = "completed"
    record["progress"] = 100
    record["completed_at"] = now_iso()
    evaluation_repo.upsert_evaluation(record)


def _resolve_model_path(model_id: str) -> str | None:
    """Resolve ``model_id`` -> weights file path via the model registry.

    Returns ``None`` if the model is not found or its path is missing.
    Registry records carry the path under ``path``; some legacy records may
    use ``model_path``.
    """
    if not model_id:
        return None
    try:
        registry = models_service.read_model_registry()
    except Exception:  # noqa: BLE001 - registry read must not crash runner
        return None
    for entry in registry:
        if entry.get("id") == model_id:
            return entry.get("path") or entry.get("model_path") or None
    return None


def _resolve_data_yaml(record: dict) -> str | None:
    """Locate a data.yaml with val+test splits, else build one fresh.

    First scans ``read_train_jobs()`` for the latest job whose ``run_dir``
    contains a ``data.yaml``; if found, reuses it. Otherwise builds a new
    dataset under ``PATHS['train_work']/evaldataset_<record_id>`` via
    :func:`training_service.build_yolo_training_dataset` (which writes
    ``dataset/data.yaml`` with train/val/test). Returns the data_yaml path
    or ``None`` if neither succeeds.
    """
    candidate = _find_existing_data_yaml()
    if candidate:
        return candidate

    # Build a fresh dataset (needs annotated images; may raise).
    try:
        from app.services.training_service import build_yolo_training_dataset

        work_dir = os.path.join(PATHS["train_work"], f"evaldataset_{record['id']}")
        dataset = build_yolo_training_dataset(work_dir)
        return dataset.get("data_yaml")
    except Exception:  # noqa: BLE001 - dataset build failure -> caller fails job
        return None


def _find_existing_data_yaml() -> str | None:
    """Return the data.yaml from the latest train job with a usable run_dir."""
    try:
        jobs = read_train_jobs()
    except Exception:  # noqa: BLE001 - read must not crash runner
        return None
    # Newest first by created_at (matches train_jobs_repo sort convention is
    # not guaranteed; sort defensively here).
    jobs_sorted = sorted(
        jobs, key=lambda j: j.get("created_at") or "", reverse=True
    )
    for job in jobs_sorted:
        run_dir = job.get("run_dir") or ""
        if not run_dir:
            continue
        data_yaml = os.path.join(run_dir, "data.yaml")
        if os.path.isfile(data_yaml):
            return data_yaml
    return None


def _mark_failed(record: dict, exc: BaseException) -> None:
    """Persist a failed terminal status. Never raises."""
    try:
        record["status"] = "failed"
        record["error"] = str(exc)
        record["completed_at"] = now_iso()
        evaluation_repo.upsert_evaluation(record)
    except Exception:  # noqa: BLE001 - failure-path persistence must not raise
        pass


def _extract_metrics(result) -> dict:
    """Extract the METRICS schema from an ultralytics val result.

    Every field is wrapped in try/except with a safe default because the
    result-object API varies across ultralytics versions. NEVER raises.
    """
    metrics: dict = {
        "map50": 0.0,
        "map50_95": 0.0,
        "per_class": [],
        "precision": 0.0,
        "recall": 0.0,
        "f1": 0.0,
        "speed_ms": 0.0,
        "fps": 0.0,
        "pr_curve": [],
        "confusion_matrix": {"matrix": [], "classes": []},
    }

    def _box():
        return getattr(result, "box", None)

    box = _box()

    # map50
    try:
        metrics["map50"] = float(getattr(box, "map50", 0.0) or 0.0)
    except Exception:  # noqa: BLE001
        metrics["map50"] = 0.0
    # map50_95 (ultralytics stores mAP[.5:.95] on result.box.map)
    try:
        metrics["map50_95"] = float(getattr(box, "map", 0.0) or 0.0)
    except Exception:  # noqa: BLE001
        metrics["map50_95"] = 0.0
    # precision (result.box.mp)
    try:
        metrics["precision"] = float(getattr(box, "mp", 0.0) or 0.0)
    except Exception:  # noqa: BLE001
        metrics["precision"] = 0.0
    # recall (result.box.mr)
    try:
        metrics["recall"] = float(getattr(box, "mr", 0.0) or 0.0)
    except Exception:  # noqa: BLE001
        metrics["recall"] = 0.0
    # f1
    try:
        p = metrics["precision"]
        r = metrics["recall"]
        metrics["f1"] = (2 * p * r / (p + r)) if (p + r) > 0 else 0.0
    except Exception:  # noqa: BLE001
        metrics["f1"] = 0.0
    # speed_ms (result.speed['inference'])
    try:
        speed = getattr(result, "speed", {}) or {}
        metrics["speed_ms"] = float(speed.get("inference", 0.0) or 0.0)
    except Exception:  # noqa: BLE001
        metrics["speed_ms"] = 0.0
    # fps
    try:
        speed_ms = metrics["speed_ms"]
        metrics["fps"] = (1000.0 / speed_ms) if speed_ms > 0 else 0.0
    except Exception:  # noqa: BLE001
        metrics["fps"] = 0.0
    # per_class
    try:
        ap_class_index = list(getattr(box, "ap_class_index", []) or [])
        names = getattr(result, "names", {}) or {}
        maps = list(getattr(box, "maps", []) or [])
        per_class = []
        for i, cls_idx in enumerate(ap_class_index):
            cls_name = (
                names.get(cls_idx) if isinstance(names, dict) else
                (names[cls_idx] if isinstance(names, (list, tuple)) and 0 <= cls_idx < len(names) else str(cls_idx))
            )
            ap_val = float(maps[i]) if i < len(maps) else 0.0
            per_class.append({
                "class": str(cls_name) if cls_name is not None else str(cls_idx),
                "map50": ap_val,
                "map50_95": ap_val,
            })
        metrics["per_class"] = per_class
    except Exception:  # noqa: BLE001
        metrics["per_class"] = []
    # pr_curve (from result.box.p / result.box.r arrays)
    try:
        p_arr = list(getattr(box, "p", []) or [])
        r_arr = list(getattr(box, "r", []) or [])
        pr_curve = []
        n = min(len(p_arr), len(r_arr))
        for i in range(n):
            pr_curve.append([float(r_arr[i]), float(p_arr[i])])
        metrics["pr_curve"] = pr_curve
    except Exception:  # noqa: BLE001
        metrics["pr_curve"] = []
    # confusion_matrix
    try:
        cm = getattr(result, "confusion_matrix", None)
        matrix = []
        if cm is not None:
            cm_matrix = getattr(cm, "matrix", None)
            if cm_matrix is not None:
                # numpy array -> nested python lists
                matrix = cm_matrix.tolist() if hasattr(cm_matrix, "tolist") else [
                    [int(x) for x in row] for row in cm_matrix
                ]
        names = getattr(result, "names", {}) or {}
        classes = (
            list(names.values()) if isinstance(names, dict) else
            [str(x) for x in names]
        )
        metrics["confusion_matrix"] = {"matrix": matrix, "classes": classes}
    except Exception:  # noqa: BLE001
        metrics["confusion_matrix"] = {"matrix": [], "classes": []}

    return metrics
