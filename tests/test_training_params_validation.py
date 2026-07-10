"""H13: training parameter validation.

Locks ``build_train_job`` rejection of invalid epochs/imgsz/batch (raised as
``ValueError`` which the blueprint maps to HTTP 400), and that the HTTP
``/api/train/start`` route returns 400 for invalid params while accepting valid
ones. ``run_training_job`` is mocked so no real training runs.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.training_service import build_train_job  # noqa: E402

import app as training_app  # noqa: E402


_READY = {"total_images": 25, "annotated_images": 25}
_INACTIVE = {"model_id": "", "model_name": "", "model_path": ""}


def _base_payload(**overrides):
    payload = {"epochs": 5, "imgsz": 640, "batch": 4, "mode": "incremental"}
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Unit: build_train_job raises ValueError on invalid params
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_build_train_job_rejects_zero_epochs():
    with pytest.raises(ValueError, match="epochs 必须在 1-1000"):
        build_train_job(_base_payload(epochs=0), "incremental", _READY, _INACTIVE)


@pytest.mark.unit
def test_build_train_job_rejects_imgsz_not_multiple_of_32():
    with pytest.raises(ValueError, match="32 的正整数倍"):
        build_train_job(_base_payload(imgsz=100), "incremental", _READY, _INACTIVE)


@pytest.mark.unit
def test_build_train_job_rejects_zero_batch():
    with pytest.raises(ValueError, match="batch 必须在 1-512"):
        build_train_job(_base_payload(batch=0), "incremental", _READY, _INACTIVE)


@pytest.mark.unit
def test_build_train_job_rejects_negative_imgsz():
    with pytest.raises(ValueError, match="32 的正整数倍"):
        build_train_job(_base_payload(imgsz=-64), "incremental", _READY, _INACTIVE)


@pytest.mark.unit
def test_build_train_job_rejects_non_numeric_epochs():
    with pytest.raises(ValueError, match="epochs 参数必须为整数"):
        build_train_job(_base_payload(epochs="abc"), "incremental", _READY, _INACTIVE)


@pytest.mark.unit
def test_build_train_job_rejects_epochs_above_max():
    with pytest.raises(ValueError, match="epochs 必须在 1-1000"):
        build_train_job(_base_payload(epochs=1001), "incremental", _READY, _INACTIVE)


# ---------------------------------------------------------------------------
# Unit: valid params build a queued job with normalized fields
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_build_train_job_accepts_valid_params():
    job = build_train_job(
        _base_payload(epochs=1, imgsz=640, batch=1),
        "incremental", _READY, _INACTIVE,
    )

    assert job["status"] == "queued"
    assert job["epochs"] == 1
    assert job["imgsz"] == 640
    assert job["batch"] == 1
    assert job["total_epochs"] == 1
    assert job["id"].startswith("train_")


@pytest.mark.unit
def test_build_train_job_applies_defaults_when_params_omitted():
    job = build_train_job({"mode": "incremental"}, "incremental", _READY, _INACTIVE)

    # defaults: epochs=30, imgsz=640, batch=8
    assert job["epochs"] == 30
    assert job["imgsz"] == 640
    assert job["batch"] == 8


# ---------------------------------------------------------------------------
# Integration: HTTP /api/train/start rejects invalid params with 400
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_train_start_rejects_invalid_epochs_with_400(isolated_app, monkeypatch):
    # Mock the background runner so no real training thread does work.
    monkeypatch.setattr("app.blueprints.training.run_training_job", lambda job_id, root_path=None: None)

    response = isolated_app.test_client().post(
        "/api/train/start",
        json={"mode": "incremental", "epochs": 0, "imgsz": 640, "batch": 1},
    )

    assert response.status_code == 400
    assert "error" in response.get_json()
    # No job persisted when validation blocks.
    jobs = json.loads(Path(training_app.PATHS["train_jobs"]).read_text(encoding="utf-8"))
    assert jobs == []


@pytest.mark.integration
def test_train_start_rejects_invalid_imgsz_with_400(isolated_app, monkeypatch):
    monkeypatch.setattr("app.blueprints.training.run_training_job", lambda job_id, root_path=None: None)

    response = isolated_app.test_client().post(
        "/api/train/start",
        json={"mode": "incremental", "epochs": 1, "imgsz": 100, "batch": 1},
    )

    assert response.status_code == 400
    assert "error" in response.get_json()


@pytest.mark.integration
def test_train_start_accepts_valid_params(isolated_app, monkeypatch):
    monkeypatch.setattr("app.blueprints.training.run_training_job", lambda job_id, root_path=None: None)

    response = isolated_app.test_client().post(
        "/api/train/start",
        json={"mode": "incremental", "epochs": 1, "imgsz": 640, "batch": 1},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["message"] == "training started"
    assert body["job"]["epochs"] == 1
    assert body["job"]["imgsz"] == 640
    assert body["job"]["batch"] == 1
