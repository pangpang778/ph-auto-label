"""ph-auto-label Flask application package.

Exposes ``create_app()`` (application factory) and a legacy re-export shim so
``import app as training_app`` + ``training_app.<symbol>`` keep resolving for the
test suite. Route handlers live in ``app/blueprints/*``; business logic in
``app/services/*``; JSON I/O in ``app/repositories/*``; shared helpers in
``app/common/*``.

The package NO LONGER constructs a module-level Flask app instance - the
factory runs only when ``create_app()`` is explicitly called (by ``run.py`` or
the ``isolated_app`` test fixture). This prevents filesystem writes
(``_ensure_dirs``/``_init_data_files``) from firing as a side effect of
``import app`` and avoids creating two app instances.
"""
import json
import logging
import os

from flask import Flask
from flask_cors import CORS

from app.common.config import PATHS, VIDEO_EXTENSIONS
from app.services import models_service
from app.services.training_service import (
    _artifact_allowed_roots,
    build_yolo_training_dataset,
    normalize_split_config,
)
from plugins.sam3_service import sam3_service
from plugins.video_inference import video_inference_service

__all__ = [
    "create_app", "PATHS", "VIDEO_EXTENSIONS",
    "video_inference_service", "sam3_service",
    # DEPRECATED: compat re-exports for tests using ``training_app.<symbol>``;
    # migrate tests to import from app.services.training_service directly.
    "normalize_split_config", "build_yolo_training_dataset",
    "_artifact_allowed_roots",
    "get_active_model", "get_models_dir", "get_models_install_path",
]


# --- Backward-compat re-exports -------------------------------------------
# These names historically lived on the top-level ``app`` package. Callers
# (e.g. tests via ``training_app.get_models_dir()``) still import them from
# here, so they are preserved - but they now DELEGATE to the service layer
# instead of importing straight from ``app.repositories.model_registry_repo``,
# honoring the closed-world contract documented in
# ``app/services/models_service.py`` (cross-domain callers must go through the
# service, never the repo directly).
def get_active_model():
    """Delegate to :func:`app.services.models_service.get_active_model`."""
    return models_service.get_active_model()


def get_models_dir():
    """Delegate to :func:`app.services.models_service.get_models_dir`."""
    return models_service.get_models_dir()


def get_models_install_path():
    """Delegate to :func:`app.services.models_service.get_models_install_path`."""
    return models_service.get_models_install_path()

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

    # --- Security config -------------------------------------------------
    # SECRET_KEY: honor an explicit env value; otherwise random per start with
    # a warning (sessions reset on restart). Production MUST set SECRET_KEY.
    secret = os.environ.get("SECRET_KEY")
    if not secret:
        secret = os.urandom(32)
        app.logger.warning("SECRET_KEY 未设置，使用随机值，session 将在重启后失效")
    app.config["SECRET_KEY"] = secret

    # CORS origins configurable via ALLOWED_ORIGINS (comma-separated). Fallback
    # is local-dev only; PRODUCTION MUST set ALLOWED_ORIGINS explicitly.
    allowed = os.environ.get("ALLOWED_ORIGINS")
    cors_origins = (
        [o.strip() for o in allowed.split(",") if o.strip()]
        if allowed else ["http://127.0.0.1:5000", "http://localhost:5000"]
    )
    CORS(app, origins=cors_origins)

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

    # Defensive orphan-job recovery: any training job left "running" by a
    # previous crash is reset so it no longer blocks the UI. Wrapped so a
    # failure here can never break app startup.
    try:
        from app.services.training_service import recover_orphaned_jobs
        recover_orphaned_jobs()
    except Exception as exc:  # never let cleanup break app startup
        logging.getLogger(__name__).warning(
            "orphan recovery skipped: %s", exc
        )

    return app
