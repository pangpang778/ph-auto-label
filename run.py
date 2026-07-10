"""ph-auto-label entry point.

Run with: ``python run.py``

Security: the Werkzeug debugger is only enabled when FLASK_DEBUG=1, and the
reloader is disabled (it breaks non-interactive/scripted launches with exit
127). The server binds to 127.0.0.1 by default; set FLASK_HOST to bind
elsewhere (e.g. ``0.0.0.0``) only when you intentionally want external access.
"""
import os

from app import create_app
from plugins.sam3_service import sam3_service

app = create_app()


if __name__ == "__main__":
    try:
        sam3_service.load_model()
        sam3_service.warmup()
    except Exception as e:
        print(f"[WARNING] SAM3 model failed to load: {e}")
        print("SAM3 auto-annotation will not be available. Set SAM3_MODEL_PATH env var or place model at plugins/sam3/models/model.pt")

    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    host = os.environ.get("FLASK_HOST", "127.0.0.1")
    port = int(os.environ.get("FLASK_PORT", "5000"))
    app.run(debug=debug, use_reloader=False, host=host, port=port)
