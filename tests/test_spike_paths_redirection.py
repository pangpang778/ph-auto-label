"""Phase 0 spike proof — PATHS-registry redirection for all 3 path classes.

Closes Risk #1 (monkeypatch-target hazard) BEFORE Phase 1 extraction. Asserts
that app code reading ``PATHS[key]`` resolves to the fixture's ``tmp_path``,
not the repo-root defaults, for all three path classes:

  - annotation JSON  (PATHS['annotations'] / ['classes'] / ['training_splits'] / ...)
  - uploads          (PATHS['uploads'])
  - root-derived     (PATHS['root'] / ['train_work'] / ['plugins_yolo11'] / ['plugins_sam3_models'])

The fixture mutates the shared ``PATHS`` dict in place via
``monkeypatch.setitem(training_app.PATHS, ...)``. ``training_app.PATHS`` IS
``path_config.PATHS`` (same object, imported by reference), so the mutation
propagates across the module boundary — the property re-exported constants
would NOT have, which is the reason the plan chose a registry over re-exported
constants.
"""
from pathlib import Path

import pytest

import app as training_app


@pytest.mark.integration
def test_spike_all_path_classes_redirect_to_tmp_path(isolated_app, tmp_path):
    repo_root = Path(training_app.__file__).resolve().parent
    assert repo_root != tmp_path  # sanity

    # Class 1: annotation JSON
    for key in ("annotations", "classes", "training_splits", "train_jobs",
                "model_registry", "active_model", "timelines", "scenario"):
        val = training_app.PATHS[key]
        assert str(val).startswith(str(tmp_path)), f"{key}={val!r} not under tmp_path"

    # Class 2: uploads
    assert str(training_app.PATHS["uploads"]).startswith(str(tmp_path))

    # Class 3: root-derived
    assert str(training_app.PATHS["root"]) == str(tmp_path)
    for key in ("train_work", "plugins_yolo11", "plugins_sam3_models"):
        assert str(training_app.PATHS[key]).startswith(str(tmp_path)), f"{key} not under tmp_path"


@pytest.mark.integration
def test_spike_app_functions_read_redirected_paths(isolated_app, tmp_path):
    """App-level functions that read PATHS return tmp_path-based values.

    This is the cross-module propagation proof: the fixture mutated
    path_config.PATHS, and app.py functions reading PATHS see the mutation
    (because they hold the same dict object by reference)."""
    install_path = training_app.get_models_install_path()  # reads PATHS['plugins_yolo11']
    assert install_path.startswith(str(tmp_path)), install_path

    models_dir = training_app.get_models_dir()  # install_path + '/models'
    assert models_dir.startswith(str(tmp_path)), models_dir
    assert models_dir.replace("\\", "/").endswith("plugins/yolo11/models"), models_dir

    roots = training_app._artifact_allowed_roots()  # [PATHS['train_work'], get_models_dir()]
    assert len(roots) == 2
    assert all(r.startswith(str(tmp_path)) for r in roots), roots
    assert roots[0].replace("\\", "/").endswith("static/train_work"), roots[0]


@pytest.mark.integration
def test_spike_annotation_read_resolves_to_tmp_path(isolated_app, tmp_path):
    """GET /api/classes reads the fixture-seeded tmp_path classes.json (containing
    'part'), NOT the repo-root default (person/car/animal). Proves the annotation
    JSON path class is redirected end-to-end through a real request."""
    client = isolated_app.test_client()
    resp = client.get("/api/classes")
    assert resp.status_code == 200
    names = [c["name"] for c in resp.get_json()]
    assert "part" in names, names      # fixture seed lives in tmp_path
    assert "person" not in names, names  # repo-root default must NOT leak in
