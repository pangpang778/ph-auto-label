"""跟踪器适配器：ByteTrack。"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

import numpy as np
from ultralytics.trackers import BYTETracker

logger = logging.getLogger(__name__)


class ByteTrackTracker:
    """基于独立 BYTETracker 的跟踪器，接受任意检测框输入。"""

    def __init__(self, args: Any = None) -> None:
        if args is None:
            class _Args:
                tracker = "bytetrack.yaml"
                track_high_thresh = 0.25
                track_low_thresh = 0.1
                new_track_thresh = 0.25
                track_buffer = 30
                match_thresh = 0.8
                fuse_score = True
                conf_thres = 0.35
                iou_thres = 0.5
                max_det = 300
                classes = None
                verbose = False
                device = "cpu"
            args = _Args()
        self.args = args
        self._tracker: Any = None

    def _get_tracker(self) -> Any:
        if self._tracker is None:
            self._tracker = BYTETracker(args=self.args)
        return self._tracker

    def track(self, frame: np.ndarray, detections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not detections:
            # 空帧不重置 tracker：喂空检测让 BYTETracker 自然老化轨迹
            # （track_buffer=30 正是为跨越检测空窗而设），重置会破坏稳定 ID。
            if self._tracker is None:
                return []
            import torch
            from ultralytics.engine.results import Boxes

            results = Boxes(torch.zeros((0, 6)), orig_shape=frame.shape[:2])
            self._tracker.update(results, frame)
            return []

        import torch
        from ultralytics.engine.results import Boxes

        dets = np.array([[d["xyxy"][0], d["xyxy"][1], d["xyxy"][2], d["xyxy"][3],
                          d.get("conf", 1.0), 0.0] for d in detections], dtype=np.float32)
        dets_t = torch.from_numpy(dets)

        # BYTETracker consumes the Boxes-like interface directly in current
        # Ultralytics releases (conf/cls/xywh), not the outer Results object.
        results = Boxes(dets_t, orig_shape=frame.shape[:2])
        tracker = self._get_tracker()
        tracked = tracker.update(results, frame)

        out: List[Dict[str, Any]] = []
        if hasattr(tracked, "boxes"):
            if tracked.boxes is None or tracked.boxes.id is None:
                return out
            tracked_rows = np.column_stack([
                tracked.boxes.xyxy.cpu().numpy(),
                tracked.boxes.id.cpu().numpy(),
                tracked.boxes.conf.cpu().numpy(),
                tracked.boxes.cls.cpu().numpy(),
            ])
        else:
            tracked_rows = np.asarray(tracked)
        if tracked_rows.size == 0:
            return out

        # BYTETracker.update row layout pinned to current ultralytics:
        # [x1,y1,x2,y2, id, conf, cls, det_idx]; revisit on upgrade.
        IDX_ID, IDX_DET = 4, 7
        for row in tracked_rows:
            box = row[:4]
            tid = int(row[IDX_ID]) if len(row) > IDX_ID else -1
            source_index = int(row[IDX_DET]) if len(row) > IDX_DET else -1
            if not (0 <= source_index < len(detections)):
                logger.warning(
                    "track row det_idx out of range (%s); attributing detections[0]",
                    source_index)
                source_index = 0
            source = detections[source_index]
            out.append({
                "xyxy": [float(v) for v in box],
                "class": source["class"],
                "conf": float(row[5]) if len(row) > 5 else source.get("conf", 1.0),
                "track_id": tid,
            })
        return out
