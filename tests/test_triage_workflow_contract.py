from pathlib import Path

import pytest


WORKFLOW_DIR = Path(__file__).resolve().parents[1] / ".github" / "workflows"
WORKFLOW = WORKFLOW_DIR / "triage-pr-ci.md"


@pytest.mark.unit
def test_post_ci_trigger_covers_all_possible_pr_head_branches():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    workflow_run = workflow.split("  pull_request_target:", 1)[0]

    assert "  workflow_run:" in workflow_run
    assert "    branches: ['**']" in workflow_run


@pytest.mark.unit
def test_implementation_workflow_is_dispatchable_and_requires_the_execution_lock():
    workflow = (WORKFLOW_DIR / "implementation.md").read_text(encoding="utf-8")

    assert "  workflow_dispatch:" in workflow
    assert "max-turns: 32" in workflow
    assert "max-ai-credits: 3000" in workflow
    assert "target_number:" in workflow
    assert "event_key:" in workflow
    assert "ready-for-agent` and `agent-running` labels" in workflow
    assert "PH_AUTO_LABEL_TARGET: issue:" in workflow
    assert "create-pull-request:" in workflow
    assert "protected-files: fallback-to-issue" in workflow


def test_triage_dispatch_uses_the_ci_trigger_identity():
    shared = (WORKFLOW_DIR / "shared" / "triage-safe-job.md").read_text(encoding="utf-8")
    conversation = (WORKFLOW_DIR / "triage-conversation.md").read_text(encoding="utf-8")
    pr_ci = (WORKFLOW_DIR / "triage-pr-ci.md").read_text(encoding="utf-8")

    assert "GH_AW_CI_TRIGGER_TOKEN: ${{ secrets.GH_AW_CI_TRIGGER_TOKEN }}" in shared
    assert "GH_AW_CI_TRIGGER_TOKEN: ${{ secrets.GH_AW_CI_TRIGGER_TOKEN }}" in conversation
    assert "GH_AW_CI_TRIGGER_TOKEN: ${{ secrets.GH_AW_CI_TRIGGER_TOKEN }}" in pr_ci


@pytest.mark.unit
def test_frontier_workflow_only_runs_after_a_merged_pull_request():
    workflow = (WORKFLOW_DIR / "frontier-advance.yml").read_text(encoding="utf-8")

    assert "types: [closed]" in workflow
    assert "github.event.pull_request.merged == true" in workflow
    assert "scripts/frontier_advance.py" in workflow
