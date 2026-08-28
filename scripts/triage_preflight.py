"""Preflight gate for the native triage bridge.

Read-only wrapper around ``triage_adapter.decide_preflight``. Runs in the
repository's trusted preflight job (contents/issues/pull-requests: read only),
before any model call. It writes the run/analyze/reconcile decision and the
trusted target identity to ``GITHUB_OUTPUT``; a skip means the analyze and
apply jobs are disabled and GitHub is never written.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from triage_adapter import GitHubClient, TriageError, decide_preflight


def _github_outputs(path: str | None, mapping: dict[str, str]) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in mapping.items():
            handle.write(f"{key}={value}\n")


def main() -> int:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        print("GITHUB_EVENT_PATH is missing", file=sys.stderr)
        return 1
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN", "")
    api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    writer_bot_id = os.environ.get("TRIAGE_WRITER_BOT_ID", "").strip()
    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        client = GitHubClient(token, api_url, repository)
        decision = decide_preflight(event_name, event, client=client, writer_bot_id=writer_bot_id)
    except (OSError, json.JSONDecodeError, TriageError):
        decision = {
            "run": False,
            "analyze": False,
            "reconcile": False,
            "reason": "context_failure",
            "target_type": "",
            "target_number": 0,
            "event_key": "",
            "head_sha": "",
        }
    flat = {
        "should_run": str(decision["run"]).lower(),
        "should_analyze": str(decision["analyze"]).lower(),
        "should_reconcile": str(decision["reconcile"]).lower(),
        "reason": decision["reason"],
        "target_type": decision["target_type"],
        "target_number": str(decision["target_number"]),
        "event_key": decision["event_key"],
        "head_sha": decision["head_sha"],
    }
    _github_outputs(os.environ.get("GITHUB_OUTPUT"), flat)
    print(f"preflight: {flat}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
