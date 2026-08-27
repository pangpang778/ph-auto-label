"""ph-auto-label entry point.

Run with: ``python run.py``

Security: the Werkzeug debugger is only enabled when FLASK_DEBUG=1, and the
reloader is disabled (it breaks non-interactive/scripted launches with exit
127). The server binds to 127.0.0.1 by default; set FLASK_HOST to bind
elsewhere (e.g. ``0.0.0.0``) only when you intentionally want external access.
"""
import os

from app import create_app

app = create_app()


if __name__ == "__main__":
    # SAM3 不再启动即预加载：8GB 显存下会把运行中的 LA/Qwen 后端挤死。
    # 首次 SAM3 请求时按需加载（detect_frame/_require_sam3_loaded 内部
    # 会先停 VLM 容器腾显存，串行互斥见 plugins/sam3_service.py）。

    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    host = os.environ.get("FLASK_HOST", "127.0.0.1")
    port = int(os.environ.get("FLASK_PORT", "5000"))
    app.run(debug=debug, use_reloader=False, host=host, port=port)
