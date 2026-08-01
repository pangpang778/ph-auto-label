"""Evaluations JSON store (static/annotations/evaluations.json) + lock.

Guarded by a cross-process ``filelock`` (``evaluations.json.lock``).
:func:`update_evaluations` is the atomic read-modify-write entry;
:func:`upsert_evaluation` is rewritten on top of it.

Mirrors :mod:`app.repositories.train_jobs_repo` in structure. The store is a
JSON array of evaluation records (see module docstring of the service layer for
the record shape); this module only persists and retrieves them.
"""
import filelock

from app.common.config import PATHS
from app.common.json_store import read_json_file, write_json_file
from app.common.utils import now_iso

_EVALUATIONS_LOCK_PATH = PATHS['evaluations'] + '.lock'


def read_evaluations() -> list[dict]:
    with filelock.FileLock(_EVALUATIONS_LOCK_PATH, timeout=10):
        return read_json_file(PATHS['evaluations'], [])


def write_evaluations(records: list[dict]) -> None:
    """Overwrite evaluations.json under the cross-process lock."""
    with filelock.FileLock(_EVALUATIONS_LOCK_PATH, timeout=10):
        write_json_file(PATHS['evaluations'], records)


def update_evaluations(mutator, *, timeout=10):
    """Atomically read-modify-write evaluations.json under the file lock.

    ``mutator(current: list) -> (new_data, result)`` where ``new_data`` is the
    list to persist (or ``None`` to skip the write) and ``result`` is any
    value returned to the caller.
    """
    lock = filelock.FileLock(_EVALUATIONS_LOCK_PATH, timeout=timeout)
    with lock:
        current = read_json_file(PATHS['evaluations'], [])
        new_data, result = mutator(current)
        if new_data is not None:
            write_json_file(PATHS['evaluations'], new_data)
        return result


def list_evaluations(model_id: str | None = None) -> list[dict]:
    """Return all evaluation records, newest first by ``started_at``.

    Optionally filtered by ``model_id``. Never raises.
    """
    try:
        records = read_evaluations()
    except Exception:  # noqa: BLE001 - read_evaluations must never raise here
        return []
    if model_id is not None:
        records = [r for r in records if r.get("model_id") == model_id]
    records.sort(
        key=lambda r: r.get("started_at") or "",
        reverse=True,
    )
    return records


def get_evaluation(record_id: str) -> dict | None:
    """Return the evaluation record with ``id == record_id`` or ``None``."""
    try:
        for record in read_evaluations():
            if record.get("id") == record_id:
                return record
    except Exception:  # noqa: BLE001 - lookup must never raise
        pass
    return None


def upsert_evaluation(record: dict) -> None:
    """Insert or replace an evaluation by id, atomically via update_evaluations."""
    def _mutator(records):
        for i, old in enumerate(records):
            if old.get("id") == record.get("id"):
                records[i] = record
                return records, None
        records.append(record)
        return records, None
    update_evaluations(_mutator)


def append_evaluation(record: dict) -> None:
    """Alias for :func:`upsert_evaluation` (insert)."""
    upsert_evaluation(record)


def find_running_evaluation() -> dict | None:
    """Return the first record with ``status == "running"`` or ``None``.

    Used to enforce the evaluation mutex (only one running eval at a time).
    """
    try:
        for record in read_evaluations():
            if record.get("status") == "running":
                return record
    except Exception:  # noqa: BLE001 - lookup must never raise
        pass
    return None
