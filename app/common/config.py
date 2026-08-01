"""Mutable path registry for ph-auto-label.

A single dict shared by reference across the application. Production code reads
``PATHS[key]`` at call time; tests mutate ``PATHS[key]`` (via
``monkeypatch.setitem(app.PATHS, ...)``) to redirect I/O to a per-test
``tmp_path``. Because every holder imports the *same* dict object, in-place
mutations propagate across the module boundary - this is what closes the
monkeypatch-target hazard (Risk #1) after extraction.

Phase 1 home (moved from the Phase 0 spike module ``path_config.py``).
"""
import os

# app/common/config.py -> app/common -> app -> repo root
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PATHS = {
    "root": _ROOT,
    "uploads": os.path.join(_ROOT, "uploads"),
    "annotations": os.path.join(_ROOT, "static", "annotations", "annotations.json"),
    "classes": os.path.join(_ROOT, "static", "annotations", "classes.json"),
    "train_jobs": os.path.join(_ROOT, "static", "annotations", "train_jobs.json"),
    "model_registry": os.path.join(_ROOT, "static", "annotations", "model_registry.json"),
    "active_model": os.path.join(_ROOT, "static", "annotations", "active_model.json"),
    "training_splits": os.path.join(_ROOT, "static", "annotations", "training_splits.json"),
    "timelines": os.path.join(_ROOT, "static", "annotations", "timelines.json"),
    "scenario": os.path.join(_ROOT, "static", "annotations", "sop_scenario.json"),
    "train_work": os.path.join(_ROOT, "static", "train_work"),
    "evaluations": os.path.join(_ROOT, "static", "annotations", "evaluations.json"),
    "plugins_yolo11": os.path.join(_ROOT, "plugins", "yolo11"),
    "plugins_sam3_models": os.path.join(_ROOT, "plugins", "sam3", "models", "model.pt"),
}

VIDEO_EXTENSIONS = ('.mp4', '.avi', '.mov', '.mkv', '.webm', '.m4v')
