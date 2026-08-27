"""Video-test domain service.

Thin helper for the video-test blueprint; the route handlers delegate inference
to plugins.video_inference directly.
"""

import os

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

    Returns ``(name, engine, mode, target_fps, conf)``. The handler resolves the video
    path and performs engine-specific checks (SAM3 loaded / classes) afterward.
    """
    name = (data.get('video_name') or '').strip()
    engine = (data.get('engine') or 'yolo').strip().lower()
    mode = (data.get('mode') or 'detect').strip().lower()
    try:
        target_fps = int(data.get('target_fps', 2))
    except (TypeError, ValueError):
        target_fps = 2
    try:
        conf = float(data.get('confidence', 0.35))
    except (TypeError, ValueError):
        conf = 0.35
    if engine not in ('yolo', 'sam3', 'vlm'):
        raise ValueError('引擎必须是 yolo/sam3/vlm')
    if mode not in ('detect', 'depth_track'):
        raise ValueError('任务模式必须是 detect 或 depth_track')
    if engine == 'vlm' and mode != 'detect':
        raise ValueError('VLM 引擎暂只支持普通检测模式（无跟踪 ID）')
    if target_fps not in (1, 2, 5):
        raise ValueError('帧率仅支持 1/2/5')
    return name, engine, mode, target_fps, conf


def resolve_depth_model(data):
    """校验 depth_track 模式的可选深度模型参数（工单 06）。

    返回 None（非 depth_track），或
    ``{"id", "weights_path", "metric", "show_meters"}``。非法 id 抛 ValueError（→400）。
    """
    mode = str(data.get('mode') or 'detect').strip().lower()
    if mode != 'depth_track':
        return None
    from plugins.yolo_depth.depth_models import BUILTIN_DEPTH_MODELS, DEFAULT_DEPTH_MODEL
    from app.services.models_service import list_models_by_kind

    depth_model = str(data.get('depth_model') or DEFAULT_DEPTH_MODEL).strip()
    metric_flags = {mid: info["metric"] for mid, info in BUILTIN_DEPTH_MODELS.items()}
    weights_path = None
    if depth_model not in metric_flags:
        rec = next((m for m in list_models_by_kind("depth") if m.get("id") == depth_model), None)
        if rec is None:
            raise ValueError(f'未知的深度模型: {depth_model}')
        weights_path = rec.get("path") or ""
        if not os.path.isfile(weights_path):
            raise ValueError(f'深度模型权重不存在: {depth_model}')
        metric_flags[depth_model] = True

    show_raw = data.get('show_meters')
    show_meters = True if show_raw is None else bool(show_raw)
    metric = bool(metric_flags[depth_model])
    # 相对深度无米可显：强制关闭（前端开关对相对模型禁用，后端兜底）
    return {"id": depth_model, "weights_path": weights_path,
            "metric": metric, "show_meters": metric and show_meters}
