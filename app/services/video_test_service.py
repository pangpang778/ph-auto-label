"""Video-test domain service.

Thin helper for the video-test blueprint; the route handlers delegate inference
to plugins.video_inference directly.
"""


def _parse_classes(raw) -> list:
    """把 SAM3 目标类别从 list/str 解析成干净的字符串列表。"""
    if isinstance(raw, list):
        return [str(c).strip() for c in raw if str(c).strip()]
    if isinstance(raw, str):
        s = raw.replace('，', ',').replace('\n', ',').replace(';', ',')
        return [c.strip() for c in s.split(',') if c.strip()]
    return []
