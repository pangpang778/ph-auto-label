---
name: Triage Conversations
description: Classify new Issues and human Issue/PR comments, then route validated outcomes safely.
strict: true
private: true
inlined-imports: true
on:
  issues:
    types: [opened]
  issue_comment:
    types: [created]
  roles: all
  skip-bots: [github-actions, agentic-workflows, dependabot, renovate, copilot]
  reaction: none
  status-comment: false
permissions:
  contents: read
  issues: read
  pull-requests: read
concurrency:
  group: "triage-target-${{ github.repository }}-${{ github.event.issue.pull_request && 'pull_request' || 'issue' }}-${{ github.event.issue.number || github.event.pull_request.number || github.run_id }}"
  cancel-in-progress: false
checkout:
  ref: ${{ github.event.repository.default_branch }}
  sparse-checkout: |
    .github
    .agents
    .claude
    .codex
    .gemini
    .pi
model: ${{ vars.GH_AW_LLM_MODEL }}
timeout-minutes: 30
max-turns: 8
max-ai-credits: 500
max-daily-ai-credits: 3000
models:
  default-ai-credits-pricing:
    input: 0.000001
    output: 0.000001
engine:
  id: claude
  permission-mode: auto
  env:
    ANTHROPIC_BASE_URL: https://ark.cn-beijing.volces.com/api/coding
    ANTHROPIC_API_KEY: ${{ secrets.GH_AW_ANTHROPIC_API_KEY }}
skills:
  - mattpocock/skills/skills/engineering/triage@5b15a47f2d7150f545fbcacbfe381787fc0230dc
network:
  allowed: [defaults, ark.cn-beijing.volces.com]
tools:
  bash: false
  edit: false
  cli-proxy: false
  github:
    toolsets: [repos, issues, pull_requests]
safe-outputs:
  report-failure-as-issue: false
  report-failed-jobs: false
imports:
  - shared/triage-safe-job.md
jobs:
  conclusion:
    permissions:
      contents: read
      actions: write
      issues: write
      pull-requests: write
    pre-steps:
      - name: Checkout trusted default-branch validator
        uses: actions/checkout@v7
        with:
          ref: ${{ github.event.repository.default_branch }}
          persist-credentials: false
          sparse-checkout: |
            scripts/triage_conclusion.py
      - name: Apply deterministic fallback when needed
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GH_AW_CI_TRIGGER_TOKEN: ${{ secrets.GH_AW_CI_TRIGGER_TOKEN }}
          GH_AW_AGENT_OUTPUT: ""
        run: python scripts/triage_conclusion.py
---

/triage

This workflow explicitly invokes the installed upstream `triage` skill. Use its state-machine roles
and context-gathering procedure for the current target. The local read-only and safe-output policy
below overrides any upstream instruction that would require direct tracker mutations, checkout, shell,
code edits, closing, or waiting for maintainer interaction.

{{#runtime-import .github/workflows/shared/triage-policy.md}}

## Trigger-specific context

The event is either a newly opened Issue or a newly created human comment on an Issue/PR. Derive the
event key from the trusted event identity. For a new Issue use `issue:<number>:opened:<created_at>`.
For a comment use `comment:<comment_id>:created`. For a PR comment, set `target_type` to
`pull_request` and obtain the current head SHA through the read-only PR API before calling the safe
output. A comment event always starts a fresh audit comment even if the conclusion repeats.
