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
import types
from pathlib import Path

import pytest
from PIL import Image

import app as training_app  # noqa: E402
from app.repositories.train_jobs_repo import upsert_train_job  # noqa: E402
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
    upsert_train_job(job)  # H4: helpers persist via mutate_train_job (job must exist on disk)

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
    upsert_train_job(job)  # H4: _mark_completed persists via mutate_train_job
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
    upsert_train_job(job)  # H4: _mark_failed persists via mutate_train_job

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
    upsert_train_job(job)  # H4: _mark_failed persists via mutate_train_job

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
    upsert_train_job(job)  # H4: helpers persist via mutate_train_job

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


# ---------------------------------------------------------------------------
# C4: real run_training_job end-to-end with a fake ultralytics.YOLO
# ---------------------------------------------------------------------------

class _FakeTrainer:
    def __init__(self, epoch):
        self.epoch = epoch


class _FakeYOLO:
    """Stand-in for ultralytics.YOLO so run_training_job runs without GPU.

    train() writes the artifacts the runner expects (weights/best.pt,
    results.csv, results.png) and invokes the registered on_train_epoch_end
    callback once per epoch so the H4 progress-persist path is exercised.
    """

    def __init__(self, base_model):
        self.base_model = base_model
        self._callbacks = {}

    def add_callback(self, event, fn):
        self._callbacks.setdefault(event, []).append(fn)

    def train(self, **kwargs):
        epochs = int(kwargs.get("epochs", 1))
        run_dir = os.path.join(kwargs["project"], kwargs.get("name", "detector"))
        weights_dir = os.path.join(run_dir, "weights")
        os.makedirs(weights_dir, exist_ok=True)
        for epoch in range(epochs):
            trainer = _FakeTrainer(epoch)
            for fn in self._callbacks.get("on_train_epoch_end", []):
                fn(trainer)
        Path(weights_dir, "best.pt").write_bytes(b"fake-trained-weights")
        Path(run_dir, "results.csv").write_text(
            "epoch,metrics/mAP50(B)\n1,0.5\n", encoding="utf-8"
        )
        Path(run_dir, "results.png").write_bytes(b"fake-png")


def _seed_20_annotated_images():
    """Seed uploads/ with 20 annotated images + classes (min for training)."""
    anns = {}
    for i in range(20):
        name = f"train_img_{i}.png"
        Image.new("RGB", (32, 32), color=(i, 0, 0)).save(
            Path(training_app.PATHS["uploads"]) / name
        )
        anns[name] = [{"class": "part", "points": [[0, 0], [10, 0], [10, 10], [0, 10]], "type": "rectangle"}]
    Path(training_app.PATHS["classes"]).write_text(
        json.dumps([{"name": "part", "color": "#ffffff"}]), encoding="utf-8"
    )
    Path(training_app.PATHS["annotations"]).write_text(json.dumps(anns), encoding="utf-8")


@pytest.mark.integration
def test_run_training_job_end_to_end_completes(isolated_app, monkeypatch):
    """C4: real run_training_job transitions queued->running->completed and
    registers the trained model, with ultralytics.YOLO faked (no GPU)."""
    _seed_20_annotated_images()

    fake_module = types.ModuleType("ultralytics")
    fake_module.YOLO = _FakeYOLO
    monkeypatch.setitem(sys.modules, "ultralytics", fake_module)

    from app.services import models_service
    from app.services.training_job_runner import run_training_job
    from app.services.training_service import build_train_job, training_readiness

    job = build_train_job(
        {"epochs": 2, "mode": "incremental"},
        "incremental",
        training_readiness(),
        models_service.get_active_model(),
    )
    upsert_train_job(job)

    run_training_job(job["id"])

    jobs = json.loads(Path(training_app.PATHS["train_jobs"]).read_text(encoding="utf-8"))
    completed = next(j for j in jobs if j["id"] == job["id"])
    assert completed["status"] == "completed"
    assert completed["progress"] == 100
    assert os.path.isfile(completed["weights_path"])

    registry = json.loads(
        Path(training_app.PATHS["model_registry"]).read_text(encoding="utf-8")
    )
    assert len(registry) == 1
    assert registry[0]["job_id"] == job["id"]

    active = json.loads(
        Path(training_app.PATHS["active_model"]).read_text(encoding="utf-8")
    )
    assert active["model_id"] == registry[0]["id"]


@pytest.mark.integration
def test_run_training_job_failure_marks_failed_and_cleans_dataset(isolated_app, monkeypatch):
    """C4: when YOLO.train raises, run_training_job marks the job failed and
    removes the per-job dataset copy (best-effort cleanup)."""
    _seed_20_annotated_images()

    class _BoomYOLO(_FakeYOLO):
        def train(self, **kwargs):
            raise RuntimeError("training exploded")

    fake_module = types.ModuleType("ultralytics")
    fake_module.YOLO = _BoomYOLO
    monkeypatch.setitem(sys.modules, "ultralytics", fake_module)

    from app.services import models_service
    from app.services.training_job_runner import run_training_job
    from app.services.training_service import build_train_job, training_readiness

    job = build_train_job(
        {"epochs": 2, "mode": "incremental"},
        "incremental",
        training_readiness(),
        models_service.get_active_model(),
    )
    upsert_train_job(job)

    run_training_job(job["id"])

    jobs = json.loads(Path(training_app.PATHS["train_jobs"]).read_text(encoding="utf-8"))
    failed = next(j for j in jobs if j["id"] == job["id"])
    assert failed["status"] == "failed"
    assert "training exploded" in failed["message"]
    # Dataset copy cleaned up by _cleanup_failed_dataset.
    dataset_dir = os.path.join(training_app.PATHS["train_work"], job["id"], "dataset")
    assert not os.path.isdir(dataset_dir)
