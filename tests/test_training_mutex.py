"""H12: training mutex - a running job blocks new /api/train/start (409).

The blueprint guards against concurrent training by rejecting a new start when
any existing job has ``status == "running"``. This locks that behavior so a
regression that drops the guard cannot silently launch overlapping training
threads (GPU/CPU contention, dataset races).

Approach: seed a ``running`` job into train_jobs.json, then POST /api/train/start
must return 409 with ``status == "busy"``. ``run_training_job`` is mocked to
no-op so the assertion depends only on the mutex guard, not on thread timing.
"""
import json
import sys
from pathlib import Path

import pytest

import app as training_app  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _seed_running_job():
    """Write a single status='running' job into train_jobs.json."""
    Path(training_app.PATHS["train_jobs"]).write_text(
        json.dumps([{
            "id": "train_running_1",
            "status": "running",
            "mode": "incremental",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }]),
        encoding="utf-8",
    )


@pytest.mark.integration
def test_train_start_returns_409_when_a_job_is_running(isolated_app, monkeypatch):
    # Even with the background runner mocked, the mutex guard fires BEFORE the
    # thread is launched, so the seeded 'running' job blocks the new start.
    monkeypatch.setattr("app.blueprints.training.run_training_job", lambda job_id, root_path=None: None)
    _seed_running_job()

    response = isolated_app.test_client().post(
        "/api/train/start",
        json={"mode": "incremental", "epochs": 1, "imgsz": 640, "batch": 1},
    )

    assert response.status_code == 409
    body = response.get_json()
    assert body["status"] == "busy"
    assert "error" in body
    # No new job appended - still exactly the seeded running job.
    jobs = json.loads(Path(training_app.PATHS["train_jobs"]).read_text(encoding="utf-8"))
    assert len(jobs) == 1
    assert jobs[0]["id"] == "train_running_1"


@pytest.mark.integration
def test_train_start_allows_new_job_when_existing_is_queued_not_running(isolated_app, monkeypatch):
    """The mutex checks 'running' only (not 'queued') so back-to-back queued
    submissions in the mocked-runner test flow are not blocked. Lock this so
    the guard's exact predicate is preserved."""
    monkeypatch.setattr("app.blueprints.training.run_training_job", lambda job_id, root_path=None: None)
    Path(training_app.PATHS["train_jobs"]).write_text(
        json.dumps([{
            "id": "train_queued_1",
            "status": "queued",
            "mode": "incremental",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }]),
        encoding="utf-8",
    )

    response = isolated_app.test_client().post(
        "/api/train/start",
        json={"mode": "incremental", "epochs": 1, "imgsz": 640, "batch": 1},
    )

    # A queued (not running) job does NOT block - new job accepted.
    assert response.status_code == 200
    assert response.get_json()["message"] == "training started"
    jobs = json.loads(Path(training_app.PATHS["train_jobs"]).read_text(encoding="utf-8"))
    assert len(jobs) == 2
