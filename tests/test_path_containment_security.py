"""Security regression tests for path containment (C1/C2).

C1: user-controlled install_path must not escape PATHS['root']. Previously
annotation_inference_service._resolve_install_path passed absolute paths
through unchecked, allowing an attacker-chosen absolute install_path to load
a .pt from anywhere (RCE via torch.load inside YOLO()). models_service was
already hardened (H2b); these tests lock BOTH sides so the asymmetry cannot
re-emerge.

C2: parse_sop_scenario must reject scenario_dir outside PATHS['root'].
Previously it did os.path.abspath with no containment, reading arbitrary
local directories (process.yaml / labels/*.yaml) and echoing the absolute
path in errors.
"""
import sys
from pathlib import Path

import pytest

import app as training_app  # noqa: E402
from app.common.path_safety import PathSafetyError  # noqa: E402
from app.services.annotation_inference_service import (  # noqa: E402
    AnnotationError,
    _resolve_install_path as inference_resolve_install_path,
)
from app.services.models_service import resolve_install_path  # noqa: E402
from app.services.video_timeline_service import parse_sop_scenario  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------------------------------------------------------------------------
# C1: inference-side install_path containment (the side that was missing)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_inference_resolve_install_path_rejects_absolute_outside_root(isolated_app):
    with pytest.raises(AnnotationError) as exc:
        inference_resolve_install_path("C:/Windows/System32")
    assert exc.value.status == 400


@pytest.mark.unit
def test_inference_resolve_install_path_rejects_traversal(isolated_app):
    with pytest.raises(AnnotationError):
        inference_resolve_install_path("../../etc/passwd")


@pytest.mark.unit
def test_inference_resolve_install_path_accepts_relative_under_root(isolated_app):
    resolved = inference_resolve_install_path("plugins/yolo11")
    assert resolved.startswith(str(training_app.PATHS["root"]))


# ---------------------------------------------------------------------------
# C1: models-side install_path containment (regression lock on the H2b fix)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_models_resolve_install_path_rejects_absolute_outside_root(isolated_app):
    with pytest.raises(PathSafetyError):
        resolve_install_path("C:/Windows/System32")


@pytest.mark.unit
def test_models_resolve_install_path_rejects_traversal(isolated_app):
    with pytest.raises(PathSafetyError):
        resolve_install_path("../../etc/passwd")


# ---------------------------------------------------------------------------
# C2: scenario_dir containment
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_parse_sop_scenario_rejects_absolute_out_of_root(isolated_app):
    with pytest.raises(ValueError):
        parse_sop_scenario("C:/Windows/System32")


@pytest.mark.unit
def test_parse_sop_scenario_rejects_traversal(isolated_app):
    with pytest.raises(ValueError):
        parse_sop_scenario("../../etc/passwd")


@pytest.mark.unit
def test_parse_sop_scenario_accepts_under_root(isolated_app):
    root = Path(training_app.PATHS["root"])
    scen = root / "scen_under_root"
    scen.mkdir()
    (scen / "process.yaml").write_text(
        "process:\n  id: s1\n  name: Scenario1\nsteps: []\n", encoding="utf-8"
    )
    result = parse_sop_scenario(str(scen))
    assert result["scenario_id"] == "s1"
