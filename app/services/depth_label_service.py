"""伪标签生成服务：视频抽帧 → MoGe 教师推理 → frames+npy 数据集目录。

产物布局与 proto 一致：
<out_dir>/frames/<stem>/NNNNN.jpg, depth/<stem>/NNNNN.npy(float16 米),
manifest.json 索引全部样本。
"""
from __future__ import annotations

import json
import logging
import os

import cv2
import numpy as np
import torch

from app.common.utils import now_iso

logger = logging.getLogger(__name__)

DHASH_SIZE = 8
DEFAULT_INTERVAL_S = 0.2
DEDUP_THRESHOLD = 6
MANIFEST_NAME = "manifest.json"


def _dhash(img, size=DHASH_SIZE):
    g = cv2.cvtColor(cv2.resize(img, (size + 1, size)), cv2.COLOR_BGR2GRAY)
    return (g[:, 1:] > g[:, :-1]).flatten()


def extract_frames(videos, frames_dir, interval_s=DEFAULT_INTERVAL_S,
                   dedup_threshold=DEDUP_THRESHOLD):
    """0.2s 间隔抽帧 + dhash 去重。videos=[{name,path}]，返回帧路径列表。"""
    paths = []
    for v in videos:
        stem = os.path.splitext(os.path.basename(v["name"]))[0]
        vdir = os.path.join(frames_dir, stem)
        os.makedirs(vdir, exist_ok=True)
        cap = cv2.VideoCapture(v["path"])
        try:
            if not cap.isOpened():
                logger.warning("无法打开视频，跳过: %s", v["path"])
                continue
            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            step = max(1, round(fps * interval_s))
            last_hash = None
            idx = kept = 0
            while True:
                ok = cap.grab()
                if not ok:
                    break
                if idx % step == 0:
                    ok, frame = cap.retrieve()
                    if not ok:
                        break
                    h = _dhash(frame)
                    if last_hash is None or int((h != last_hash).sum()) >= dedup_threshold:
                        p = os.path.join(vdir, f"{kept:05d}.jpg")
                        cv2.imwrite(p, frame)
                        paths.append(p)
                        last_hash = h
                        kept += 1
                idx += 1
        finally:
            cap.release()
    return paths


def run_pseudo_labeling(videos, interval_s, out_dir, progress_cb=None):
    """完整伪标签流程：抽帧 → MoGe 教师推理 → manifest.json。返回统计 dict。

    progress_cb(progress:int 0-100, message:str) 由调用方（训练任务线程）
    转成任务进度持久化。教师加载失败抛 RuntimeError（任务转 failed）。
    """
    frames_dir = os.path.join(out_dir, "frames")
    depth_dir = os.path.join(out_dir, "depth")
    for d in (frames_dir, depth_dir):
        os.makedirs(d, exist_ok=True)

    def report(pct, msg):
        if progress_cb:
            progress_cb(int(pct), msg)

    report(5, "抽取视频帧...")
    paths = extract_frames(videos, frames_dir, interval_s)
    if not paths:
        raise RuntimeError("未从视频中抽到任何帧，请检查视频文件")

    from plugins.yolo_depth.depth_models import MoGeDepthEstimator
    teacher = MoGeDepthEstimator()
    teacher.ensure_loaded()  # 提前暴露缺包/缺权重错误（公开接口）
    report(15, f"MoGe 教师已就绪，开始打标 {len(paths)} 帧...")

    stats = {"videos": {}, "items": []}
    n = len(paths)
    for i, fp in enumerate(paths):
        stem = os.path.basename(os.path.dirname(fp))
        img = cv2.imread(fp)
        if img is None:
            continue
        with torch.inference_mode():
            d = teacher.estimate(img)
        rel_frame = os.path.relpath(fp, out_dir).replace("\\", "/")
        rel_depth = rel_frame.replace("frames/", "depth/").replace(".jpg", ".npy")
        depth_path = os.path.join(out_dir, rel_depth)
        # np.save 不建中间目录；depth/<stem>/ 必须先创建（评审 HIGH 回归点）
        os.makedirs(os.path.dirname(depth_path), exist_ok=True)
        np.save(depth_path, d.astype(np.float16))
        stats["items"].append({"frame": rel_frame, "depth": rel_depth})
        stats["videos"][stem] = stats["videos"].get(stem, 0) + 1
        if (i + 1) % 10 == 0 or i + 1 == n:
            report(15 + 75 * (i + 1) // n, f"教师打标 {i + 1}/{n} 帧")

    report(92, "写入 manifest...")
    manifest = {
        "teacher": "moge2_vitl",
        "interval_s": interval_s,
        "created_at": now_iso(),
        "frames_total": len(stats["items"]),
        "videos": stats["videos"],
        "items": stats["items"],
    }
    with open(os.path.join(out_dir, MANIFEST_NAME), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    report(100, f"伪标签完成：{len(stats['items'])} 帧")
    return manifest
