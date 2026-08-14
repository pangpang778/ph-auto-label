---
on:
  issues:
    types: [labeled]
    names: [ready-for-dev]
  roles: [admin]

permissions:
  contents: read
  issues: read
  pull-requests: read

model: ${{ vars.GH_AW_LLM_MODEL }}
timeout-minutes: 45
max-turns: 20
models:
  default-ai-credits-pricing:
    input: 0.000001
    output: 0.000001
engine:
  id: claude
  env:
    ANTHROPIC_BASE_URL: https://ark.cn-beijing.volces.com/api/coding
    ANTHROPIC_API_KEY: ${{ secrets.GH_AW_ANTHROPIC_API_KEY }}
    PYTHONPATH: /tmp/gh-aw/agent-python
tools:
  github:
    toolsets: [repos, issues, pull_requests]

runtimes:
  python:
    version: "3.12"

network:
  allowed:
    - defaults
    - python
    - ark.cn-beijing.volces.com

steps:
  - name: Prepare focused Python test environment
    shell: bash
    run: |
      set -euo pipefail
      agent_python="/tmp/gh-aw/agent-python"
      rm -rf "$agent_python"
      mkdir -p "$agent_python"
      python -m pip install \
        --disable-pip-version-check \
        --no-cache-dir \
        --target "$agent_python" \
        "Flask>=3.1.0" \
        "flask-cors>=6.0.0" \
        "opencv-python>=4.10.0" \
        "pillow>=10.0.0" \
        "numpy>=2.0.0" \
        "psutil>=5.9.0" \
        "requests>=2.28.0" \
        "filelock>=3.13.0" \
        "PyYAML>=6.0" \
        pytest \
        ruff

safe-outputs:
  add-comment:
    max: 1
  create-pull-request:
    max: 1
    base-branch: master
    branch-prefix: feature/issue-
    preserve-branch-name: true
    title-prefix: "[ai] "
    labels: [ai-generated]
    draft: true
    fallback-as-issue: false
    protected-files: fallback-to-issue
---

# Issue To Draft PR

The triggering issue is a human-approved implementation contract. Implement it
only when it contains all of these non-empty sections:

- `## Background`
- `## Goal`
- `## Non-goals`
- `## Acceptance criteria`
- `## Constraints`
- `## Verification`

If any section is missing, ambiguous, contradictory, or requests a protected
file change, do not create a pull request. Post one concise comment identifying
the missing decision or conflict and stop.

Before changing code:

1. Read the entire triggering issue and its comments.
2. Inspect repository instructions, the current implementation, and relevant
   tests before choosing an implementation.
3. Respect every listed constraint. Do not alter authentication, authorization,
   secrets, CI workflows, dependency manifests, or data migrations unless the
   issue explicitly requests it. Protected files must fall back to human review.
4. Keep the change scoped to the issue. Do not perform unrelated refactors.

Implementation requirements:

1. Create a branch named `feature/issue-<issue-number>-<short-slug>`.
2. Implement the smallest complete solution that meets every acceptance
   criterion.
3. Add or update focused regression tests.
4. Run the verification commands stated in the issue. If the issue supplies no
   commands, run `ruff check app tests` and `pytest`.
5. Do not create a PR if required verification fails. Post one comment with the
   failure, evidence, and the smallest recommended next step.
6. Create exactly one draft PR when verification succeeds. Its body must link
   the issue, list changed behavior, list verification commands and results,
   and call out remaining risks. Do not merge the PR.
7. Reuse the deterministic Python test environment prepared by the workflow.
   Do not create virtual environments, probe alternate language runtimes,
   upgrade tooling, install packages, or run the full application dependency
   set. Use the issue's verification commands as written; if `ruff` or
   `pytest` is not on PATH, invoke it as `python -m ruff` or `python -m pytest`.

Never include credentials, secret values, private endpoints, or model settings
in commits, PR bodies, issue comments, logs, or agent output.
