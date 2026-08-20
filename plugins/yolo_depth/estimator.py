"""车辆深度跟踪引擎 — 每目标测距 + Δd/Δt 测速（纯逻辑，可脱离模型单测）。

移植自 yolo-vehicle-depth-track/speed/demo_video.py 的核心算法：
- box_distance: 对跟踪框下半中区域取单目深度图的中值作为相机到车距离（米）。
- VehicleDepthEstimator: 按 track_id 记录距离，用窗口两端 + 分段瞬时中值估径向速度。
"""

from __future__ import annotations

from collections import defaultdict, deque

import numpy as np

# 车辆类别（COCO）：car, motorcycle, bus, truck
VEHICLE_IDS = (2, 3, 5, 7)
VEHICLE_NAMES = {2: "car", 3: "moto", 5: "bus", 7: "truck"}


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


class VehicleDepthEstimator:
    """按 track_id 记录距离，用 Δd/Δt 估速度，并做滑动平均平滑。

    速度符号：正=远离相机，负=靠近相机。显示用绝对值 + 方向文案。
    """

    def __init__(self, window: int = 8, min_dt: float = 0.08):
        self.window = window
        self.min_dt = min_dt
        self._hist: dict[int, deque[tuple[float, float]]] = defaultdict(
            lambda: deque(maxlen=window)
        )

    def update(self, track_id: int, t_sec: float, dist_m: float | None) -> tuple[float | None, float | None, str]:
        """返回 (距离m, 速度km/h, 方向文案)。样本不足/速度过小时速度与方向为空/空串。"""
        if dist_m is None:
            return None, None, ""

        q = self._hist[track_id]
        q.append((t_sec, dist_m))
        if len(q) < 2:
            return dist_m, None, ""

        items = list(q)
        # 分段瞬时速度 → 中值（抗抖动）
        inst = []
        for i in range(1, len(items)):
            dti = items[i][0] - items[i - 1][0]
            if dti >= 1e-3:
                inst.append((items[i][1] - items[i - 1][1]) / dti)
        if not inst:
            return dist_m, None, ""
        speed_mps = float(np.median(inst))

        speed_kmh = speed_mps * 3.6
        if abs(speed_kmh) < 1.0:
            direction = "静止/缓行"
        elif speed_kmh > 0:
            direction = "远离"
        else:
            direction = "靠近"
        return dist_m, speed_kmh, direction


def build_vd_label(track_id: int, name: str, dist_m: float | None,
                   speed_kmh: float | None, direction: str) -> str:
    """拼源风格标签：ID8 car 4.5m 18km/h 靠近。"""
    parts = [f"ID{track_id}", name]
    if dist_m is not None:
        parts.append(f"{dist_m:.1f}m")
    if speed_kmh is not None:
        parts.append(f"{abs(speed_kmh):.0f}km/h")
        if direction:
            parts.append(direction)
    return " ".join(parts)