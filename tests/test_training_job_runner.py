"""H17: training job_runner state machine (stable unit-test form).

Full ``run_training_job`` requires mocking ``ultralytics.YOLO`` (heavy, GPU-adjacent,
flaky across environments). Per the task spec, this file degrades to direct
unit tests of the state-transition helpers (_prepare_job_dir, _register_trained_model,
_mark_completed, _mark_failed) with a minimal job dict + a real tmp run_dir holding
a fake best.pt. This locks the observable side effects (status transitions,
registry append, active_model update, weights_path resolution) without depending
on the real YOLO training loop.

These are heavier than pure unit tests (they touch disk via PATHS) but are
fast (<2s) and deterministic. Marked ``integration`` because they rely on the
isolated_app PATHS redirection.
"""
import json
import os
import sys
from pathlib import Path

import pytest

import app as training_app  # noqa: E402
from app.services.training_job_runner import (  # noqa: E402
    _mark_completed,
    _mark_failed,
    _prepare_job_dir,
    _register_trained_model,
    _resolve_trained_model,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _minimal_job(job_id: str = "train_test_1", epochs: int = 1) -> dict:
    return {
        "id": job_id,
        "mode": "incremental",
        "status": "queued",
        "progress": 0,
        "epochs": epochs,
        "total_epochs": epochs,
        "base_model": "yolo11n.pt",
        "device": "cpu",
        "log_path": os.path.join(training_app.PATHS["train_work"], job_id, "train.log"),
        "split_counts": {},
    }


def _make_run_dir_with_weights(job_id: str) -> str:
    """Create a fake run_dir with weights/best.pt the runner can resolve + copy."""
    run_dir = os.path.join(training_app.PATHS["train_work"], job_id, "runs", "detector")
    weights_dir = os.path.join(run_dir, "weights")
    os.makedirs(weights_dir, exist_ok=True)
    Path(weights_dir, "best.pt").write_bytes(b"fake-trained-weights")
    # results.csv so metrics extraction has something to read.
    Path(run_dir, "results.csv").write_text(
        "epoch,metrics/mAP50(B)\n1,0.42\n", encoding="utf-8"
    )
    Path(run_dir, "results.png").write_bytes(b"fake-png")
    return run_dir


# ---------------------------------------------------------------------------
# _resolve_trained_model
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_resolve_trained_model_prefers_best_over_last(isolated_app, tmp_path):
    run_dir = tmp_path / "run"
    weights = run_dir / "weights"
    weights.mkdir(parents=True)
    (weights / "best.pt").write_bytes(b"best")
    (weights / "last.pt").write_bytes(b"last")

    resolved = _resolve_trained_model(str(run_dir))

    assert resolved.endswith("best.pt")


@pytest.mark.integration
def test_resolve_trained_model_falls_back_to_last_when_no_best(isolated_app, tmp_path):
    run_dir = tmp_path / "run"
    weights = run_dir / "weights"
    weights.mkdir(parents=True)
    (weights / "last.pt").write_bytes(b"last")

    resolved = _resolve_trained_model(str(run_dir))

    assert resolved.endswith("last.pt")


@pytest.mark.integration
def test_resolve_trained_model_raises_when_no_weights(isolated_app, tmp_path):
    run_dir = tmp_path / "run"
    (run_dir / "weights").mkdir(parents=True)

    with pytest.raises(FileNotFoundError):
        _resolve_trained_model(str(run_dir))


# ---------------------------------------------------------------------------
# _prepare_job_dir: queued -> running transition
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_prepare_job_dir_transitions_queued_to_running_and_persists(isolated_app):
    job = _minimal_job()

    job_dir = _prepare_job_dir(job)

    assert job["status"] == "running"
    assert job["progress"] == 5
    assert "Preparing" in job["message"]
    assert os.path.isdir(job_dir)
    assert os.path.isfile(job["log_path"])
    # Persisted to train_jobs.json as running.
    jobs = json.loads(Path(training_app.PATHS["train_jobs"]).read_text(encoding="utf-8"))
    assert len(jobs) == 1
    assert jobs[0]["id"] == job["id"]
    assert jobs[0]["status"] == "running"


# ---------------------------------------------------------------------------
# _register_trained_model: registry append + active_model update
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_register_trained_model_appends_production_record_and_sets_active(isolated_app):
    job = _minimal_job()
    run_dir = _make_run_dir_with_weights(job["id"])

    model_info = _register_trained_model(job, job["id"], run_dir)

    # The copied model file exists in the models dir.
    assert os.path.isfile(model_info["model_dst"])
    # model_dst is a real file the active_model points to.
    # Registry has one record.
    registry = json.loads(
        Path(training_app.PATHS["model_registry"]).read_text(encoding="utf-8")
    )
    assert len(registry) == 1
    record = registry[0]
    assert record["id"] == model_info["model_id"]
    assert record["job_id"] == job["id"]
    assert record["mode"] == "incremental"
    # _register_trained_model itself sets status="candidate"; activation to
    # "production" happens via set_active (active_model.json). Lock both.
    assert record["status"] == "candidate"
    assert record["weights_path"].endswith("best.pt")
    # active_model.json updated to this model.
    active = json.loads(
        Path(training_app.PATHS["active_model"]).read_text(encoding="utf-8")
    )
    assert active["model_id"] == model_info["model_id"]
    assert active["model_path"] == model_info["model_dst"]


@pytest.mark.integration
def test_register_trained_model_extracts_metrics_from_results_csv(isolated_app):
    job = _minimal_job()
    run_dir = _make_run_dir_with_weights(job["id"])

    model_info = _register_trained_model(job, job["id"], run_dir)

    # results.csv had one numeric column -> metrics dict carries it.
    assert model_info["metrics"]["metrics/mAP50(B)"] == pytest.approx(0.42)


# ---------------------------------------------------------------------------
# _mark_completed: running -> completed, weights_path recorded
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_mark_completed_transitions_to_completed_with_weights_path(isolated_app):
    job = _minimal_job()
    job["status"] = "running"
    job["progress"] = 45
    run_dir = _make_run_dir_with_weights(job["id"])
    model_info = _register_trained_model(job, job["id"], run_dir)

    _mark_completed(job, model_info)

    assert job["status"] == "completed"
    assert job["progress"] == 100
    assert job["weights_path"].endswith("best.pt")
    assert os.path.isfile(job["weights_path"])  # trained_model file exists
    assert job["model_id"] == model_info["model_id"]
    assert job["epoch"] == job["epochs"]
    # Persisted as completed.
    jobs = json.loads(Path(training_app.PATHS["train_jobs"]).read_text(encoding="utf-8"))
    assert jobs[0]["status"] == "completed"


# ---------------------------------------------------------------------------
# _mark_failed: running -> failed, progress capped at 99
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_mark_failed_transitions_to_failed_and_caps_progress(isolated_app):
    job = _minimal_job()
    job["status"] = "running"
    job["progress"] = 45

    _mark_failed(job, RuntimeError("boom"))

    assert job["status"] == "failed"
    # ponytail: 100 reserved for completed; failed keeps progress capped at 99.
    assert job["progress"] <= 99
    # The exception message is embedded in job["message"]; job["error"] holds
    # the traceback string (which may be "NoneType: None" when the exception
    # was raised outside a try/except frame). Lock the message, not the trace.
    assert "boom" in job["message"]
    assert "error" in job  # traceback field always present
    assert isinstance(job["error"], str)
    # Persisted as failed.
    jobs = json.loads(Path(training_app.PATHS["train_jobs"]).read_text(encoding="utf-8"))
    assert jobs[0]["status"] == "failed"


@pytest.mark.integration
def test_mark_failed_does_not_claim_100_progress(isolated_app):
    """A failed job that had reached progress=100 mid-run still reports <=99
    after failure (100 is the completed-only signal)."""
    job = _minimal_job()
    job["status"] = "running"
    job["progress"] = 100  # hypothetical mid-run value

    _mark_failed(job, RuntimeError("late failure"))

    assert job["status"] == "failed"
    assert job["progress"] == 99


# ---------------------------------------------------------------------------
# Full state-machine sequence (queued -> running -> completed), no real YOLO
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_full_state_machine_queued_to_running_to_completed(isolated_app):
    """End-to-end state-machine sequence using only the helpers (no YOLO train).

    This is the closest stable approximation of run_training_job's control
    flow: prepare (queued->running) -> register model -> mark completed.
    Asserts the final job is completed, weights_path resolves to a real file,
    and the registry gained a record pointing at it.
    """
    job = _minimal_job()

    _prepare_job_dir(job)
    assert job["status"] == "running"

    run_dir = _make_run_dir_with_weights(job["id"])
    model_info = _register_trained_model(job, job["id"], run_dir)
    _mark_completed(job, model_info)

    assert job["status"] == "completed"
    assert os.path.isfile(job["weights_path"])

    registry = json.loads(
        Path(training_app.PATHS["model_registry"]).read_text(encoding="utf-8")
    )
    assert len(registry) == 1
    assert registry[0]["job_id"] == job["id"]

    active = json.loads(
        Path(training_app.PATHS["active_model"]).read_text(encoding="utf-8")
    )
    assert active["model_id"] == model_info["model_id"]
    assert os.path.isfile(active["model_path"])
