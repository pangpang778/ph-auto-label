"""深度蒸馏训练服务：MoGe 伪标签 → 学生网络训练 → checkpoint（工单 05）。

消费 depth_label_service 产出的 frames+npy+manifest 数据集目录，
按视频分组 8:2 切分（防相邻帧时间泄漏），SiLog+梯度匹配损失训练，
验证集 SiLog 选优保存 best checkpoint。
"""
from __future__ import annotations

import json
import logging
import os

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# 训练配置（工单 02 推荐值）
DEFAULT_EPOCHS = 50
DEFAULT_BATCH = 32
LEARNING_RATE = 1e-4

def load_manifest(dataset_dir):
    """读取数据集 manifest.json。缺失抛 FileNotFoundError。"""
    path = os.path.join(dataset_dir, "manifest.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"数据集缺少 manifest.json: {dataset_dir}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def split_items_by_video(items, train_ratio=0.8, seed=42):
    """按视频分组 8:2 切分（工单 03：防相邻帧时间泄漏）。

    items 为 manifest["items"]，frame 形如 frames/<stem>/00001.jpg，
    分组键取 <stem>。确定性：seed 固定，同数据集重训得到同切分。
    """
    groups = {}
    for it in items:
        stem = os.path.basename(os.path.dirname(it["frame"]))
        groups.setdefault(stem, []).append(it)
    stems = sorted(groups)
    if len(stems) == 1:
        # Single-video fallback: front segment trains / tail validates with an
        # exclusion gap, keeping adjacent-frame leakage protection.
        its = sorted(groups[stems[0]], key=lambda it: it["frame"])
        cut = max(1, int(len(its) * train_ratio))
        gap = max(1, int(len(its) * 0.02))
        val = its[cut + gap:]
        if not val:
            raise RuntimeError("single-video dataset too small for a val split")
        return {"train": its[:cut], "val": val}
    rng = np.random.RandomState(seed)
    rng.shuffle(stems)
    n_train = max(1, int(len(stems) * train_ratio))
    train_items, val_items = [], []
    for s in stems[:n_train]:
        train_items.extend(groups[s])
    for s in stems[n_train:]:
        val_items.extend(groups[s])
    return {"train": train_items, "val": val_items}


class _DepthDataset:
    """frames+npy 数据集：resize 384、log 深度目标。torch Dataset 协议。"""

    def __init__(self, dataset_dir, items, input_size=384):
        self.dataset_dir = dataset_dir
        self.items = items
        self.input_size = input_size

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        import torch
        it = self.items[i]
        img = cv2.imread(os.path.join(self.dataset_dir, it["frame"]))
        depth = np.load(
            os.path.join(self.dataset_dir, it["depth"]), allow_pickle=False
        ).astype(np.float32)
        img = cv2.resize(img, (self.input_size, self.input_size), interpolation=cv2.INTER_LINEAR)
        depth = cv2.resize(depth, (self.input_size, self.input_size), interpolation=cv2.INTER_LINEAR)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        img = (img - mean) / std
        depth = np.nan_to_num(depth, nan=0.1, posinf=150.0, neginf=0.05).clip(0.05, 150.0)
        x = torch.from_numpy(img).permute(2, 0, 1).float()
        y = torch.from_numpy(np.log(depth)).float().unsqueeze(0)
        return x, y


def _run_epoch(net, loader, optimizer, device, train):
    """一个 epoch；train=True 反传。返回 (平均 silog, 平均 gradmatch)。"""
    from plugins.yolo_depth.depth_student import silog_loss, gradmatch_loss
    import torch
    net.train() if train else net.eval()
    tot_s = tot_g = 0.0
    n = 0
    ctx = torch.enable_grad() if train else torch.inference_mode()
    with ctx:
        for x, y in loader:
            x = x.to(device)
            y = y.to(device)
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=(device != "cpu")):
                pred = net(x)
                loss = silog_loss(pred, y) + gradmatch_loss(pred, y)
            if train:
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
            pf, yf = pred.float().detach(), y.float()
            tot_s += float(silog_loss(pf, yf))
            tot_g += float(gradmatch_loss(pf, yf))
            n += 1
    return tot_s / max(n, 1), tot_g / max(n, 1)


def _validate_manifest_paths(dataset_dir, manifest):
    """Manifest rel paths must stay inside dataset_dir (defense-in-depth)."""
    base = os.path.realpath(dataset_dir)
    for it in manifest.get("items", []):
        for key in ("frame", "depth"):
            rel = str(it.get(key) or "")
            target = os.path.realpath(os.path.join(base, rel))
            try:
                inside = os.path.commonpath([base, target]) == base
            except ValueError:
                inside = False
            if not inside:
                raise ValueError(f"manifest path escape: {rel}")


def train_depth_student(job, job_dir, progress_cb=None):
    """深度蒸馏训练主流程。job 携带 dataset_dir/epochs/batch/device。

    返回 {"checkpoint_path", "metrics"}；checkpoint 为
    torch.save({"arch","state_dict","meta"}) 格式，供 TrainedDepthEstimator 加载。
    progress_cb(progress:int, message:str)。
    """
    from plugins.yolo_depth.depth_student import build_student_net
    import torch
    from torch.utils.data import DataLoader

    def report(pct, msg):
        if progress_cb:
            progress_cb(int(pct), msg)

    dataset_dir = job["dataset_dir"]
    manifest = load_manifest(dataset_dir)
    _validate_manifest_paths(dataset_dir, manifest)
    splits = split_items_by_video(manifest["items"])
    train_ds = _DepthDataset(dataset_dir, splits["train"])
    val_ds = _DepthDataset(dataset_dir, splits["val"])
    if len(train_ds) < 8 or len(val_ds) < 2:
        raise RuntimeError(f"训练/验证样本不足: train={len(train_ds)} val={len(val_ds)}")
    device = job.get("device") or ("cuda" if torch.cuda.is_available() else "cpu")
    batch = int(job.get("batch") or DEFAULT_BATCH)
    n_workers = 2 if os.name == "nt" else min(4, os.cpu_count() or 2)
    loader_kw = dict(num_workers=n_workers, pin_memory=(device != "cpu"),
                     persistent_workers=(n_workers > 0))
    train_loader = DataLoader(train_ds, batch_size=batch, shuffle=True, **loader_kw)
    val_loader = DataLoader(val_ds, batch_size=batch, shuffle=False, **loader_kw)

    # 尊重已校验的 job['device']（resolve_training_device 产物：CUDA id / "cpu"），
    # 显式要求 cpu 时不得静默回退 GPU。
    net = build_student_net(pretrained=True).to(device)
    optimizer = torch.optim.AdamW(net.parameters(), lr=LEARNING_RATE)
    epochs = int(job.get("epochs") or DEFAULT_EPOCHS)
    best_silog = None
    best_state = None
    run_dir = os.path.join(job_dir, "runs", "depth")
    os.makedirs(run_dir, exist_ok=True)
    report(10, f"开始蒸馏：train={len(train_ds)} val={len(val_ds)} device={device}")

    for epoch in range(1, epochs + 1):
        tr_s, tr_g = _run_epoch(net, train_loader, optimizer, device, train=True)
        with torch.inference_mode():
            va_s, va_g = _run_epoch(net, val_loader, None, device, train=False)
        if best_silog is None or va_s < best_silog:
            best_silog = va_s
            best_state = {k: v.detach().cpu().clone() for k, v in net.state_dict().items()}
        pct = 10 + int(85 * epoch / epochs)
        report(pct, f"Epoch {epoch}/{epochs} silog={va_s:.4f} (best {best_silog:.4f})")

    ckpt = save_student_checkpoint(net, best_state, run_dir,
                                   {"epochs": epochs, "val_silog": best_silog,
                                    "train_frames": len(train_ds), "val_frames": len(val_ds)})
    report(96, "checkpoint 已保存，计算框内中值偏差验收指标...")
    try:
        dev = box_median_deviation(dataset_dir, splits["val"], ckpt)
    except Exception as exc:
        logger.warning("框内中值偏差评估失败（不阻塞训练完成）: %s", exc)
        dev = None
    metrics = {"val_silog": round(best_silog, 5),
               "box_median_dev_pct": dev,
               "gradmatch_final": round(tr_g, 5)}
    report(100, f"蒸馏完成 silog={best_silog:.4f}")
    return {"run_dir": run_dir, "checkpoint_path": ckpt, "metrics": metrics,
            "splits": {k: len(v) for k, v in splits.items()}}


def save_student_checkpoint(net, best_state, run_dir, meta):
    """保存 best checkpoint：{"arch","state_dict","meta"}。返回路径。"""
    import torch
    from plugins.yolo_depth.depth_student import STUDENT_ARCH
    path = os.path.join(run_dir, "depth_best.pt")
    torch.save({
        "arch": STUDENT_ARCH,
        "state_dict": best_state if best_state is not None else net.state_dict(),
        "meta": meta,
    }, path)
    return path


def box_median_deviation(dataset_dir, val_items, checkpoint_path, max_frames=200,
                         detector_weights="yolo11n.pt"):
    """工单 07 验收指标：留出验证集上学生 vs 教师的框内中值距离偏差（%）。

    用 yolo11n 检测 car 框，分别取学生/教师深度图框内中值，
    偏差 = mean(|d_pred - d_teach| / d_teach)。无检测框时返回 None。
    """
    from plugins.yolo_depth.depth_models import TrainedDepthEstimator
    est = TrainedDepthEstimator(checkpoint_path)
    from ultralytics import YOLO
    det = YOLO(detector_weights)
    devs = []
    for it in val_items[:max_frames]:
        img = cv2.imread(os.path.join(dataset_dir, it["frame"]))
        teach = np.load(
            os.path.join(dataset_dir, it["depth"]), allow_pickle=False
        ).astype(np.float32)
        pred = est.estimate(img)
        h, w = img.shape[:2]
        boxes = det.predict(img, classes=[2], conf=0.35, verbose=False)
        r = boxes[0] if boxes else None
        if r is None or r.boxes is None or r.boxes.xyxy is None or len(r.boxes.xyxy) == 0:
            continue
        for xyxy in r.boxes.xyxy.cpu().numpy():
            x1, y1, x2, y2 = [int(v) for v in xyxy]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            if x2 - x1 < 8 or y2 - y1 < 8:
                continue
            m_t = float(np.median(teach[y1:y2, x1:x2]))
            m_p = float(np.median(pred[y1:y2, x1:x2]))
            if not np.isfinite(m_t) or m_t <= 0 or not np.isfinite(m_p):
                continue
            devs.append(abs(m_p - m_t) / m_t)
    if not devs:
        return None
    return round(100.0 * float(np.mean(devs)), 2)
