"""Unit tests for the video-inference service facade and pure helpers.

The HTTP contract lives in test_char_video_test_flow.py; this file tests the
deep module at the service seam: param parsing, facade dispatch/validation
(the 400/503 mappings), and video path containment. The service is built with
constructor-injected directories so no real paths are touched.
"""
import pytest

from app.services.video_inference_service import (
    VideoInferenceService,
    VideoTestError,
    _is_within,
    _parse_classes,
    _parse_params,
)


# --- param parsing ---

def test_parse_params_defaults_for_yolo():
    assert _parse_params({"video_name": "a.mp4"}) == ("a.mp4", "yolo", 2, 0.35)


def test_parse_params_normalizes_engine_and_parses_numerics():
    assert _parse_params(
        {"video_name": "a.mp4", "engine": "SAM3", "target_fps": 5, "confidence": "0.5"}
    ) == ("a.mp4", "sam3", 5, 0.5)


@pytest.mark.parametrize("bad", [{"engine": "foo"}, {"target_fps": 3}])
def test_parse_params_rejects_invalid(bad):
    with pytest.raises(ValueError):
        _parse_params({"video_name": "a.mp4", **bad})


def test_parse_classes_handles_list_str_and_empty():
    assert _parse_classes(["person", " car "]) == ["person", "car"]
    assert _parse_classes("person，car;cat\n dog") == ["person", "car", "cat", "dog"]
    assert _parse_classes("") == []
    assert _parse_classes(None) == []


# --- facade dispatch ---

def _make_service(tmp_path):
    upload = tmp_path / "uploads"
    static = tmp_path / "static"
    upload.mkdir(parents=True)
    static.mkdir(parents=True)
    (upload / "a.mp4").write_bytes(b"fake")
    return VideoInferenceService(upload_video_dir=str(upload), static_video_dir=str(static))


def test_facade_yolo_launches_with_pretrained_name(tmp_path, monkeypatch):
    svc = _make_service(tmp_path)
    monkeypatch.setattr(svc, "_launch_job", lambda *a, **k: {**k, "engine": a[1], "path": a[0]})
    result = svc.start_job({"video_name": "a.mp4", "engine": "yolo", "model": "yolo11n.pt"})
    assert result["engine"] == "yolo"
    assert result["model_path"] == "yolo11n.pt"
    assert result["path"].endswith("a.mp4")


def test_facade_yolo_accepts_model_under_models_dir(tmp_path, monkeypatch):
    from app.common.config import PATHS

    monkeypatch.setitem(PATHS, "plugins_yolo11", str(tmp_path))
    models_dir = tmp_path / "models"
    models_dir.mkdir(parents=True)
    (models_dir / "trained.pt").write_bytes(b"x")
    svc = _make_service(tmp_path)
    monkeypatch.setattr(svc, "_launch_job", lambda *a, **k: k)
    result = svc.start_job(
        {"video_name": "a.mp4", "engine": "yolo", "model": str(models_dir / "trained.pt")}
    )
    assert result["model_path"].endswith("trained.pt")


@pytest.mark.parametrize("evil", ["../../../etc/passwd", "C:\\Windows\\system32\\x.pt"])
def test_facade_yolo_rejects_out_of_dir_model_path(tmp_path, monkeypatch, evil):
    from app.common.config import PATHS

    monkeypatch.setitem(PATHS, "plugins_yolo11", str(tmp_path / "yolo11"))
    svc = _make_service(tmp_path)
    with pytest.raises(VideoTestError) as ei:
        svc.start_job({"video_name": "a.mp4", "engine": "yolo", "model": evil})
    assert ei.value.status == 400


def test_facade_sam3_launches_with_parsed_classes(tmp_path, monkeypatch):
    svc = _make_service(tmp_path)
    monkeypatch.setattr(svc, "sam3_loaded", lambda: True)
    monkeypatch.setattr(svc, "_launch_job", lambda *a, **k: {"engine": a[1], **k})
    result = svc.start_job({"video_name": "a.mp4", "engine": "sam3", "classes": "person,car"})
    assert result["engine"] == "sam3"
    assert result["classes"] == ["person", "car"]


def test_facade_sam3_unloaded_raises_503(tmp_path, monkeypatch):
    svc = _make_service(tmp_path)
    monkeypatch.setattr(svc, "sam3_loaded", lambda: False)
    with pytest.raises(VideoTestError) as ei:
        svc.start_job({"video_name": "a.mp4", "engine": "sam3", "classes": "person"})
    assert ei.value.status == 503


def test_facade_sam3_missing_classes_raises_400(tmp_path, monkeypatch):
    svc = _make_service(tmp_path)
    monkeypatch.setattr(svc, "sam3_loaded", lambda: True)
    with pytest.raises(VideoTestError) as ei:
        svc.start_job({"video_name": "a.mp4", "engine": "sam3"})
    assert ei.value.status == 400


def test_facade_unknown_video_raises_400(tmp_path):
    svc = _make_service(tmp_path)
    with pytest.raises(VideoTestError) as ei:
        svc.start_job({"video_name": "missing.mp4", "engine": "yolo"})
    assert ei.value.status == 400


def test_facade_invalid_engine_raises_400(tmp_path):
    svc = _make_service(tmp_path)
    with pytest.raises(VideoTestError) as ei:
        svc.start_job({"video_name": "a.mp4", "engine": "nope"})
    assert ei.value.status == 400


# --- path containment ---

def test_is_within_rejects_traversal_and_absolute(tmp_path):
    base = tmp_path / "uploads"
    base.mkdir()
    assert _is_within(str(base), "../secret.txt") is None
    assert _is_within(str(base), "/etc/passwd") is None
    assert _is_within(str(base), "..") is None


def test_is_within_requires_existing_file(tmp_path):
    base = tmp_path / "uploads"
    base.mkdir()
    assert _is_within(str(base), "ghost.mp4") is None
    (base / "ok.mp4").write_bytes(b"x")
    assert _is_within(str(base), "ok.mp4") == str((base / "ok.mp4").resolve())


def test_resolve_video_searches_both_dirs_and_rejects_traversal(tmp_path):
    upload = tmp_path / "uploads"
    static = tmp_path / "static"
    upload.mkdir()
    static.mkdir()
    (upload / "u.mp4").write_bytes(b"x")
    (static / "s.mp4").write_bytes(b"x")
    svc = VideoInferenceService(upload_video_dir=str(upload), static_video_dir=str(static))
    assert svc.resolve_video("u.mp4") is not None
    assert svc.resolve_video("s.mp4") is not None
    assert svc.resolve_video("nope.mp4") is None
    assert svc.resolve_video("../evil.mp4") is None


def test_default_dirs_read_path_registry(tmp_path, monkeypatch):
    from app.common.config import PATHS

    upload = tmp_path / "u"
    upload.mkdir()
    (upload / "x.mp4").write_bytes(b"x")
    monkeypatch.setitem(PATHS, "video_uploads", str(upload))
    monkeypatch.setitem(PATHS, "video_static", str(tmp_path / "s"))
    svc = VideoInferenceService()
    assert svc.resolve_video("x.mp4") is not None
