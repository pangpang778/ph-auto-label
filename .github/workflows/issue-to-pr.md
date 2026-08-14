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

model: gpt-5.4
engine:
  id: codex
tools:
  github:
    toolsets: [repos, issues, pull_requests]

network: defaults

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
    github-token: ${{ secrets.GH_AW_GITHUB_TOKEN }}
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

Never include credentials, secret values, private endpoints, or model settings
in commits, PR bodies, issue comments, logs, or agent output.
