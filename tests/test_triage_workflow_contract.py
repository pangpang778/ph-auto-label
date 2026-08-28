"""Workflow contract tests for the native Matt triage bridge.

These assert the fail-closed structural guarantees of `.github/workflows/triage.yml`
(invocation pin, read-only analysis surface, plugin catalog pin, structured
result, single-writer apply) without needing a GitHub runner.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "triage.yml"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
SCHEMA = ROOT / "schema" / "triage-output.schema.json"

CCA = "anthropics/claude-code-action@a60f3e1db3edbceed2b1e6c6a9d34c36b8a15eba"
PLUGIN_SHA = "6acc160e4e0cd062dbbbd7a1b26ae92855edf07e"


@pytest.fixture(autouse=True)
def _require_new_workflow():
    assert WORKFLOW.exists(), "native triage.yml must be the active workflow"
    return WORKFLOW.read_text(encoding="utf-8")


def test_invokes_pinned_claude_code_action():
    assert CCA in WORKFLOW.read_text(encoding="utf-8")


def test_prompt_invokes_native_matt_triage_skill():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "/mattpocock-skills:engineering/triage" in text


def test_analysis_surface_is_read_only_and_restricted():
    text = WORKFLOW.read_text(encoding="utf-8")
    # Exact built-in tool restriction and MCP deny are present.
    assert '--tools "Read,Glob,Grep,Skill"' in text
    assert "--disallowedTools " in text
    assert "--add-dir" in text
    # The analysis job holds no write permission.
    assert "issues: read" in text
    assert "pull-requests: read" in text
    assert "issues: write" in text  # apply path only
    assert "--json-schema" in text
    # No unapproved executable surface exposed to the model.
    for banned in ('Task",', "--permission-mode bypassPermissions", "dangerouslySkipPermissions"):
        assert banned not in text


def test_marketplace_catalog_is_pinned_and_loaded():
    import json

    catalog = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    assert catalog["name"] == "ph-auto-label"
    assert isinstance(catalog.get("owner"), dict)  # claude CLI requires owner
    (entry,) = catalog["plugins"]
    assert entry["name"] == "mattpocock-skills"
    assert entry["version"] == "1.2.3"
    assert entry["source"] == {
        "source": "github",
        "repo": "mattpocock/skills",
        "sha": PLUGIN_SHA,
    }
    text = WORKFLOW.read_text(encoding="utf-8")
    # claude-code-action mounts the checked-out repo dir as a local marketplace.
    assert "plugin_marketplaces: ${{ github.workspace }}" in text
    assert "plugins: mattpocock-skills@ph-auto-label" in text


def test_structured_result_is_consumed_without_shell_interpolation():
    text = WORKFLOW.read_text(encoding="utf-8")
    # Model output is handed over via environment, not inlined into shell source.
    assert "TRIAGE_RESULT: ${{ steps.triage.outputs.structured_output }}" in text
    assert 'printf \'%s\' "$TRIAGE_RESULT"' in text


def test_apply_is_the_single_writer_through_the_adapter():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "scripts/triage_adapter.py" in text
    assert "scripts/triage_preflight.py" in text
    assert "scripts/triage_context.py" in text
    # The apply job is the only place that mutates issues.
    assert "issues: write" in text
    # No writer capability is ever handed to analysis.
    assert "actions: write" not in text
    assert "workflow_dispatch" not in text
    assert "pull-requests: write" not in text


@pytest.mark.unit
def test_hidden_schema_matches_adapter_contract():
    import json

    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["category", "state", "reason", "comment"]
    assert schema["properties"]["category"]["enum"] == ["bug", "enhancement"]
    assert set(schema["properties"]["state"]["enum"]) == {
        "needs-triage",
        "needs-info",
        "ready-for-agent",
        "ready-for-human",
        "wontfix",
    }


def test_serialises_runs_per_target_and_never_cancels_in_progress():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "cancel-in-progress: false" in text
    assert "concurrency:" in text