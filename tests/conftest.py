"""Shared pytest fixtures for ph-auto-label.

The ``isolated_app`` fixture redirects every ``PATHS[key]`` to a per-test
``tmp_path`` via ``monkeypatch.setitem`` (auto-reverts at teardown). Because
``PATHS`` is a single dict imported by reference across the codebase, the
mutation propagates to every reader — in ``app.py`` now and in
``app/repositories`` + ``app/services`` after the Phase 1 extraction. This is
the mechanism that closes the cross-module monkeypatch hazard (Risk #1).
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as training_app  # noqa: E402


@pytest.fixture()
def isolated_app(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    annotations_dir = tmp_path / "static" / "annotations"
    train_work_dir = tmp_path / "static" / "train_work"
    yolo11_dir = tmp_path / "plugins" / "yolo11"
    models_dir = yolo11_dir / "models"
    sam3_models_file = tmp_path / "plugins" / "sam3" / "models" / "model.pt"
    for path in (upload_dir, annotations_dir, train_work_dir, models_dir, sam3_models_file.parent):
        path.mkdir(parents=True, exist_ok=True)

    files = {
        "annotations": (annotations_dir / "annotations.json", {}),
        "classes": (annotations_dir / "classes.json", [{"name": "part", "color": "#fff"}]),
        "training_splits": (annotations_dir / "training_splits.json", {}),
        "train_jobs": (annotations_dir / "train_jobs.json", []),
        "model_registry": (annotations_dir / "model_registry.json", []),
        "active_model": (annotations_dir / "active_model.json",
                         {"model_id": "", "model_name": "", "model_path": ""}),
        "timelines": (annotations_dir / "timelines.json", {}),
        "scenario": (annotations_dir / "sop_scenario.json",
                     {"scenario_id": "", "name": "", "steps": [], "object_classes": [], "action_labels": []}),
    }
    for key, (path, default) in files.items():
        path.write_text(json.dumps(default), encoding="utf-8")
        monkeypatch.setitem(training_app.PATHS, key, str(path))

    monkeypatch.setitem(training_app.PATHS, "uploads", str(upload_dir))
    monkeypatch.setitem(training_app.PATHS, "root", str(tmp_path))
    monkeypatch.setitem(training_app.PATHS, "train_work", str(train_work_dir))
    monkeypatch.setitem(training_app.PATHS, "plugins_yolo11", str(yolo11_dir))
    monkeypatch.setitem(training_app.PATHS, "plugins_sam3_models", str(sam3_models_file))

    # Build the app AFTER PATHS are redirected so _ensure_dirs/_init_data_files
    # operate against tmp_path (the data files above already exist, so
    # _init_data_files skips re-creating them). create_app() is the only
    # public entry now - there is no module-level app instance.
    yield training_app.create_app()
