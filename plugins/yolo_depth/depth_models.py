"""深度模型注册表：内置估计器 + 自训练学生动态注入。

工单 06 契约：REGISTRY 内置 depth_anything_v2_small（相对深度，现状不动）
+ moge2_vitl（metric 米数）；自训练学生从 models 注册表 kind=depth 条目
动态注入（条目由调用方传入 —— plugins 层禁止反向依赖 app 包）。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# 内置深度模型：id -> 展示信息；metric=True 表示输出绝对米数
BUILTIN_DEPTH_MODELS = {
    "depth_anything_v2_small": {"label": "Depth Anything v2 Small（相对深度）", "metric": False},
    "moge2_vitl": {"label": "MoGe-2 ViT-L（绝对深度·米）", "metric": True},
}

DEFAULT_DEPTH_MODEL = "depth_anything_v2_small"

# MoGe 教师本地权重（proto 验证过的 1.3GB 文件）；缺失时回退 HF 在线下载。
# 可用 MOGE_WEIGHTS_PATH 环境变量显式指定（优先级最高），来源始终打日志。
_LOCAL_TEACHER_WEIGHTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".scratch", "trainable-depth", "proto", "weights", "moge-2-vitl-model.pt",
)


def _resolve_teacher_weights() -> Optional[str]:
    """教师权重来源：MOGE_WEIGHTS_PATH env > 本地缓存 > None(HF 在线下载)。"""
    env = os.environ.get("MOGE_WEIGHTS_PATH", "").strip()
    if env:
        if os.path.isfile(env):
            return env
        logger.warning("MOGE_WEIGHTS_PATH 指向的文件不存在，忽略: %s", env)
    if os.path.isfile(_LOCAL_TEACHER_WEIGHTS):
        return _LOCAL_TEACHER_WEIGHTS
    return None

# 学生 checkpoint 元数据里的架构标识
TEACHER_MODEL_ID = "moge2_vitl"
STUDENT_ARCH = "mbv3_unet_384"


def list_depth_models(registry_entries: Optional[list] = None) -> list[dict]:
    """下拉条目：内置 2 项 + 注册表 kind=depth 条目（权重文件存在时）。"""
    entries = [
        {"id": mid, "label": info["label"], "metric": info["metric"], "source": "builtin"}
        for mid, info in BUILTIN_DEPTH_MODELS.items()
    ]
    for rec in registry_entries or []:
        if not (rec.get("id") and os.path.isfile(rec.get("path") or "")):
            continue  # ponytail: 权重丢失的注册项跳过，避免坏选项
        entries.append({
            "id": str(rec["id"]),
            "label": str(rec.get("version") or "") + " 深度蒸馏（自训练）",
            "metric": True,
            "source": "trained",
            # server-internal path is resolved via the registry on selection;
            # never expose absolute filesystem layout over the API.
            "path": None,
            "version": rec.get("version", ""),
        })
    return entries


def create_depth_estimator(model_id: str, weights_path: Optional[str] = None,
                           device: Optional[Any] = None) -> Any:
    """按 id 构造估计器。未知 id 或权重缺失抛 ValueError。"""
    if model_id == DEFAULT_DEPTH_MODEL:
        from plugins.yolo_depth.depth_estimator import DepthAnythingDepthEstimator
        return DepthAnythingDepthEstimator(device=device)
    if model_id == "moge2_vitl":
        return MoGeDepthEstimator(weights_path=_resolve_teacher_weights(), device=device)
    # 自训练学生：weights_path 由调用方提供（来自注册表 record.path）
    if not weights_path or not os.path.isfile(weights_path):
        raise ValueError(f"未知或权重缺失的深度模型: {model_id}")
    return TrainedDepthEstimator(weights_path, device=device)


class MoGeDepthEstimator:
    """MoGe-2 metric 深度估计器（米）。需要 moge 包（懒加载，缺包给清晰报错）。"""

    def __init__(self, weights_path: Optional[str] = None,
                 device: Optional[Any] = None) -> None:
        self.weights_path = weights_path
        self.device = device
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            try:
                from moge.model.v2 import MoGeModel  # type: ignore
            except ImportError as exc:
                raise RuntimeError(
                    'MoGe 缺包：pip install "moge @ git+https://github.com/microsoft/MoGe.git"'
                    f"（含 fork 版 utils3d_moge）: {exc}"
                ) from exc
            import torch
            src = self.weights_path or "Ruicheng/moge-2-vitl"
            logger.info("MoGe 权重来源: %s", src)
            model = MoGeModel.from_pretrained(src)
            if torch.cuda.is_available():
                model = model.cuda().eval()
            else:
                model = model.eval()
            self._model = model
        return self._model

    def ensure_loaded(self):
        """Public warmup hook: surfaces missing deps/weights early."""
        self._load()

    def estimate(self, frame: np.ndarray) -> np.ndarray:
        import torch
        model = self._load()
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        t = torch.from_numpy(rgb).permute(2, 0, 1).float() / 255.0
        d = model.infer(t)["depth"].float().cpu().numpy()
        d[~np.isfinite(d)] = 120.0  # ponytail: >100m 零星 inf 钳位（proto 同款）
        return d


class TrainedDepthEstimator:
    """自训练学生估计器。checkpoint 格式 torch.save({"arch","state_dict","meta"})。"""

    def __init__(self, weights_path: str, device: Optional[Any] = None) -> None:
        self.weights_path = weights_path
        self.device = device
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            from plugins.yolo_depth.depth_student import build_student_net
            import torch
            ckpt = torch.load(self.weights_path, map_location="cpu", weights_only=True)
            arch = ckpt.get("arch") or STUDENT_ARCH
            if arch != STUDENT_ARCH:
                raise ValueError(f"未知的深度学生架构: {arch}")
            net = build_student_net(pretrained=False)
            net.load_state_dict(ckpt["state_dict"])
            net.eval()
            if self.device is not None:
                net = net.to(self.device)
            self._model = net
        return self._model

    def estimate(self, frame: np.ndarray) -> np.ndarray:
        import torch
        net = self._load()
        with torch.inference_mode():
            x = preprocess_frame(frame).unsqueeze(0).to(next(net.parameters()).device)
            pred_log = net(x)[0, 0].float().cpu().numpy()
        depth = np.exp(pred_log)
        h, w = frame.shape[:2]
        depth = cv2.resize(depth, (w, h), interpolation=cv2.INTER_LINEAR)
        return np.nan_to_num(depth, nan=0.1, posinf=150.0, neginf=0.05).clip(0.05, 150.0)


def preprocess_frame(frame: np.ndarray):
    """BGR uint8 帧 -> ImageNet 归一化 CHW float32 张量，resize 到学生输入分辨率。"""
    from plugins.yolo_depth.depth_student import INPUT_SIZE
    img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    img = cv2.resize(img, (INPUT_SIZE, INPUT_SIZE), interpolation=cv2.INTER_LINEAR)
    img = img.astype(np.float32) / 255.0
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    img = (img - mean) / std
    import torch
    return torch.from_numpy(img).permute(2, 0, 1).float()
