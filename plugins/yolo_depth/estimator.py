"""深度跟踪测距测速模块 — 每目标测距 + Δd/Δt 测速（纯逻辑，可脱离模型单测）。

移植自 yolo-vehicle-depth-track/speed/demo_video.py 的核心算法：
- box_distance: 对跟踪框下半中区域取单目深度图的中值作为目标距离（米）。
- VehicleDepthEstimator: 按 track_id 记录距离，用窗口两端 + 分段瞬时中值估径向速度。
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Optional

import numpy as np


def box_distance(depth_m: np.ndarray, xyxy) -> float | None:
    """取框下半中区域的深度中值作为距离。depth 无效/越界返回 None。"""
    h, w = depth_m.shape[:2]
    x1, y1, x2, y2 = map(int, xyxy)
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return None

    bw, bh = x2 - x1, y2 - y1
    patch = depth_m[
        y1 + int(bh * 0.45): y1 + int(bh * 0.90),
        x1 + int(bw * 0.25): x1 + int(bw * 0.75),
    ]
    patch = patch[np.isfinite(patch) & (patch > 0)]
    if patch.size < 10:
        patch = depth_m[y1:y2, x1:x2]
        patch = patch[np.isfinite(patch) & (patch > 0)]
    if patch.size == 0:
        return None
    return float(np.median(patch))


# Direction-shift thresholds as frame-height fractions (12px/24px at 720p),
# resolution independent by construction.
_MIN_CENTER_SHIFT = 12.0 / 720.0
_STRONG_CENTER_SHIFT = 24.0 / 720.0


class VehicleDepthEstimator:
    """按 track_id 记录距离，用稳定时间窗口估计径向运动。

    Depth Anything 输出的是相对深度，逐帧数值会有尺度漂移。因此有检测框时，
    方向优先依据目标框的稳定尺度变化：框变大表示靠近，框变小表示远离；
    深度趋势只用于速度幅值。没有检测框时保留深度差分兼容行为。
    """

    def __init__(
        self,
        window: int = 30,
        min_dt: float = 0.5,
        stationary_speed_kmh: float = 1.0,
        min_box_change: float = 0.04,
    ):
        self.window = window
        self.min_dt = min_dt
        self.stationary_speed_kmh = stationary_speed_kmh
        self.min_box_change = min_box_change
        self._hist: dict[
            int, deque[tuple[float, float, Optional[float], Optional[float], bool]]
        ] = defaultdict(lambda: deque(maxlen=window))

    def update(
        self,
        track_id: int,
        t_sec: float,
        dist_m: float | None,
        xyxy=None,
        frame_shape=None,
    ) -> tuple[float | None, float | None, str]:
        """返回 (距离, 速度, 方向)，并过滤单目深度和检测框抖动。"""
        if dist_m is None:
            return None, None, ""

        q = self._hist[track_id]
        cy = _box_center_y(xyxy)
        fh = float(frame_shape[0]) if frame_shape is not None else 0.0
        q.append((
            t_sec,
            dist_m,
            _box_scale(xyxy),
            (cy / fh) if (cy is not None and fh > 0) else cy,
            _box_is_clipped(xyxy, frame_shape),
        ))
        if len(q) < 2:
            return dist_m, None, ""

        items = list(q)
        depth_speed_kmh = self._depth_speed(items)
        if depth_speed_kmh is None:
            return dist_m, None, ""

        box_trend = self._box_trend(items)
        if box_trend is not None:
            if box_trend > 0:
                direction = "靠近"
            elif box_trend < 0:
                direction = "远离"
            else:
                return dist_m, 0.0, "静止/缓行"

            # Relative depth is not reliable enough to determine the sign, but
            # its magnitude is still useful when it agrees with a real motion.
            magnitude = abs(depth_speed_kmh)
            speed_kmh = magnitude if direction == "远离" else -magnitude
            if magnitude < self.stationary_speed_kmh:
                speed_kmh = None
            return dist_m, speed_kmh, direction

        # Callers without a box retain the original depth-only convention.
        if abs(depth_speed_kmh) < self.stationary_speed_kmh:
            direction = "静止/缓行"
        elif depth_speed_kmh > 0:
            direction = "远离"
        else:
            direction = "靠近"
        return dist_m, depth_speed_kmh, direction

    def _depth_speed(
        self,
        items: list[tuple[float, float, Optional[float], Optional[float], bool]],
    ) -> float | None:
        """Estimate depth speed from samples separated by a real time baseline."""
        current = items[-1]
        reference = [item for item in items[:-1] if current[0] - item[0] >= self.min_dt]
        if not reference:
            return None

        # Median endpoints make the estimate tolerant to one bad depth patch.
        old = reference[-min(3, len(reference)):]
        recent = items[-min(3, len(items)):]
        old_t = float(np.median([item[0] for item in old]))
        new_t = float(np.median([item[0] for item in recent]))
        dt = new_t - old_t
        if dt < self.min_dt * 0.8:
            return None
        old_d = float(np.median([item[1] for item in old]))
        new_d = float(np.median([item[1] for item in recent]))
        return (new_d - old_d) / dt * 3.6

    def _box_trend(
        self,
        items: list[tuple[float, float, Optional[float], Optional[float], bool]],
    ) -> int | None:
        """Combine box scale and vertical movement into a radial direction signal."""
        valid = [item for item in items if item[2] is not None or item[3] is not None]
        if len(valid) < 2:
            return None

        current_t = valid[-1][0]
        reference = [item for item in valid[:-1] if current_t - item[0] >= self.min_dt]
        if not reference:
            return None

        old = reference[-min(3, len(reference)):]
        recent = valid[-min(3, len(valid)):]
        old_scale = _median_optional(old, 2)
        new_scale = _median_optional(recent, 2)
        old_center = _median_optional(old, 3)
        new_center = _median_optional(recent, 3)

        size_direction = 0
        relative_change = 0.0
        if old_scale is not None and new_scale is not None and old_scale > 0:
            relative_change = new_scale / old_scale - 1.0
            if abs(relative_change) >= self.min_box_change:
                size_direction = 1 if relative_change > 0 else -1

        # A box touching the bottom edge is artificially shortened as the
        # vehicle leaves the image. Ignore scale shrinkage while clipped.
        if any(item[4] for item in recent) and size_direction < 0:
            size_direction = 0

        center_direction = 0
        center_change = 0.0
        if old_center is not None and new_center is not None:
            center_change = new_center - old_center
            if abs(center_change) >= _MIN_CENTER_SHIFT:
                # Image y grows downwards: down means nearer to a fixed camera.
                center_direction = 1 if center_change > 0 else -1

        if size_direction == 0 and center_direction == 0:
            return 0
        if size_direction == 0:
            return center_direction
        if center_direction == 0 or size_direction == center_direction:
            return size_direction

        # At the image boundary, a growing vehicle can have a shrinking clipped
        # box. Trust a strong center movement when the scale conflict is small.
        if abs(center_change) >= _STRONG_CENTER_SHIFT and abs(relative_change) < self.min_box_change * 2:
            return center_direction
        return size_direction


def _box_scale(xyxy) -> Optional[float]:
    """Use square-root box area as a size signal, reducing width/height noise."""
    if xyxy is None:
        return None
    try:
        x1, y1, x2, y2 = (float(value) for value in xyxy)
    except (TypeError, ValueError):
        return None
    width, height = x2 - x1, y2 - y1
    if width <= 0 or height <= 0:
        return None
    return float(np.sqrt(width * height))


def _box_center_y(xyxy) -> Optional[float]:
    if xyxy is None:
        return None
    try:
        _, y1, _, y2 = (float(value) for value in xyxy)
    except (TypeError, ValueError):
        return None
    return (y1 + y2) / 2.0


def _box_is_clipped(xyxy, frame_shape) -> bool:
    if xyxy is None or frame_shape is None:
        return False
    try:
        x1, y1, x2, y2 = (float(value) for value in xyxy)
        height, width = (float(value) for value in frame_shape[:2])
    except (TypeError, ValueError, IndexError):
        return False
    edge = 1.0
    return x1 <= edge or y1 <= edge or x2 >= width - edge or y2 >= height - edge


def _median_optional(items, index: int) -> Optional[float]:
    values = [item[index] for item in items if item[index] is not None]
    return float(np.median(values)) if values else None


def build_vd_label(track_id: int, name: str, dist_m: float | None,
                   speed_kmh: float | None, direction: str,
                   show_dist: bool = True) -> str:
    """拼源风格标签：ID8 car 4.5m 18km/h 靠近。

    相对深度模型输出的数值不是真实米数，show_dist=False 时省略距离段。
    """
    parts = [f"ID{track_id}", name]
    if dist_m is not None and show_dist:
        parts.append(f"{dist_m:.1f}m")
    if speed_kmh is not None:
        parts.append(f"{abs(speed_kmh):.0f}km/h")
    if direction:
        parts.append(direction)
    return " ".join(parts)
