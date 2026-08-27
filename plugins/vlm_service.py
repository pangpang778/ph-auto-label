"""VLM 大模型服务：SGLang OpenAI 兼容端点的 HTTP 客户端单例。

平台不加载大模型本体——容器化 SGLang 服务负责推理（8GB 显存下后端互斥：
选中后端时自动 docker start 目标容器、stop 竞争容器），本模块只做请求/解析。
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import time

import cv2
import requests

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = os.environ.get("VLM_BASE_URL", "http://127.0.0.1:8001/v1")
DEFAULT_MODEL = os.environ.get("VLM_MODEL_NAME", "qwen3-vl-4b")
# bbox 坐标语义：absolute=原图绝对像素(Qwen2.5)；normalized=0-1000(新一代 VLM)
COORD_MODE = os.environ.get("VLM_COORD_MODE", "normalized")

# VLM backend registry: swappable OpenAI-compatible services (VRAM-exclusive).
# ponytail: 端口/容器名硬编码为默认值，换部署用 env 覆盖即可。
VLM_BACKENDS = {
    "qwen3-vl-4b": {
        "base_url": os.environ.get("VLM_BASE_URL", "http://127.0.0.1:8001/v1"),
        "container": os.environ.get("VLM_CONTAINER", "ph-vlm"),
        "label": "Qwen3-VL-4B (SGLang)",
    },
    "locateanything-3b": {
        "base_url": os.environ.get("LOCATEANYTHING_API", "http://127.0.0.1:8002/v1"),
        "container": os.environ.get("LOCATEANYTHING_CONTAINER", "ph-la"),
        "model_id": "nvidia/LocateAnything-3B",
        "label": "LocateAnything-3B (SGLang)",
    },
}
TIMEOUT_S = 600  # 后端冷启动（docker start + 权重加载）可达数分钟，防误超时

# LA 官方 detect-all prompt（模型 README）：类别用 </c> 连接
LA_DETECT_PROMPT = (
    "Locate all the instances that matches the following description: {targets}."
)


class VlmService:
    def __init__(self) -> None:
        self._session = None

    @property
    def session(self):
        if self._session is None:
            import requests
            self._session = requests.Session()
            # ponytail: Windows 系统代理会把 localhost 打到外部代理上（实测 502），
            # 本地 SGLang 调用必须绕过一切环境/系统代理设置。
            self._session.trust_env = False
        return self._session

    def _backend_live(self, model_id) -> str | None:
        """后端在线且模型 id 尾段匹配时返回实际 serve 的 id，否则 None。"""
        cfg = VLM_BACKENDS[model_id]
        try:
            r = self.session.get(f"{cfg['base_url']}/models", timeout=3)
            if r.status_code == 200:
                want = cfg.get("model_id", model_id)
                for m in r.json().get("data", []):
                    lid = str(m.get("id") or "")
                    # 尾段大小写不敏感匹配：nvidia/X 与 X 视为同一模型
                    if lid == want or lid.split("/")[-1].lower() == want.split("/")[-1].lower():
                        return lid
        except Exception:
            return None
        return None

    def list_models(self):
        """Aggregate models across backends with availability flags."""
        out = []
        for model_id, cfg in VLM_BACKENDS.items():
            live = self._backend_live(model_id)
            out.append({"id": model_id,
                        "label": cfg["label"] + (" ✓" if live else "(not started)"),
                        "available": bool(live)})
        return out

    def _engine_warm(self, cfg) -> bool:
        """/models 200 ≠ 引擎可用（权重加载中即应答）；/health_generate 真跑一次
        token 生成，才是推理就绪。"""
        try:
            base = cfg["base_url"].rstrip("/")
            if base.endswith("/v1"):
                base = base[:-3]
            r = self.session.get(f"{base}/health_generate", timeout=15)
            return r.status_code == 200
        except Exception:
            return False

    def _kv_pool_ok(self, cfg) -> bool:
        """崩溃后 docker 自动重启时旧进程显存未释放，KV 池会被算成几百 token
        （图片请求 ~950 token 直接 400）。池子太小就要求重启容器。"""
        try:
            base = cfg["base_url"].rstrip("/")
            if base.endswith("/v1"):
                base = base[:-3]
            r = self.session.get(f"{base}/get_server_info", timeout=10)
            if r.status_code != 200:
                return True  # 探测不了就别拦
            info = r.json()
            pool = int(info.get("max_total_num_tokens") or 0)
            return pool == 0 or pool >= 2500
        except Exception:
            return True

    def ensure_backend(self, model: str | None = None) -> dict:
        """公开接口：确保所选模型的后端容器运行、引擎 warm 且 KV 池可用。"""
        import subprocess
        model = model or self.model
        cfg = self._ensure_backend(model)
        restarted = False
        if cfg:
            deadline = time.time() + 420
            while time.time() < deadline:
                if self._backend_live(model) and self._engine_warm(cfg):
                    if self._kv_pool_ok(cfg):
                        break
                    if not restarted:
                        logger.warning("VLM backend KV pool too small, restarting %s",
                                       cfg.get("container"))
                        restarted = True
                        try:
                            from plugins.sam3_service import sam3_service
                            if sam3_service.is_loaded:
                                sam3_service.unload()
                            # 显存释放有滞后，且容器自己占着 5.5GB——必须先停
                            # 再等显存回落、后启动，restart 一把梭只会白等
                            subprocess.run(["docker", "stop", cfg["container"]],
                                           capture_output=True, timeout=120)
                            self._wait_vram_free(min_free_mb=6500, timeout_s=150)
                            subprocess.run(["docker", "start", cfg["container"]],
                                           capture_output=True, timeout=120)
                        except Exception:
                            pass
                time.sleep(3)
        return cfg

    def is_available(self) -> bool:
        """轻量健康检查：任一后端 /models 200 即就绪。"""
        return any(self._backend_live(mid) for mid in VLM_BACKENDS)

    @property
    def base_url(self) -> str:
        return os.environ.get("VLM_BASE_URL", DEFAULT_BASE_URL)

    @property
    def model(self) -> str:
        return os.environ.get("VLM_MODEL_NAME", DEFAULT_MODEL)

    # ---------- grounding：文本提示 -> 检测框 ----------
    # ponytail: 紧凑格式+数量上限--解码 ~26 tok/s(4060 FP8)，输出长度即延迟
    GROUNDING_PROMPT = (
        "Detect up to 8 instances of: {targets}. "
        "Output ONLY a JSON array, each item "
        '{{"bbox_2d": [x1,y1,x2,y2], "label": "<target>"}} '
        "with 0-1000 normalized coords. Stop after the array."
    )

    def grounding(self, frame_bgr, targets, conf=0.35, model=None):
        """对 BGR ndarray 做开放词汇检测。targets=[str]。返回 [{class,conf,xyxy}]。

        两代后端统一 0-1000 归一化坐标：
        - Qwen 系输出 JSON {"bbox_2d": [x1,y1,x2,y2]}；
        - LocateAnything 输出 <ref>label</ref><box><x1><y1><x2><y2></box>
          （0-1000 归一化，顺序 x1,y1,x2,y2——README 为准；generate_utils
          注释的 x1,x2,y1,y2 说法实测有误，全图退化框只有按 xyxy 读才合理）。
        """
        model = model or self.model
        h, w = frame_bgr.shape[:2]
        if model == "locateanything-3b":
            # 768px 宽输入：视觉 token 少 3 倍，质量/速度更优。坐标是
            # 0-1000 归一化，但必须按【原图】尺寸换算回像素（标注显示在
            # 原图上；用缩放后尺寸会让全部框缩成 60% 挤在左上角）。
            if w > 768:
                scale = 768.0 / w
                frame_bgr = cv2.resize(frame_bgr, (768, int(h * scale)))
            raw = self._la_detect(frame_bgr, targets, model)
            boxes = _parse_la_boxes(raw)
        else:
            prompt = self.GROUNDING_PROMPT.format(targets="、".join(targets))
            content = [
                {"type": "image_url", "image_url": {"url": _to_data_uri(frame_bgr)}},
                {"type": "text", "text": prompt},
            ]
            raw = self._chat(content, model=model)
            boxes = _parse_bbox_json(raw)
        if model == "locateanything-3b":
            # LA 的 AR 解码输出长度随机（同参数下既有 20+ 正常小框，也有单个
            # 全图退化框）。检出可疑结果就重新采样，最多 3 次；仍可疑则按最后
            # 一次返回（前端可见，人工兜底）。
            for _ in range(2):
                if not _la_suspect(boxes, w, h):
                    break
                logger.info("LA output suspect (%d boxes), resampling", len(boxes))
                raw = self._la_detect(frame_bgr, targets, model)
                boxes = _parse_la_boxes(raw)
        out = []
        for b in boxes:
            x1, y1, x2, y2 = (float(v) for v in b["bbox_2d"])
            if COORD_MODE == "normalized":
                # 0-1000 归一化 -> 原图像素
                x1, x2 = x1 * w / 1000.0, x2 * w / 1000.0
                y1, y2 = y1 * h / 1000.0, y2 * h / 1000.0
            label = str(b.get("label") or "")
            if label not in targets:
                # 模型可能输出近义标签；宽松匹配包含关系，匹配不上直接丢弃
                # （强改成 targets[0] 会静默污染训练标注）
                match = [t for t in targets if t in label or label in t]
                if not match:
                    logger.debug("VLM label not in targets, drop box: %s", label)
                    continue
                logger.debug("VLM label mapped: %s -> %s", label, match[0])
                label = match[0]
            xyxy = [
                max(0.0, min(x1, float(w))),
                max(0.0, min(y1, float(h))),
                max(0.0, min(x2, float(w))),
                max(0.0, min(y2, float(h))),
            ]
            if xyxy[2] - xyxy[0] < 4 or xyxy[3] - xyxy[1] < 4:
                continue
            out.append({"class": label, "conf": float(b.get("conf", 0.8)), "xyxy": xyxy})
        return [o for o in out if o["conf"] >= conf]

    def _la_detect(self, frame_bgr, targets, model) -> str:
        """官方混合解码后端（la_hybrid 容器 /detect），返回原始 <ref>/<box> 流。"""
        cfg = self._ensure_backend(model) or {}
        base = cfg.get("base_url", "http://127.0.0.1:8002/v1").rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        r = self.session.post(f"{base}/detect",
                              json={"image": _to_data_uri(frame_bgr),
                                    "targets": list(targets)},
                              timeout=TIMEOUT_S)
        r.raise_for_status()
        return r.json().get("text", "")

    # ---------- chat 底座 ----------
    def _wait_vram_free(self, min_free_mb: int, timeout_s: int) -> None:
        """docker stop 返回后显存释放有滞后（Windows/WSL2 驱动延迟），立刻启动
        下一个后端会按残缺显存算 KV 池（实测缩到 637 token，图片请求全 400）。"""
        import subprocess
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                out = subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.free",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=15).stdout
                free = int(out.strip().splitlines()[0])
                if free >= min_free_mb:
                    return
            except Exception:
                return  # nvidia-smi 不可用时不阻塞，退化为原行为
            time.sleep(3)

    def _ensure_backend(self, model: str):
        """Ensure the chosen backend container runs and its rival is stopped."""
        import subprocess
        cfg = VLM_BACKENDS.get(model)
        if not cfg:
            return None
        try:
            from plugins.sam3_service import sam3_service
            if sam3_service.is_loaded:
                logger.info("Unloading in-process SAM3 to free VRAM for %s", model)
                sam3_service.unload()
        except Exception:
            pass
        rivals = [c["container"] for mid, c in VLM_BACKENDS.items()
                  if mid != model]
        try:
            ps = subprocess.run(["docker", "ps", "--format", "{{.Names}}"],
                                capture_output=True, text=True, timeout=30)
            running = set(ps.stdout.split())
        except Exception:
            return cfg
        if other := next((o for o in rivals if o in running), None):
            subprocess.run(["docker", "stop", other],
                           capture_output=True, timeout=120)
            self._wait_vram_free(min_free_mb=6500, timeout_s=90)
        if target := cfg.get("container"):
            if target not in running:
                subprocess.run(["docker", "start", target],
                               capture_output=True, timeout=120)
                deadline = time.time() + 300
                while time.time() < deadline:
                    if self._backend_live(model) and self._engine_warm(cfg):
                        break
                    time.sleep(5)
        return cfg

    def _chat(self, content, model=None) -> str:
        model = model or self.model
        cfg = self._ensure_backend(model) or {}
        payload = {
            "model": cfg.get("model_id", model),
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.0,
            "max_tokens": 300,
            # Qwen3 思考模式默认开启：先推理长文再吐答案，纯检测任务必须关闭
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if model == "locateanything-3b":
            # 官方配方：采样式解码（greedy 实测退化为全图框），
            # 且必须保留 <ref>/<box> 特殊 token 才能解析出框
            payload.update({"temperature": 0.7, "top_p": 0.9,
                            "repetition_penalty": 1.1,
                            "skip_special_tokens": False, "max_tokens": 2048})
        r = self.session.post(f"{cfg.get('base_url', self.base_url)}/chat/completions",
                              json=payload, timeout=TIMEOUT_S)
        r.raise_for_status()
        data = r.json()
        return (data.get("choices") or [{}])[0].get("message", {}).get("content", "")

    def detect_frame(self, frame, text=None, conf=0.35, model=None):
        """视频逐帧接口：与 sam3_service.detect_frame 同签名。text=[str]。"""
        targets = [t.strip() for t in (text or []) if t.strip()]
        if not targets:
            return []
        return self.grounding(frame, targets, conf=conf, model=model)

    def detect_from_file(self, image_path, text=None, conf=0.35, model=None):
        img = cv2.imread(image_path)
        if img is None:
            raise FileNotFoundError(f"图片不存在: {image_path}")
        return self.detect_frame(img, text=text, conf=conf, model=model)


vlm_service = VlmService()


# ---------- 段级工具 ----------
def _to_data_uri(frame_bgr) -> str:
    ok, buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 90])
    b64 = base64.b64encode(buf.tobytes()).decode()
    return f"data:image/jpeg;base64,{b64}"


def _parse_bbox_json(raw):
    """从模型回复里抠出 bbox 数组。容忍 ```json 围栏、前后闲话与 max_tokens 截断
    （图像任务输出常被截断，整段 loads 易失败，故逐对象提取）。"""
    raw = raw or ""
    # whole-array json.loads always fails on truncated output; extract per object directly
    items = re.findall(r"\{[^{}]*\}", raw)
    out = []
    for item in items:
        if isinstance(item, str):
            try:
                item = json.loads(item)
            except json.JSONDecodeError:
                continue
        if not isinstance(item, dict):
            continue
        box = item.get("bbox_2d") or item.get("bbox")
        if isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box):
            out.append({"bbox_2d": box, "label": item.get("label"), "conf": item.get("score", 0.8)})
    return out


def _parse_la_boxes(raw):
    """LA 输出解析：<ref>label</ref><box><a><b><c><d></box> -> bbox_2d xyxy。

    模型坐标顺序为 x1,x2,y1,y2（模型 release generate_utils.py 注释为准），
    解析时转成 x1,y1,x2,y2。
    """
    raw = raw or ""
    events = sorted(
        [(m.start(), "ref", m.group(1).strip())
         for m in re.finditer(r"<ref>([^<]+)</ref>", raw)]
        + [(m.start(), "box", m.groups())
           for m in re.finditer(r"<box><(\d+)><(\d+)><(\d+)><(\d+)></box>", raw)]
    )
    out, label = [], ""
    for _pos, kind, val in events:
        if kind == "ref":
            label = val
            continue
        # 实测坐标顺序为 x1,y1,x2,y2（README 正确，generate_utils 注释有误）：
        # 全图退化框 <0><0><998><999> 只有按 xyxy 读才合理。
        x1, y1, x2, y2 = (int(v) for v in val)
        if x1 > x2:
            x1, x2 = x2, x1
        if y1 > y2:
            y1, y2 = y2, y1
        out.append({"bbox_2d": [x1, y1, x2, y2], "label": label})
    return out


def _la_suspect(boxes, w, h):
    """LA 退化输出判定：0 框、全图孤框（≥55% 面积）、极端宽高比条带、
    或 ≥3 框全部贴顶（树冠/水印条带模式）。

    只拦明显坏样本：合法的大目标单框（如近景车占半幅）不该触发无谓重采样。
    """
    if not boxes:
        return True
    if len(boxes) == 1:
        x1, y1, x2, y2 = (v / 1000.0 for v in boxes[0]["bbox_2d"])
        if (x2 - x1) * (y2 - y1) >= 0.55:
            return True
    for b in boxes:
        x1, y1, x2, y2 = (v / 1000.0 for v in b["bbox_2d"])
        bw, bh = max(x2 - x1, 1e-6), max(y2 - y1, 1e-6)
        if bw / bh > 6 or bh / bw > 6:
            return True
    if len(boxes) >= 3 and all(b["bbox_2d"][3] < 80 for b in boxes):
        return True
    return False
