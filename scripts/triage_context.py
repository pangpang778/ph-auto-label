"""Trusted read-only context builder for the native triage bridge.

Runs in the analysis job *before* Claude. It reads the triggering event and
target metadata through the GitHub API and writes two fixed files:

  <out_dir>/triage-context.json   bounded metadata, body, comments, diff flags
  <out_dir>/triage-diff.patch     bounded PR diff (empty/absent for Issues)

Claude is told to read these files as untrusted data and never as
instructions. The files are produced by a trusted workflow step (the analyze
job), not by the PR, and their paths are fixed, never model-controlled.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

try:  # installed as a package (pytest)...
    from scripts.triage_adapter import GitHubClient
except ImportError:  # ...or run directly as a script (workflow)
    from triage_adapter import GitHubClient

DIFF_MAX_BYTES = 1_000_000  # ponytail: single cap; raise via env TRIAGE_DIFF_MAX_BYTES
CONTEXT_MAX_BODY_BYTES = 40_000
CONTEXT_MAX_COMMENT_BYTES = 8_000
OUT_FILENAMES = ("triage-context.json", "triage-diff.patch")


@dataclass(frozen=True)
class Context:
    context_path: str
    diff_path: str


def _bounded_text(raw: str, limit: int) -> str:
    if len(raw) <= limit:
        return raw
    return raw[:limit] + "\n[...truncated...]"


def build_context(
    client: GitHubClient,
    repository: str,
    target_type: str,
    target_number: int,
    head_sha: str | None,
    out_dir: str,
    event_key: str,
) -> Context:
    """Fetch target metadata + (for PRs) a bounded diff and write the files."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    item = client.get_issue_or_pr(target_number)
    title = item.get("title", "")
    body = _bounded_text(item.get("body") or "", CONTEXT_MAX_BODY_BYTES)
    author = {"login": item.get("user", {}).get("login", "")}
    labels = [lbl.get("name", "") for lbl in item.get("labels", [])]
    comments = []
    try:
        for c in client.list_comments(target_number):
            comments.append(
                {
                    "author": c.get("user", {}).get("login", ""),
                    "body": _bounded_text(c.get("body", ""), CONTEXT_MAX_COMMENT_BYTES),
                }
            )
    except Exception:  # noqa: BLE001  comments are best-effort context only
        comments.append({"author": "", "body": "[comments unavailable]"})

    diff_available = False
    diff_complete = True
    diff_text = ""
    if target_type == "pr":
        diff_text = client.pull_diff(target_number)
        diff_available = bool(diff_text)
        diff_max = int(os.environ.get("TRIAGE_DIFF_MAX_BYTES", str(DIFF_MAX_BYTES)))
        if len(diff_text) > diff_max:
            diff_text = diff_text[:diff_max] + "\n[...diff truncated...]\n"
            diff_complete = False

    context = {
        "repository": repository,
        "target_type": target_type,
        "target_number": target_number,
        "event_key": event_key,
        "head_sha": head_sha,
        "title": title,
        "body": body,
        "author": author,
        "labels": labels,
        "comments": comments,
        "diff_available": diff_available,
        "diff_complete": diff_complete,
        "diff_truncated": not diff_complete,
        "diff_patch_file": OUT_FILENAMES[1],
    }

    ctx_path = out / OUT_FILENAMES[0]
    ctx_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    diff_path = out / OUT_FILENAMES[1]
    diff_path.write_text(diff_text, encoding="utf-8")
    return Context(str(ctx_path), str(diff_path))


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN")
    api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    out_dir = os.environ.get("TRIAGE_CONTEXT_DIR", "")
    target_type = os.environ.get("TARGET_TYPE", "")
    target_number = int(os.environ.get("TARGET_NUMBER", "0") or 0)
    head_sha = os.environ.get("HEAD_SHA", "") or None
    event_key = os.environ.get("EVENT_KEY", "")
    if not (token and repository and out_dir and target_type and target_number):
        print("missing required context env", file=sys.stderr)
        return 1
    client = GitHubClient(token, api_url, repository)
    ctx = build_context(
        client, repository, target_type, target_number, head_sha, out_dir, event_key
    )
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        complete = (
            json.loads(Path(ctx.context_path).read_text(encoding="utf-8")).get("diff_complete")
            is not False
        )
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write(f"diff_complete={str(complete).lower()}\n")
    print(f"context: {ctx.context_path} diff: {ctx.diff_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())