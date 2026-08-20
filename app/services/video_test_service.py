"""Video-test domain service.

Thin helper for the video-test blueprint; the route handlers delegate inference
to plugins.video_inference directly.
"""

from app.repositories.model_registry_repo import get_active_model, get_models_dir  # noqa: F401  (re-exported for blueprint)


def _parse_classes(raw) -> list:
    """把 SAM3 目标类别从 list/str 解析成干净的字符串列表。"""
    if isinstance(raw, list):
        return [str(c).strip() for c in raw if str(c).strip()]
    if isinstance(raw, str):
        s = raw.replace('，', ',').replace('\n', ',').replace(';', ',')
        return [c.strip() for c in s.split(',') if c.strip()]
    return []


def parse_video_test_params(data):
    """Parse + validate video-test request params. Raises ``ValueError`` on invalid.

    Returns ``(name, engine, target_fps, conf)``. The handler resolves the video
    path and performs engine-specific checks (SAM3 loaded / classes) afterward.
    """
    name = (data.get('video_name') or '').strip()
    engine = (data.get('engine') or 'yolo').strip().lower()
    try:
        target_fps = int(data.get('target_fps', 2))
    except (TypeError, ValueError):
        target_fps = 2
    try:
        conf = float(data.get('confidence', 0.35))
    except (TypeError, ValueError):
        conf = 0.35
    if engine not in ('yolo', 'sam3', 'vehicle-depth'):
        raise ValueError('引擎必须是 yolo、sam3 或 vehicle-depth')
    if target_fps not in (1, 2, 5):
        raise ValueError('帧率仅支持 1/2/5')
    return name, engine, target_fps, conf
