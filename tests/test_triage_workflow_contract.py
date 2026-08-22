from pathlib import Path

import pytest


WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "triage-pr-ci.md"


@pytest.mark.unit
def test_post_ci_trigger_covers_all_possible_pr_head_branches():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    workflow_run = workflow.split("  pull_request_target:", 1)[0]

    assert "  workflow_run:" in workflow_run
    assert "    branches: ['**']" in workflow_run
