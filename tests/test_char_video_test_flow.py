"""Phase 0 characterization test — locks the video-test HTTP flow.

Golden baseline for the refactor of the 2905-line ``app.py`` Flask monolith
into a layered ``app/`` package. These tests lock the OBSERVABLE HTTP behavior
of the three video-test endpoints, NOT their implementation:

  - POST /api/video-test/start   (app.py:2772) — starts a video inference job
  - GET  /api/video-test/stream/<job_id>  (app.py:2812) — SSE progress stream
  - GET  /api/video-test/job/<job_id>     (app.py:2827) — job lookup

Only the video-inference externals are mocked (``video_inference_service`` and
``resolve_video_path``); the real Flask route handlers run unchanged. This
preserves the contract a future extraction into ``app/services`` /
``app/api`` must still satisfy.
"""
import pytest

import app as training_app


# ---------------------------------------------------------------------------
# Mocking helpers — mock only externals, never the route handlers.
# ---------------------------------------------------------------------------

def _patch_video_externals(monkeypatch, job_id="job-xyz"):
    """Patch the video-inference deep-module boundaries on app's reference.

    The facade validation (param parsing, path resolution, engine dispatch)
    runs for real; only the externals are mocked: path resolution, job launch,
    and SAM3 load state. Mocks return deterministic, contract-shaped values so
    the real handlers can be exercised without a real video file or inference
    backend.
    """
    monkeypatch.setattr(
        training_app.video_inference_service,
        "_launch_job",
        lambda *a, **k: {"id": job_id, "status": "running"},
    )
    monkeypatch.setattr(
        training_app.video_inference_service,
        "stream_progress",
        lambda jid: iter(['data: {"status":"running"}\n\n']),
    )
    monkeypatch.setattr(
        training_app.video_inference_service,
        "get_job",
        lambda jid: {"id": jid, "status": "completed"} if jid == job_id else None,
    )
    # Only "fake.mp4" resolves; any other name is treated as unknown (None).
    monkeypatch.setattr(
        training_app.video_inference_service,
        "resolve_video",
        lambda name: "/tmp/fake.mp4" if name == "fake.mp4" else None,
    )


# ---------------------------------------------------------------------------
# POST /api/video-test/start
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_video_test_start_returns_job_id_and_status_for_valid_yolo_request(isolated_app, monkeypatch):
    # Arrange — a valid yolo job request; mock externals so no real file/backend
    _patch_video_externals(monkeypatch)
    client = isolated_app.test_client()

    # Act
    response = client.post(
        "/api/video-test/start",
        json={
            "engine": "yolo",
            "target_fps": 2,
            "video_name": "fake.mp4",
        },
    )

    # Assert
    assert response.status_code == 200
    body = response.get_json()
    assert body["job_id"] == "job-xyz"
    assert body["status"] == "running"


@pytest.mark.integration
def test_video_test_start_rejects_invalid_engine_with_400(isolated_app, monkeypatch):
    # Arrange
    _patch_video_externals(monkeypatch)
    client = isolated_app.test_client()

    # Act
    response = client.post(
        "/api/video-test/start",
        json={"engine": "foo", "target_fps": 2, "video_name": "fake.mp4"},
    )

    # Assert
    assert response.status_code == 400
    assert "error" in response.get_json()


@pytest.mark.integration
def test_video_test_start_rejects_invalid_target_fps_with_400(isolated_app, monkeypatch):
    # Arrange — fps=3 is outside the allowed (1, 2, 5) set
    _patch_video_externals(monkeypatch)
    client = isolated_app.test_client()

    # Act
    response = client.post(
        "/api/video-test/start",
        json={"engine": "yolo", "target_fps": 3, "video_name": "fake.mp4"},
    )

    # Assert
    assert response.status_code == 400
    assert "error" in response.get_json()


@pytest.mark.integration
def test_video_test_start_rejects_missing_video_name_with_400(isolated_app, monkeypatch):
    # Arrange — mocked resolve_video_path returns None for empty/missing name
    _patch_video_externals(monkeypatch)
    client = isolated_app.test_client()

    # Act
    response = client.post(
        "/api/video-test/start",
        json={"engine": "yolo", "target_fps": 2, "video_name": ""},
    )

    # Assert
    assert response.status_code == 400
    assert "error" in response.get_json()


@pytest.mark.integration
def test_video_test_start_rejects_unknown_video_with_400(isolated_app, monkeypatch):
    # Arrange — a non-empty name that resolve_video_path does not recognize
    _patch_video_externals(monkeypatch)
    client = isolated_app.test_client()

    # Act
    response = client.post(
        "/api/video-test/start",
        json={"engine": "yolo", "target_fps": 2, "video_name": "does-not-exist.mp4"},
    )

    # Assert
    assert response.status_code == 400
    assert "error" in response.get_json()


@pytest.mark.integration
def test_video_test_start_sam3_requires_loaded_model_and_classes(isolated_app, monkeypatch):
    # Arrange — SAM3 path: model not loaded -> 503; loaded but no classes -> 400.
    # is_loaded is a read-only property backed by _loaded; patch the backing attr.
    _patch_video_externals(monkeypatch)
    monkeypatch.setattr(training_app.sam3_service, "_loaded", False)
    client = isolated_app.test_client()

    # Act / Assert — unloaded model surfaces as 503
    unloaded = client.post(
        "/api/video-test/start",
        json={"engine": "sam3", "target_fps": 2, "video_name": "fake.mp4", "classes": "person"},
    )
    assert unloaded.status_code == 503

    # Act / Assert — loaded but missing classes surfaces as 400
    monkeypatch.setattr(training_app.sam3_service, "_loaded", True)
    no_classes = client.post(
        "/api/video-test/start",
        json={"engine": "sam3", "target_fps": 2, "video_name": "fake.mp4", "classes": ""},
    )
    assert no_classes.status_code == 400


@pytest.mark.integration
def test_video_test_start_sam3_returns_job_id_when_loaded_with_classes(isolated_app, monkeypatch):
    # Arrange — SAM3 happy path. is_loaded is a property; set the backing _loaded.
    _patch_video_externals(monkeypatch)
    monkeypatch.setattr(training_app.sam3_service, "_loaded", True)
    client = isolated_app.test_client()

    # Act
    response = client.post(
        "/api/video-test/start",
        json={"engine": "sam3", "target_fps": 5, "video_name": "fake.mp4", "classes": "person,car"},
    )

    # Assert
    assert response.status_code == 200
    body = response.get_json()
    assert body["job_id"] == "job-xyz"
    assert body["status"] == "running"


# ---------------------------------------------------------------------------
# GET /api/video-test/stream/<job_id>
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_video_test_stream_returns_sse_content_type(isolated_app, monkeypatch):
    # Arrange
    _patch_video_externals(monkeypatch)
    client = isolated_app.test_client()

    # Act
    response = client.get("/api/video-test/stream/job-xyz")

    # Assert — mimetype is SSE
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/event-stream")


@pytest.mark.integration
def test_video_test_stream_yields_at_least_one_nonempty_chunk(isolated_app, monkeypatch):
    # Arrange
    _patch_video_externals(monkeypatch)
    client = isolated_app.test_client()

    # Act
    response = client.get("/api/video-test/stream/job-xyz")

    # Assert — body carries the chunk produced by the mocked stream_progress
    assert response.data
    assert b"status" in response.data


# ---------------------------------------------------------------------------
# GET /api/video-test/job/<job_id>
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_video_test_job_returns_job_dict_for_known_job(isolated_app, monkeypatch):
    # Arrange
    _patch_video_externals(monkeypatch)
    client = isolated_app.test_client()

    # Act
    response = client.get("/api/video-test/job/job-xyz")

    # Assert
    assert response.status_code == 200
    body = response.get_json()
    assert body["id"] == "job-xyz"


@pytest.mark.integration
def test_video_test_job_returns_404_for_unknown_job(isolated_app, monkeypatch):
    # Arrange — mocked get_job returns None for ids other than job-xyz
    _patch_video_externals(monkeypatch)
    client = isolated_app.test_client()

    # Act
    response = client.get("/api/video-test/job/no-such-job")

    # Assert
    assert response.status_code == 404
    assert "error" in response.get_json()
