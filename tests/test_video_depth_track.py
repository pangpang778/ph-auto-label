"""Generic depth-tracking (engine=detector, mode=depth_track) unit/service tests.

Covers the API parameter contract and the depth-pipeline component selection
without spinning up real YOLO/SAM3/Depth Anything inference (no GPU needed).
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.video_test_service import parse_video_test_params  # noqa: E402
from plugins.video_inference import VideoInferenceService  # noqa: E402
from plugins.yolo_depth.detector import YoloTrackDetector, Sam3Detector  # noqa: E402
from plugins.yolo_depth.tracker import ByteTrackTracker  # noqa: E402


# ---------------------------------------------------------------------------
# parse_video_test_params: engine split from mode
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_parse_params_accepts_detect_mode_default():
    # detect is the default task mode, even if omitted
    name, engine, mode, fps, conf = parse_video_test_params(
        {"video_name": "a.mp4", "engine": "yolo"})
    assert (engine, mode) == ("yolo", "detect")
    assert conf == 0.35


@pytest.mark.unit
def test_parse_params_accepts_yolo_and_sam3_with_depth_track():
    for engine in ("yolo", "sam3"):
        _, e, mode, _, _ = parse_video_test_params(
            {"video_name": "a.mp4", "engine": engine, "mode": "depth_track"})
        assert (e, mode) == (engine, "depth_track")


@pytest.mark.unit
def test_parse_params_rejects_legacy_vehicle_depth_engine():
    with pytest.raises(ValueError):
        parse_video_test_params(
            {"video_name": "a.mp4", "engine": "vehicle-depth", "mode": "detect"})


@pytest.mark.unit
def test_parse_params_rejects_unknown_mode():
    with pytest.raises(ValueError):
        parse_video_test_params(
            {"video_name": "a.mp4", "engine": "yolo", "mode": "zoom"})


# ---------------------------------------------------------------------------
# start_job: job dict carries engine + mode with correct state
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_start_job_depth_track_dict(monkeypatch):
    # Prevent the background thread from actually running inference
    monkeypatch.setattr("threading.Thread.start", lambda self: None)
    svc = VideoInferenceService()
    job = svc.start_job("/tmp/nonexistent.mp4", engine="yolo", mode="depth_track", conf=0.4)
    assert job["engine"] == "yolo"
    assert job["mode"] == "depth_track"
    assert job["status"] == "queued"
    assert job["conf"] == 0.4
    # start_stream_session carries the same mode through its session dict
    session = svc.start_stream_session("/tmp/nonexistent.mp4", engine="sam3", mode="depth_track")
    sid = session["session_id"]
    assert svc.get_session(sid)["engine"] == "sam3"


@pytest.mark.unit
def test_stop_job_sets_stopping_state_and_stop_flag(monkeypatch):
    monkeypatch.setattr("threading.Thread.start", lambda self: None)
    svc = VideoInferenceService()
    job = svc.start_job("/tmp/nonexistent.mp4", engine="yolo")

    result = svc.stop_job(job["id"])

    assert result == {"stopped": True, "status": "stopping"}
    assert svc.get_job(job["id"])["status"] == "stopping"
    assert svc._job_stop_flags[job["id"]].is_set()


@pytest.mark.unit
def test_stop_job_returns_not_found_for_unknown_job():
    svc = VideoInferenceService()

    assert svc.stop_job("vjob_missing") is None


# ---------------------------------------------------------------------------
# _get_depth_pipeline: detector/tracker selection per engine
# ---------------------------------------------------------------------------

class _FakeYolo:
    pass


@pytest.mark.unit
def test_depth_pipeline_yolo_uses_track_detector_no_tracker():
    svc = VideoInferenceService()
    det, tracker, depth, est = svc._get_depth_pipeline(
        "k", engine="yolo", yolo_model=_FakeYolo(), classes=[])
    assert isinstance(det, YoloTrackDetector)
    assert tracker is None
    assert est is not None


@pytest.mark.unit
def test_depth_pipeline_sam3_uses_tracker(monkeypatch):
    svc = VideoInferenceService()
    # SAM3 detector requires the sam3_service singleton; stub out its tracker.
    monkeypatch.setattr(ByteTrackTracker, "__init__", lambda self, args=None: None)
    det, tracker, depth, est = svc._get_depth_pipeline(
        "k", engine="sam3", yolo_model=None, classes=["person"])
    assert isinstance(det, Sam3Detector)
    assert isinstance(tracker, ByteTrackTracker)


@pytest.mark.unit
def test_bytetrack_tracker_initializes_with_ultralytics_defaults():
    tracker = ByteTrackTracker()

    native_tracker = tracker._get_tracker()

    assert native_tracker.max_frames_lost == 30
    assert tracker.args.track_high_thresh == 0.25
    assert tracker.args.fuse_score is True


@pytest.mark.unit
def test_bytetrack_tracker_accepts_detection_boxes():
    tracker = ByteTrackTracker()
    frame = np.zeros((240, 320, 3), dtype=np.uint8)

    tracks = tracker.track(frame, [{
        "xyxy": [20, 20, 120, 120],
        "conf": 0.9,
        "class": "person",
    }])

    assert len(tracks) == 1
    assert tracks[0]["track_id"] >= 1


@pytest.mark.unit
def test_bytetrack_tracker_preserves_id_across_detection_gap():
    """空帧不重置 tracker：检测空窗后同一目标仍保持原 track_id。"""
    tracker = ByteTrackTracker()
    frame = np.zeros((240, 320, 3), dtype=np.uint8)

    first = tracker.track(frame, [{
        "xyxy": [20, 20, 120, 120],
        "conf": 0.9,
        "class": "person",
    }])
    # 空帧：tracker 实例必须存活，轨迹自然老化
    assert tracker.track(frame, []) == []
    assert tracker._tracker is not None
    second = tracker.track(frame, [{
        "xyxy": [22, 22, 122, 122],
        "conf": 0.9,
        "class": "person",
    }])

    assert len(second) == 1
    assert second[0]["track_id"] == first[0]["track_id"]


@pytest.mark.unit
def test_build_decode_cmd_contract():
    """decode cmd MUST end with pipe:1 (regression: lost output target broke
    every offline job) and stride>1 adds select+vfr."""
    from plugins.video_inference import build_decode_cmd

    c1 = build_decode_cmd("v.mp4", 1)
    assert c1[-1] == "pipe:1"
    assert "-vf" not in c1
    assert "-i" in c1 and "rawvideo" in c1

    c6 = build_decode_cmd("v.mp4", 6)
    assert c6[-1] == "pipe:1"
    assert any(str(a).startswith("select=not(mod(n") for a in c6)
    assert "vfr" in c6
