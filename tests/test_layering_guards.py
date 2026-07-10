"""Layering guard: blueprints must not call repository write_* helpers directly.

Enforces the H8 fix: all persistence RMW goes through service-layer update_*
helpers, never write_* from a blueprint. A bare write_* from a route handler
bypasses the atomic RMW contract and can re-introduce the read-then-write
races closed in H2/H3/H8. This test catches regressions where a route reaches
past the service layer into a full overwrite.

Import lines (``write_classes,``) are not flagged - only actual CALLS
(``write_classes(...)``), so re-exporting a name for documentation is fine.
"""
import re
from pathlib import Path

import pytest

_BLUEPRINT_DIR = Path(__file__).resolve().parents[1] / "app" / "blueprints"

_FORBIDDEN_CALL = re.compile(
    r"\b(write_annotations|write_classes|write_timelines|write_scenario|"
    r"write_train_jobs|write_model_registry|write_training_splits)\s*\("
)


@pytest.mark.unit
def test_blueprints_do_not_call_repository_write_helpers():
    offenders = []
    for py in sorted(_BLUEPRINT_DIR.glob("*.py")):
        text = py.read_text(encoding="utf-8")
        for m in _FORBIDDEN_CALL.finditer(text):
            offenders.append(f"{py.name}: {m.group(1)}(...)")
    assert not offenders, (
        "blueprints must not call repository write_* helpers directly "
        "(use service-layer update_* instead):\n  " + "\n  ".join(offenders)
    )
