"""Evaluation domain service.

Business logic for evaluation runs: the eval mutex (atomic check-and-insert
mirroring :func:`training_service.insert_train_job_if_idle`), comparison
across records, and export to JSON/CSV. Persistence goes through
:mod:`app.repositories.evaluation_repo`; the background run lives in
:mod:`app.services.evaluation_job_runner` and is launched as a daemon
thread (Flask-context-free).

Closed-world contract: cross-domain reads (training-jobs to check the train
mutex, model-registry to resolve model_id -> name/path) go through their
service/repository modules - never past the layering boundary.
"""
import csv
import io
import json
import os
import threading
import uuid

from app.common.config import PATHS
from app.common.utils import now_iso
from app.repositories import evaluation_repo
from app.repositories.train_jobs_repo import read_train_jobs
from app.services import models_service
from app.services import evaluation_job_runner


class EvaluationBusyError(Exception):
    """Raised by :func:`start_evaluation` when training or an eval is running."""
    pass


def start_evaluation(model_id: str) -> dict:
    """Start an evaluation run for ``model_id``.

    Atomic mutex check-and-insert (mirrors
    :func:`training_service.insert_train_job_if_idle`): under one
    ``update_evaluations`` lock acquisition, refuse if any train job is
    ``running`` or any eval record is ``running``; otherwise resolve the
    model, build the queued record, append it, and spawn the background
    runner. Returns the new record.

    Raises:
        EvaluationBusyError: a training job is running, or an eval is
            already running.
        ValueError: ``model_id`` is not in the model registry.
    """
    # Resolve model name/path BEFORE the lock (read-only registry read; the
    # lock guards the eval store only). Raises ValueError -> 404 mapping.
    model_name, model_path = _resolve_model(model_id)

    record = {
        "id": f"eval_{uuid.uuid4().hex[:10]}",
        "job_id": f"evaljob_{uuid.uuid4().hex[:10]}",
        "model_id": model_id,
        "model_name": model_name,
        "status": "queued",
        "progress": 0,
        "started_at": now_iso(),
        "val": {},
        "test": {},
        "run_meta": {"imgsz": 640, "base_model": model_path},
        "error": "",
    }

    # Atomic check-and-insert. The mutator re-reads train_jobs + the current
    # eval records under the eval-store lock so two concurrent POSTs cannot
    # both observe "idle" and each launch a runner.
    def _mutator(records):
        # (a) training mutex.
        try:
            jobs = read_train_jobs()
        except Exception:  # noqa: BLE001 - treat unreadable store as empty
            jobs = []
        if any(j.get("status") == "running" for j in jobs):
            return None, ("training", None)
        # (b) eval mutex.
        if any(r.get("status") == "running" for r in records):
            return None, ("eval", None)
        # (c) idle -> insert.
        records.append(record)
        return records, ("ok", record)

    outcome = evaluation_repo.update_evaluations(_mutator)
    kind, _ = outcome if isinstance(outcome, tuple) else (None, None)

    if kind == "training":
        raise EvaluationBusyError("训练任务运行中，暂无法评估")
    if kind == "eval":
        raise EvaluationBusyError("已有评估任务在运行")
    # kind == "ok": record already persisted in the mutator.

    # Spawn the background runner (daemon so it never blocks process exit).
    threading.Thread(
        target=evaluation_job_runner.run_evaluation_job,
        args=(record,),
        daemon=True,
    ).start()
    return record


def _resolve_model(model_id: str) -> tuple[str, str]:
    """Resolve ``model_id`` -> (model_name, model_path) via the registry.

    Registry records carry the filename under ``name`` (and legacy ``model_name``)
    and the weights path under ``path`` (and legacy ``model_path``). Raises
    ``ValueError("model not found")`` if absent.
    """
    if not model_id:
        raise ValueError("model not found")
    try:
        registry = models_service.read_model_registry()
    except Exception as exc:  # noqa: BLE001 - registry read failure is fatal here
        raise ValueError("model not found") from exc
    for entry in registry:
        if entry.get("id") == model_id:
            name = entry.get("name") or entry.get("model_name") or ""
            path = entry.get("path") or entry.get("model_path") or ""
            return name, path
    raise ValueError("model not found")


def get_evaluation(record_id: str) -> dict | None:
    """Return the evaluation record with ``id == record_id`` or ``None``."""
    return evaluation_repo.get_evaluation(record_id)


def list_evaluations(model_id: str | None = None) -> list[dict]:
    """All evaluation records (newest first), optionally filtered by model_id."""
    return evaluation_repo.list_evaluations(model_id)


def build_comparison(record_ids: list[str]) -> dict:
    """Compare records and pick the best per metric (from ``val`` metrics).

    Returns ``{"records": [...], "best": {"map50": id, ...}}``. ``best``
    selects the record id with the max of map50/map50_95/precision/recall/f1
    and the min of speed_ms. If no records resolve, every best value is
    ``None``.
    """
    records = []
    for rid in record_ids or []:
        rec = get_evaluation(rid)
        if rec is not None:
            records.append(rec)

    best = {key: None for key in ("map50", "map50_95", "precision", "recall", "f1", "speed_ms")}
    if not records:
        return {"records": records, "best": best}

    def _val_metric(rec, key):
        try:
            return float(rec.get("val", {}).get(key, 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    for key in ("map50", "map50_95", "precision", "recall", "f1"):
        best[key] = max(records, key=lambda r: _val_metric(r, key)).get("id")
    # speed_ms: lower is better.
    best["speed_ms"] = min(records, key=lambda r: _val_metric(r, "speed_ms")).get("id")

    return {"records": records, "best": best}


def export_evaluation(record_ids: list[str], fmt: str) -> tuple[bytes, str, str]:
    """Export evaluation records as JSON or CSV.

    Returns ``(payload_bytes, mimetype, filename)``. Unknown ``fmt`` raises
    ``ValueError``. Missing record ids are skipped.
    """
    records = []
    for rid in record_ids or []:
        rec = get_evaluation(rid)
        if rec is not None:
            records.append(rec)
    count = len(records)

    if fmt == "json":
        payload = records if count > 1 else (records[0] if count else {})
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        return body, "application/json", f"evaluations_{count}.json"

    if fmt == "csv":
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow([
            "id", "model_name", "dataset", "map50", "map50_95",
            "precision", "recall", "f1", "speed_ms", "fps", "started_at",
        ])
        for rec in records:
            for split in ("val", "test"):
                m = rec.get(split, {}) or {}
                writer.writerow([
                    rec.get("id", ""),
                    rec.get("model_name", ""),
                    split,
                    m.get("map50", ""),
                    m.get("map50_95", ""),
                    m.get("precision", ""),
                    m.get("recall", ""),
                    m.get("f1", ""),
                    m.get("speed_ms", ""),
                    m.get("fps", ""),
                    rec.get("started_at", ""),
                ])
        body = out.getvalue().encode("utf-8")
        return body, "text/csv", f"evaluations_{count}.csv"

    raise ValueError(f"unsupported export format: {fmt}")
