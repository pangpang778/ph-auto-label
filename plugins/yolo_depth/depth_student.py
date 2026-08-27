"""深度蒸馏学生网络：MobileNetV3-Large 编码器 + 轻量 UNet 解码器（工单 02 配置）。

输入 384x384 RGB，输出 1 通道 log 深度。SiLog(λ=0.85) + 梯度匹配损失。
"""
from __future__ import annotations

import logging

import torch
import torch.nn as nn

logger = logging.getLogger(__name__)

INPUT_SIZE = 384
STUDENT_ARCH = "mbv3_unet_384"


def build_student_net(pretrained: bool = True) -> "DepthStudentNet":
    """构建学生网络。pretrained=True 时 timm 加载 ImageNet 预训练编码器，
    离线/下载失败时回退随机初始化并告警（可用性优先，但劣化必须可见）。"""
    try:
        return DepthStudentNet(encoder_pretrained=pretrained)
    except Exception as exc:
        logger.warning("预训练 encoder 加载失败，回退随机初始化: %s", exc)
        return DepthStudentNet(encoder_pretrained=False)


class DepthStudentNet(nn.Module):
    def __init__(self, encoder_pretrained: bool = True) -> None:
        super().__init__()
        import timm
        # features_only=True 返回 5 个尺度的特征图 (2,4,8,16,32 下采样)
        self.encoder = timm.create_model(
            "mobilenetv3_large_100", features_only=True,
            pretrained=encoder_pretrained,
        )
        chs = self.encoder.feature_info.channels()  # e.g. [16, 24, 40, 112, 672]
        self.dec4 = _Up(chs[4], chs[3])
        self.dec3 = _Up(chs[3], chs[2])
        conv_out = nn.Sequential(
            nn.Conv2d(chs[2], 16, 3, padding=1), nn.ReLU(inplace=True),
            # 线性头输出 log 深度；无激活函数
            nn.Conv2d(16, 1, 1),
        )
        self.head = conv_out
        self.up_to_full = nn.Upsample(scale_factor=8, mode="bilinear", align_corners=False)

    def forward(self, x):
        f = self.encoder(x)
        d4 = self.dec4(f[4], f[3])
        d3 = self.dec3(d4, f[2])
        out = self.head(d3)            # 48x48 @ 384 输入
        return self.up_to_full(out)    # 上采样到 384x384


class _Up(nn.Module):
    """加性跳连上采样解码块：2x 上采样 + conv(输入+跳连通道) + ReLU。"""

    def __init__(self, in_ch: int, skip_ch: int) -> None:
        super().__init__()
        self.up = nn.Upsample(scale_factor=2, mode="bilinear", align_corners=False)
        self.conv = nn.Sequential(
            nn.Conv2d(in_ch + skip_ch, skip_ch, 3, padding=1),
            nn.ReLU(inplace=True),
        )

    def forward(self, x, skip):
        x = self.up(x)
        if x.shape[-2:] != skip.shape[-2:]:
            x = nn.functional.interpolate(x, size=skip.shape[-2:], mode="bilinear", align_corners=False)
        return self.conv(torch.cat([x, skip], dim=1))


def silog_loss(pred_log: "torch.Tensor", target_log: "torch.Tensor",
               lam: float = 0.85) -> "torch.Tensor":
    """SiLog：sqrt(mean(l^2) - λ*(mean l)^2)，l = log 预测 - log 目标。"""
    diff = pred_log - target_log
    var_term = diff.pow(2).mean()
    mean_term_sq = diff.mean().pow(2)
    return torch.clamp(var_term - lam * mean_term_sq, min=1e-8).sqrt()


def gradmatch_loss(pred, target):
    """空间梯度匹配：mean(|dx_pred - dx_gt| + |dy_pred - dy_gt|)。"""
    dx_p = pred[:, :, :, 1:] - pred[:, :, :, :-1]
    dy_p = pred[:, :, 1:, :] - pred[:, :, :-1, :]
    dx_g = target[:, :, :, 1:] - target[:, :, :, :-1]
    dy_g = target[:, :, 1:, :] - target[:, :, :-1, :]
    return (dx_p - dx_g).abs().mean() + (dy_p - dy_g).abs().mean()
