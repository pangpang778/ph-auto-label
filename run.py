"""ph-auto-label entry point.

Run with: ``python run.py``
"""
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
    app.run(debug=True, host="0.0.0.0", port=5000)
