"""ph-auto-label Flask application package.

Exposes ``create_app()`` (application factory) and a legacy re-export shim so
``import app as training_app`` + ``training_app.<symbol>`` keep resolving for the
test suite. Route handlers live in ``app/blueprints/*``; business logic in
``app/services/*``; JSON I/O in ``app/repositories/*``; shared helpers in
``app/common/*``.
"""
import json
import os

from flask import Flask
from flask_cors import CORS

from app.common.config import PATHS, VIDEO_EXTENSIONS
from app.repositories.model_registry_repo import (
    get_active_model,
    get_models_dir,
    get_models_install_path,
)
from app.services.training_service import (
    _artifact_allowed_roots,
    build_yolo_training_dataset,
    normalize_split_config,
    run_training_job,
)
from plugins.sam3_service import sam3_service
from plugins.video_inference import video_inference_service

__all__ = [
    "create_app", "app", "PATHS", "VIDEO_EXTENSIONS",
    "video_inference_service", "sam3_service",
    "normalize_split_config", "build_yolo_training_dataset", "run_training_job",
    "get_active_model", "get_models_dir", "get_models_install_path",
    "_artifact_allowed_roots",
]

# app/__init__.py -> app -> repo root (templates/static live at repo root).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ensure_dirs():
    os.makedirs(PATHS["uploads"], exist_ok=True)
    os.makedirs(os.path.dirname(PATHS["annotations"]), exist_ok=True)


def _init_data_files():
    """Create default JSON data files on first run (preserves original formatting)."""
    if not os.path.exists(PATHS["annotations"]):
        with open(PATHS["annotations"], "w") as f:
            json.dump({}, f)
    if not os.path.exists(PATHS["classes"]):
        default_classes = [
            {"name": "person", "color": "#3aa757"},
            {"name": "car", "color": "#4c9ffd"},
            {"name": "animal", "color": "#ff9d00"},
        ]
        with open(PATHS["classes"], "w") as f:
            json.dump(default_classes, f)
    pretty = {
        "timelines": {},
        "scenario": {"scenario_id": "", "name": "", "steps": [], "object_classes": [], "action_labels": []},
        "train_jobs": [],
        "model_registry": [],
        "active_model": {"model_id": "", "model_name": "", "model_path": ""},
        "training_splits": {},
    }
    for key, default in pretty.items():
        if not os.path.exists(PATHS[key]):
            with open(PATHS[key], "w", encoding="utf-8") as f:
                json.dump(default, f, ensure_ascii=False, indent=2)


def create_app():
    """Application factory: Flask + CORS + blueprints + dir/file init."""
    app = Flask(
        __name__,
        template_folder=os.path.join(_REPO_ROOT, "templates"),
        static_folder=os.path.join(_REPO_ROOT, "static"),
    )
    CORS(app)

    # Deferred imports avoid any module-load cycles.
    from app.blueprints.annotation import bp as annotation_bp
    from app.blueprints.models import bp as models_bp
    from app.blueprints.training import bp as training_bp
    from app.blueprints.video_timeline import bp as video_timeline_bp
    from app.blueprints.video_test import bp as video_test_bp
    app.register_blueprint(annotation_bp)
    app.register_blueprint(models_bp)
    app.register_blueprint(training_bp)
    app.register_blueprint(video_timeline_bp)
    app.register_blueprint(video_test_bp)

    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 最大上传2GB

    _ensure_dirs()
    _init_data_files()
    return app


# Module-level app instance so `import app as training_app` + `training_app.app`
# resolve (legacy shim for the test suite).
app = create_app()
