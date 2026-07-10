"""Train-jobs JSON store (train_jobs.json) + lock.

Guarded by a cross-process ``filelock`` (``train_jobs.json.lock``).
:func:`update_train_jobs` is the atomic read-modify-write entry;
:func:`upsert_train_job` is rewritten on top of it.
:func:`recover_orphaned_jobs_atomic` marks running/queued jobs as failed on
process restart, atomically and never raising.
"""
import filelock

from app.common.config import PATHS
from app.common.json_store import read_json_file, write_json_file
from app.common.utils import now_iso

_TRAIN_JOBS_LOCK_PATH = PATHS['train_jobs'] + '.lock'

# Kept as a process-local filelock alias for any caller that still imports the
# name (training_service imports it). Prefer update_train_jobs() for RMW.
TRAIN_JOBS_LOCK = filelock.FileLock(_TRAIN_JOBS_LOCK_PATH, timeout=10)

_RECOVERABLE_STATUSES = ("running", "queued")


def read_train_jobs() -> list[dict]:
    with filelock.FileLock(_TRAIN_JOBS_LOCK_PATH, timeout=10):
        return read_json_file(PATHS['train_jobs'], [])


def write_train_jobs(jobs: list[dict]) -> None:
    """Overwrite train_jobs.json under the cross-process lock."""
    with filelock.FileLock(_TRAIN_JOBS_LOCK_PATH, timeout=10):
        write_json_file(PATHS['train_jobs'], jobs)


def update_train_jobs(mutator, *, timeout=10):
    """Atomically read-modify-write train_jobs.json under the file lock.

    ``mutator(current: list) -> (new_data, result)`` where ``new_data`` is the
    list to persist (or ``None`` to skip the write) and ``result`` is any
    value returned to the caller.
    """
    lock = filelock.FileLock(_TRAIN_JOBS_LOCK_PATH, timeout=timeout)
    with lock:
        current = read_json_file(PATHS['train_jobs'], [])
        new_data, result = mutator(current)
        if new_data is not None:
            write_json_file(PATHS['train_jobs'], new_data)
        return result


def upsert_train_job(job: dict) -> None:
    """Insert or replace a job by id, atomically via update_train_jobs."""
    def _mutator(jobs):
        for i, old in enumerate(jobs):
            if old.get("id") == job.get("id"):
                jobs[i] = job
                return jobs, None
        jobs.append(job)
        return jobs, None
    update_train_jobs(_mutator)


def recover_orphaned_jobs_atomic(*, timeout=10) -> int:
    """Mark running/queued train jobs as failed (process was interrupted).

    Runs a single atomic read-modify-write under the train-jobs file lock so
    concurrent callers (e.g. two app instances restarting) cannot double-recover
    or clobber each other. Each recovered job gets
    ``status="failed"`` with ``message="进程重启时中断"`` and a refreshed
    ``updated_at``.

    Returns the number of jobs recovered. Never raises - any failure is logged
    and ``0`` is returned (startup recovery must not abort the boot path).
    """
    try:
        def _mutator(jobs):
            recovered = 0
            for job in jobs:
                if job.get("status") in _RECOVERABLE_STATUSES:
                    job["status"] = "failed"
                    job["message"] = "进程重启时中断"
                    job["updated_at"] = now_iso()
                    recovered += 1
            return (jobs if recovered else None), recovered
        return update_train_jobs(_mutator, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - startup recovery must not raise
        import logging
        logging.getLogger(__name__).warning(
            "recover_orphaned_jobs_atomic failed: %s", exc)
        return 0
