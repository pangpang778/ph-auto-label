"""深度估计器适配器。"""
from __future__ import annotations

from typing import Any, Optional

import cv2
import numpy as np


class DepthAnythingDepthEstimator:
    """基于 Depth Anything v2 的通用单目深度估计器。

    使用 transformers 的 depth-estimation pipeline，首次使用会从 HuggingFace
    自动下载权重。输出为相对深度图，形状与输入帧一致。
    """

    def __init__(self, model_name: str = "depth-anything/Depth-Anything-V2-Small-hf",
                 device: Optional[str | int] = None) -> None:
        self.model_name = model_name
        self.device = device
        self._pipe: Any = None

    def _load(self) -> Any:
        if self._pipe is None:
            from transformers import pipeline
            import torch
            device = self.device
            if device is None and torch.cuda.is_available():
                device = 0
            try:
                self._pipe = pipeline(
                    "depth-estimation",
                    model=self.model_name,
                    device=device,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"无法加载深度模型 {self.model_name}，请检查网络连接或手动下载权重: {exc}"
                ) from exc
        return self._pipe

    def estimate(self, frame: np.ndarray) -> np.ndarray:
        from PIL import Image
        pipe = self._load()
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        result = pipe(img)
        value = result["predicted_depth"]
        if hasattr(value, "detach"):
            value = value.detach().cpu().numpy()
        depth = np.asarray(value)
        if depth.ndim == 3:
            depth = np.squeeze(depth, axis=0)
        if depth.shape[:2] != frame.shape[:2]:
            depth = cv2.resize(depth, (frame.shape[1], frame.shape[0]), interpolation=cv2.INTER_LINEAR)
        return depth
