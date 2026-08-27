"""通用深度跟踪管线：检测器 + 跟踪器 + 深度估计器。"""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from plugins.yolo_depth.estimator import VehicleDepthEstimator, box_distance, build_vd_label
from plugins.yolo_depth.text_cn import color_for_label


class DepthTrackingPipeline:
    """组合检测、跟踪、深度估计，输出带 ID/距离/速度的标签。"""

    def __init__(
        self,
        detector: Any,
        tracker: Any,
        depth_estimator: Any,
        estimator: VehicleDepthEstimator | None = None,
    ) -> None:
        self.detector = detector
        self.tracker = tracker
        self.depth_estimator = depth_estimator
        self.estimator = estimator or VehicleDepthEstimator()

    def infer(self, frame: np.ndarray, conf: float, t_sec: float,
              show_dist: bool = True) -> List[Dict[str, Any]]:
        detections = self.detector.detect(frame, conf)
        # 检测器已带 track_id 时跳过跟踪器（YOLO track 模式）
        if detections and all("track_id" in d for d in detections):
            tracks = detections
        elif self.tracker is not None:
            tracks = self.tracker.track(frame, detections)
        else:
            tracks = detections

        if not tracks:
            return []

        depth_m = self.depth_estimator.estimate(frame)
        out: List[Dict[str, Any]] = []
        for tr in tracks:
            box = tr["xyxy"]
            dist = box_distance(depth_m, box)
            track_id = int(tr.get("track_id", 0))
            dist_s, speed_kmh, direction = self.estimator.update(
                track_id, t_sec, dist, xyxy=box, frame_shape=frame.shape[:2]
            )
            label = build_vd_label(track_id, tr["class"], dist_s, speed_kmh, direction,
                                   show_dist=show_dist)
            out.append({
                "class": tr["class"],
                "conf": tr.get("conf", conf),
                "xyxy": box,
                "vd_label": label,
                "vd_color": color_for_label(f"id{track_id}"),
            })
        return out
