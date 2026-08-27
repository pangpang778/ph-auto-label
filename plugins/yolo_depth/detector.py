"""检测器适配器：YOLO / SAM3。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np


class YoloTrackDetector:
    """YOLO 检测+跟踪一体化：直接调用 model.track，返回带 track_id 的检测框。"""

    def __init__(self, model: Any, classes: Optional[List[int]] = None) -> None:
        self.model = model
        self.classes = classes

    def detect(self, frame: np.ndarray, conf: float) -> List[Dict[str, Any]]:
        kwargs = {"persist": True, "conf": conf, "verbose": False}
        if self.classes is not None:
            kwargs["classes"] = self.classes
        results = self.model.track(frame, **kwargs)
        if not results:
            return []
        r = results[0]
        boxes = getattr(r, "boxes", None)
        if boxes is None or boxes.xyxy is None:
            return []
        names = getattr(r, "names", {}) or {}
        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy() if boxes.conf is not None else []
        clss = boxes.cls.cpu().numpy().astype(int) if boxes.cls is not None else []
        ids = boxes.id
        ids = ids.cpu().numpy().astype(int) if ids is not None else np.arange(len(xyxy))
        out: List[Dict[str, Any]] = []
        for i in range(len(xyxy)):
            cid = int(clss[i]) if i < len(clss) else -1
            cname = names.get(cid, str(cid)) if isinstance(names, dict) else str(cid)
            out.append({
                "xyxy": [float(v) for v in xyxy[i]],
                "class": str(cname),
                "conf": float(confs[i]) if i < len(confs) else 0.0,
                "track_id": int(ids[i]) if i < len(ids) else i,
            })
        return out


class Sam3Detector:
    """SAM3 开放词汇检测器，通过文本提示检测任意类别。"""

    def __init__(self, service: Any) -> None:
        self.service = service
        self.classes: List[str] = []

    def set_classes(self, classes: List[str]) -> None:
        self.classes = [c.strip() for c in classes if c.strip()]

    def detect(self, frame: np.ndarray, conf: float) -> List[Dict[str, Any]]:
        if not self.classes:
            return []
        return self.service.detect_frame(frame, text=self.classes, conf=conf)
