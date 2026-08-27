"""VLM 大模型接入验收：vlm_service 解析/缩放 + 标注页引擎 + 视频页第三引擎。

外部依赖（vLLM HTTP 服务）全部 mock；真实端到端需 docker compose 启动服务后人工演示。
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugins.vlm_service import VlmService, _la_suspect, _parse_bbox_json, _parse_la_boxes


# ---------------------------------------------------------------------------
# 单元：解析与缩放
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_parse_bbox_json_tolerates_fence_and_chatter():
    raw = '好的，检测结果如下：\n```json\n[{"bbox_2d": [10, 20, 100, 200], "label": "car"}]\n```\n以上。'
    boxes = _parse_bbox_json(raw)
    assert len(boxes) == 1
    assert boxes[0]["bbox_2d"] == [10, 20, 100, 200]
    assert _parse_bbox_json("没有框") == []
    assert _parse_bbox_json("[{bad json]") == []

@pytest.mark.unit
def test_grounding_maps_normalized_coords(monkeypatch):
    import plugins.vlm_service as vlm_mod
    monkeypatch.setattr(vlm_mod, "COORD_MODE", "normalized")
    svc = VlmService()
    import numpy as np
    frame = np.zeros((720, 1280, 3), dtype="uint8")
    # 0-1000 归一化 [500,500,1000,1000] -> 原图 (640,360)-(1280,720)
    monkeypatch.setattr(svc, "_chat",
                        lambda content, model=None: '[{"bbox_2d": [500, 500, 1000, 1000], "label": "car"}]')
    out = svc.grounding(frame, ["car"], conf=0.35)
    assert len(out) == 1
    x1, y1, x2, y2 = out[0]["xyxy"]
    assert abs(x1 - 640) < 1 and abs(y1 - 360) < 1 and x2 == 1280 and y2 == 720


@pytest.mark.unit
def test_grounding_maps_coords_and_filters_labels(monkeypatch):
    svc = VlmService()
    frame = __import__("numpy").zeros((720, 1280, 3), dtype="uint8")
    monkeypatch.setattr(svc, "_chat",
                        lambda content, model=None: '[{"bbox_2d": [218, 194, 437, 388], "label": "car"}]')
    out = svc.grounding(frame, ["car"], conf=0.35)
    assert len(out) == 1
    x1, y1, x2, y2 = out[0]["xyxy"]
    assert 0 <= x1 < x2 <= 1280 and 0 <= y1 < y2 <= 720


# ---------------------------------------------------------------------------
# 标注页 VLM 引擎（API 级，仿 sam3 流）
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_vlm_annotate_single_persists_annotations(isolated_app, monkeypatch):
    from plugins.vlm_service import vlm_service
    monkeypatch.setattr(vlm_service, "ensure_backend", lambda model=None: {})
    monkeypatch.setattr(vlm_service, "is_available", lambda: True)
    monkeypatch.setattr(vlm_service, "detect_from_file",
                        lambda path, text=None, conf=0.35, model=None: [
                            {"class": "car", "conf": 0.9, "xyxy": [10.0, 10.0, 60.0, 60.0]}])
    client = isolated_app.test_client()
    # 造一张图
    import cv2
    import numpy as np
    img = np.zeros((100, 100, 3), dtype="uint8")
    cv2.imwrite(os.path.join(__import__("app").PATHS["uploads"], "a.jpg"), img)

    r = client.post("/api/ai-annotate-vlm", json={
        "image_name": "a.jpg", "target_classes": "car", "confidence": 0.35})
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] and body["engine"] == "vlm"
    assert len(body["annotations"]) == 1
    ann = body["annotations"][0]
    assert ann["type"] == "rectangle" and ann["auto"] is True
    assert ann["class"] == "car"
    # 单张契约同 sam3：结果返回前端，用户保存时才落盘；批量接口负责直接持久化


@pytest.mark.integration
def test_vlm_annotate_requires_classes_and_service(isolated_app, monkeypatch):
    from plugins.vlm_service import vlm_service
    monkeypatch.setattr(vlm_service, "ensure_backend", lambda model=None: {})
    monkeypatch.setattr(vlm_service, "is_available", lambda: False)
    # parse_target_classes 空输入回退本地类别表（SAM3 同款契约）：清空才能触发 400
    import app as _app
    open(_app.PATHS["classes"], "w", encoding="utf-8").write("[]")
    client = isolated_app.test_client()
    r = client.post("/api/ai-annotate-vlm", json={
        "image_name": "a.jpg", "target_classes": "car"})
    assert r.status_code == 503
    r = client.post("/api/ai-annotate-vlm", json={"image_name": "a.jpg"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# 视频页第三引擎
# ---------------------------------------------------------------------------

def _mock_video_externals(monkeypatch, job_id="vjob-x"):
    import app as training_app
    monkeypatch.setattr(training_app.video_inference_service, "start_job",
                        lambda *a, **k: {"id": job_id, "status": "running"})
    monkeypatch.setattr(
        "app.blueprints.video_test.resolve_video_path",
        lambda name: "/tmp/fake.mp4" if name == "fake.mp4" else None)


@pytest.mark.integration
def test_vlm_engine_params_and_validation(isolated_app, monkeypatch):
    from app.services.video_test_service import parse_video_test_params
    # vlm+detect 合法；vlm+depth_track 拒绝
    _, engine, mode, _, _ = parse_video_test_params({
        "video_name": "v.mp4", "engine": "vlm", "mode": "detect"})
    assert (engine, mode) == ("vlm", "detect")
    with pytest.raises(ValueError):
        parse_video_test_params({"video_name": "v.mp4", "engine": "vlm", "mode": "depth_track"})


@pytest.mark.integration
def test_vlm_video_start_requires_service_and_classes(isolated_app, monkeypatch):
    from plugins.vlm_service import vlm_service as _vlm
    _mock_video_externals(monkeypatch)
    monkeypatch.setattr(_vlm, "is_available", lambda: False)
    client = isolated_app.test_client()
    r = client.post("/api/video-test/start", json={
        "engine": "vlm", "target_fps": 2, "video_name": "fake.mp4", "classes": "car"})
    assert r.status_code == 503

    monkeypatch.setattr(_vlm, "is_available", lambda: True)
    r = client.post("/api/video-test/start", json={
        "engine": "vlm", "target_fps": 2, "video_name": "fake.mp4"})
    assert r.status_code == 400  # 缺类别
    r = client.post("/api/video-test/start", json={
        "engine": "vlm", "target_fps": 2, "video_name": "fake.mp4", "classes": "car"})
    assert r.status_code == 200


@pytest.mark.integration
def test_vlm_batch_mixed_results(isolated_app, monkeypatch):
    """batch: success rows keep annotations, failed rows report error.

    Regression for the 2-tuple/3-tuple mix that 500'd the whole batch when
    any image succeeded, and the UnboundLocalError on the retry fallback.
    """
    import os
    from plugins.vlm_service import vlm_service as _vlm
    monkeypatch.setattr(_vlm, "ensure_backend", lambda model=None: {})
    monkeypatch.setattr(_vlm, "is_available", lambda: True)

    from app.common.config import PATHS
    up = PATHS["uploads"]
    os.makedirs(up, exist_ok=True)
    with open(os.path.join(up, "b1.jpg"), "wb") as f:
        f.write(b"x")
    with open(os.path.join(up, "bad.jpg"), "wb") as f:
        f.write(b"x")

    def fake_detect(image_path, text=None, conf=0.35, model=None):
        if os.path.basename(image_path) == "bad.jpg":
            raise RuntimeError("boom")
        return [{"class": "car", "conf": 0.9, "xyxy": [1, 1, 50, 50]}]

    monkeypatch.setattr(_vlm, "detect_from_file", fake_detect)
    client = isolated_app.test_client()
    r = client.post("/api/ai-annotate-vlm-batch", json={
        "image_names": ["b1.jpg", "bad.jpg"], "target_classes": "car"})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    data = r.get_json()
    assert data["total_failed"] == 1
    ok = next(x for x in data["results"] if x["image_name"] == "b1.jpg")
    bad = next(x for x in data["results"] if x["image_name"] == "bad.jpg")
    assert ok["success"] is True and ok["count"] == 1

    assert bad["success"] is False



def test_parse_la_boxes_xyxy_and_suspect():
    """LA 输出按 x1,y1,x2,y2 解析；退化形态（全图框/空输出）被识别。"""
    boxes = _parse_la_boxes('<ref>car</ref><box><354><527><373><591></box>')
    assert boxes == [{"bbox_2d": [354, 527, 373, 591], "label": "car"}]

    # 全图退化框：单框覆盖 >=55% 判可疑
    assert _la_suspect(_parse_la_boxes('<ref>car</ref><box><0><0><998><999></box>'), 1280, 720)
    # 空输出判可疑
    assert _la_suspect([], 1280, 720)
    # 多框正常结果不触发重采样
    assert not _la_suspect(
        _parse_la_boxes('<ref>car</ref><box><0><0><100><100></box>'
                        '<ref>car</ref><box><500><500><600><600></box>'), 1280, 720)


def test_parse_la_boxes_inverted_coords_normalized():
    """坐标倒置时按轴归一化，仍产出合法 xyxy。"""
    boxes = _parse_la_boxes('<ref>car</ref><box><800><600><200><300></box>')
    assert boxes == [{"bbox_2d": [200, 300, 800, 600], "label": "car"}]
