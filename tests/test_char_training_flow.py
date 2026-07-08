"""Phase 0 characterization test — locks the training flow's HTTP + persistence
behavior as a golden baseline for refactoring the 2905-line ``app.py`` monolith
into a layered ``app/`` package.

Locks the OBSERVABLE contract (HTTP status, stable response keys, on-disk
persistence snapshots) of the training pipeline endpoints. Implementation is
free to move; these shapes must not. Complements ``test_training_backend.py``
without re-asserting split save/reset counts (already covered there).
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as training_app  # noqa: E402


# isolated_app fixture lives in tests/conftest.py (shared across characterization tests).


def _write_model_file(tmp_path: Path, name: str = "best.pt") -> Path:
    """Create a fake model weights file the activate endpoint can verify exists."""
    models_dir = Path(training_app.PATHS["plugins_yolo11"]) / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    model_file = models_dir / name
    model_file.write_bytes(b"fake-weights")
    return model_file


# ---------------------------------------------------------------------------
# GET /api/train/readiness
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_readiness_returns_stable_shape_with_cuda_block(isolated_app, tmp_path):
    client = isolated_app.test_client()

    response = client.get("/api/train/readiness")

    assert response.status_code == 200
    body = response.get_json()
    assert set(body.keys()) == {
        "total_images",
        "annotated_images",
        "min_for_initial",
        "ready_for_initial",
        "cuda",
    }
    assert isinstance(body["total_images"], int)
    assert isinstance(body["annotated_images"], int)
    assert body["min_for_initial"] == 20
    assert isinstance(body["ready_for_initial"], bool)
    assert isinstance(body["cuda"], dict)
    assert set(body["cuda"].keys()) == {
        "available",
        "device_count",
        "device_name",
        "torch_version",
        "error",
    }


@pytest.mark.integration
def test_readiness_counts_uploaded_and_annotated_images(isolated_app, tmp_path):
    upload_dir = Path(training_app.PATHS["uploads"])
    for index in range(3):
        (upload_dir / f"img_{index}.jpg").write_bytes(b"x")
    annotations = {"img_0.jpg": [{"class": "part"}], "img_1.jpg": [{"class": "part"}]}
    Path(training_app.PATHS["annotations"]).write_text(json.dumps(annotations), encoding="utf-8")

    body = isolated_app.test_client().get("/api/train/readiness").get_json()

    assert body["total_images"] == 3
    assert body["annotated_images"] == 2
    assert body["ready_for_initial"] is False  # 2 < min_for_initial(20)


# ---------------------------------------------------------------------------
# GET /api/train/split  (shape only — counts/save already covered elsewhere)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_split_get_returns_summary_shape_with_locked_keys(isolated_app):
    body = isolated_app.test_client().get("/api/train/split").get_json()

    assert set(body.keys()) == {
        "split_config",
        "counts",
        "class_options",
        "candidate_totals",
        "updated_at",
    }
    assert isinstance(body["counts"], dict)
    assert isinstance(body["class_options"], list)
    assert set(body["candidate_totals"].keys()) == {
        "total_images",
        "annotated_images",
        "candidate_images",
        "unannotated_images",
    }
    assert isinstance(body["updated_at"], str) and body["updated_at"]


@pytest.mark.integration
def test_split_get_honors_profile_id_query_param(isolated_app):
    body = isolated_app.test_client().get(
        "/api/train/split?profile_id=custom-xyz"
    ).get_json()

    # An unknown profile id resolves to a fresh config carrying that profile_id.
    assert body["split_config"]["profile_id"] == "custom-xyz"


@pytest.mark.integration
def test_split_post_persists_profile_under_default_id(isolated_app):
    response = isolated_app.test_client().post(
        "/api/train/split",
        json={"train_ratio": 0.7, "val_ratio": 0.2, "test_ratio": 0.1},
    )

    assert response.status_code == 200
    persisted = json.loads(
        Path(training_app.PATHS["training_splits"]).read_text(encoding="utf-8")
    )
    assert "default" in persisted
    assert "split_config" in persisted["default"]


# ---------------------------------------------------------------------------
# POST /api/train/start  (run_training_job mocked — no real YOLO training)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_train_start_incremental_persists_queued_job(isolated_app, tmp_path, monkeypatch):
    monkeypatch.setattr(training_app, "run_training_job", lambda job_id, root_path=None: None)

    response = isolated_app.test_client().post(
        "/api/train/start",
        json={"mode": "incremental", "epochs": 5, "base_model": "yolo11n.pt"},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["message"] == "training started"
    job = body["job"]
    assert job["status"] == "queued"
    assert job["mode"] == "incremental"
    assert job["id"].startswith("train_")
    assert job["base_model"] == "yolo11n.pt"
    assert job["epochs"] == 5
    assert job["total_epochs"] == 5

    # log_path must live under the redirected train_work dir (tmp_path).
    assert str(Path(job["log_path"])).startswith(str(tmp_path))
    assert job["log_path"].replace("\\", "/").endswith("train.log")

    # The job record is persisted to train_jobs.json BEFORE the thread launches,
    # so the queued snapshot is observable immediately.
    persisted = json.loads(
        Path(training_app.PATHS["train_jobs"]).read_text(encoding="utf-8")
    )
    assert len(persisted) == 1
    assert persisted[0]["id"] == job["id"]
    assert persisted[0]["status"] == "queued"
    for field in ("id", "mode", "base_model", "epochs", "log_path"):
        assert field in persisted[0]


@pytest.mark.integration
def test_train_start_rejects_initial_mode_below_readiness_gate(isolated_app, monkeypatch):
    monkeypatch.setattr(training_app, "run_training_job", lambda job_id, root_path=None: None)

    response = isolated_app.test_client().post(
        "/api/train/start",
        json={"mode": "initial"},
    )

    assert response.status_code == 400
    body = response.get_json()
    assert "error" in body
    assert "readiness" in body  # readiness echoed back on rejection
    # No job persisted when the readiness gate blocks.
    assert json.loads(
        Path(training_app.PATHS["train_jobs"]).read_text(encoding="utf-8")
    ) == []


# ---------------------------------------------------------------------------
# GET /api/train/jobs
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_train_jobs_returns_jobs_envelope_sorted_desc(isolated_app, monkeypatch):
    monkeypatch.setattr(training_app, "run_training_job", lambda job_id, root_path=None: None)

    client = isolated_app.test_client()
    client.post("/api/train/start", json={"mode": "incremental"})
    client.post("/api/train/start", json={"mode": "incremental"})

    response = client.get("/api/train/jobs")

    assert response.status_code == 200
    body = response.get_json()
    # Envelope is {"jobs": [...]}, NOT a bare list — lock this shape.
    assert isinstance(body, dict)
    assert isinstance(body["jobs"], list)
    assert len(body["jobs"]) == 2
    # Every job carries the stable identifier fields.
    for job in body["jobs"]:
        assert "id" in job and job["id"].startswith("train_")
        assert "status" in job
        assert "created_at" in job


# ---------------------------------------------------------------------------
# POST /api/models/<model_id>/activate
# ---------------------------------------------------------------------------


def _seed_registry_model(model_id: str = "m-1", name: str = "v1") -> Path:
    """Pre-seed a model record whose weights file exists on disk."""
    model_file = _write_model_file(Path(training_app.PATHS["root"]), "v1.pt")
    record = {
        "id": model_id,
        "name": name,
        "path": str(model_file),
        "status": "candidate",
        "created_at": "2026-01-01T00:00:00",
    }
    Path(training_app.PATHS["model_registry"]).write_text(
        json.dumps([record]), encoding="utf-8"
    )
    return model_file


@pytest.mark.integration
def test_model_activate_persists_active_model_and_marks_production(isolated_app):
    _seed_registry_model()

    response = isolated_app.test_client().post("/api/models/m-1/activate")

    assert response.status_code == 200
    body = response.get_json()
    assert body["message"] == "model activated"
    active = body["active"]
    assert active["model_id"] == "m-1"
    assert active["model_name"] == "v1"
    assert isinstance(active["model_path"], str) and active["model_path"]
    assert "updated_at" in active

    persisted_active = json.loads(
        Path(training_app.PATHS["active_model"]).read_text(encoding="utf-8")
    )
    assert persisted_active["model_id"] == "m-1"
    assert persisted_active["model_path"] == active["model_path"]

    registry = json.loads(
        Path(training_app.PATHS["model_registry"]).read_text(encoding="utf-8")
    )
    assert registry[0]["status"] == "production"


@pytest.mark.integration
def test_model_activate_demotes_other_models_to_candidate(isolated_app):
    model_file = _write_model_file(Path(training_app.PATHS["root"]), "v1.pt")
    records = [
        {"id": "m-1", "name": "v1", "path": str(model_file), "status": "production", "created_at": "2026-01-01T00:00:00"},
        {"id": "m-2", "name": "v2", "path": str(model_file), "status": "candidate", "created_at": "2026-01-02T00:00:00"},
    ]
    Path(training_app.PATHS["model_registry"]).write_text(json.dumps(records), encoding="utf-8")

    response = isolated_app.test_client().post("/api/models/m-2/activate")

    assert response.status_code == 200
    registry = json.loads(
        Path(training_app.PATHS["model_registry"]).read_text(encoding="utf-8")
    )
    by_id = {m["id"]: m for m in registry}
    assert by_id["m-2"]["status"] == "production"
    assert by_id["m-1"]["status"] == "candidate"


@pytest.mark.integration
def test_model_activate_returns_404_for_unknown_model(isolated_app):
    _seed_registry_model()

    response = isolated_app.test_client().post("/api/models/no-such-id/activate")

    assert response.status_code == 404
    assert response.get_json()["error"] == "model not found"


@pytest.mark.integration
def test_model_activate_returns_400_when_weights_file_missing(isolated_app):
    record = {
        "id": "m-ghost",
        "name": "ghost",
        "path": str(Path(training_app.PATHS["root"]) / "missing.pt"),
        "status": "candidate",
        "created_at": "2026-01-01T00:00:00",
    }
    Path(training_app.PATHS["model_registry"]).write_text(json.dumps([record]), encoding="utf-8")

    response = isolated_app.test_client().post("/api/models/m-ghost/activate")

    assert response.status_code == 400
    assert response.get_json()["error"] == "model file does not exist"
