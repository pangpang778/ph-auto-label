"""LocateAnything-3B 官方混合解码推理服务。

SGLang 只能按标准 AR 跑这个模型（其设计是 Fast/Hybrid 块并行解码，
AR 是官方为"格式不规则"准备的降级路径），实测退化框率高。本服务用
模型仓库自带的 batch_utils 混合引擎（transformers 路线）做推理。

接口故意与 SGLang 编排兼容：
- GET /v1/models            -> vlm_service._backend_live 探活
- GET /health_generate      -> vlm_service._engine_warm 就绪探测
- POST /detect {image, targets} -> {text: <ref>/<box> 原始流}
"""
import base64
import io
import os
import sys
import threading

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from PIL import Image

MODEL_ID = os.environ.get("LA_FLASH_MODEL", "nvidia/LocateAnything-3B")
MAX_DIM = 1024  # 与 batch_utils.hybrid_runtime.load_pil 一致

app = FastAPI()
_state = {"loaded": False, "error": ""}


def _load_engine():
    """解析 HF 缓存里的快照路径，导入仓库自带混合引擎并预热。"""
    from huggingface_hub import snapshot_download
    snap = snapshot_download(MODEL_ID, local_files_only=True)
    sys.path.insert(0, snap)
    global generate_batch_hybrid, get_last_hybrid_stats
    from batch_utils.hybrid_runtime import load
    from batch_utils.engine_hybrid import generate_batch_hybrid as _gen, get_last_hybrid_stats as _stats
    load()
    generate_batch_hybrid, get_last_hybrid_stats = _gen, _stats
    _state["loaded"] = True
    print("[la-hybrid] engine ready", flush=True)


class DetectReq(BaseModel):
    image: str          # dataURI 或裸 base64
    targets: list[str]  # 类别名列表


def _to_pil(image_b64: str) -> Image.Image:
    if "," in image_b64 and image_b64.strip().startswith("data:"):
        image_b64 = image_b64.split(",", 1)[1]
    im = Image.open(io.BytesIO(base64.b64decode(image_b64))).convert("RGB")
    w, h = im.size
    if max(w, h) > MAX_DIM:
        s = MAX_DIM / max(w, h)
        im = im.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)
    return im


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": MODEL_ID, "object": "model",
                                        "owned_by": "la-hybrid"}]}


@app.get("/health_generate")
@app.get("/health")
def health():
    if _state["loaded"]:
        return JSONResponse({"status": "ok"}, status_code=200)
    return JSONResponse({"status": "loading", "error": _state["error"]}, status_code=503)


_infer_lock = threading.Lock()


@app.post("/detect")
def detect(req: DetectReq):
    if not _state["loaded"]:
        return JSONResponse({"error": "model loading"}, status_code=503)
    im = _to_pil(req.image)
    query = "</c>".join(t.strip() for t in req.targets if t.strip())
    # 8GB 卡并发推理会 OOM（批量标注多线程会同时打进来），串行排队
    with _infer_lock:
        texts = generate_batch_hybrid([(im, query)])
    return {"text": texts[0] if texts else "", "stats": get_last_hybrid_stats()}


if __name__ == "__main__":
    import threading
    import uvicorn
    threading.Thread(target=_load_engine, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=30000, log_level="warning")
