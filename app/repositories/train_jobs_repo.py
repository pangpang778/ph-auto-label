"""Train-jobs JSON store (train_jobs.json) + lock."""
import threading

from app.common.config import PATHS
from app.common.json_store import read_json_file, write_json_file

TRAIN_JOBS_LOCK = threading.Lock()


def read_train_jobs() -> list[dict]:
    with TRAIN_JOBS_LOCK:
        return read_json_file(PATHS['train_jobs'], [])


def write_train_jobs(jobs: list[dict]) -> None:
    with TRAIN_JOBS_LOCK:
        write_json_file(PATHS['train_jobs'], jobs)


def upsert_train_job(job: dict) -> None:
    with TRAIN_JOBS_LOCK:
        jobs = read_json_file(PATHS['train_jobs'], [])
        for i, old in enumerate(jobs):
            if old.get("id") == job.get("id"):
                jobs[i] = job
                write_json_file(PATHS['train_jobs'], jobs)
                return
        jobs.append(job)
        write_json_file(PATHS['train_jobs'], jobs)
