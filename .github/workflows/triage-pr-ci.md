---
name: Triage PR After CI
description: Classify same-repository PRs after CI and fork PRs from trusted base context.
strict: true
private: true
inlined-imports: true
on:
  workflow_run:
    workflows: [CI]
    types: [completed]
    # workflow_run filters match the completed run's head branch; ci-evidence
    # narrows this broad trigger to one completed pull_request CI run.
    branches: ['**']
  pull_request_target:
    types: [opened, synchronize]
  roles: all
  skip-bots: [github-actions, agentic-workflows, dependabot, renovate, copilot]
  needs: [ci-evidence]
  reaction: none
  status-comment: false
if: >-
  needs.ci-evidence.outputs.ready == 'true' &&
  (github.event_name == 'workflow_run' ||
  (github.event_name == 'pull_request_target' &&
  github.event.pull_request.head.repo.id != github.repository_id))
permissions:
  contents: read
  actions: read
  issues: read
  pull-requests: read
concurrency:
  group: "triage-target-${{ github.repository }}-pull_request-${{ github.event.workflow_run.pull_requests[0].number || github.event.pull_request.number || github.run_id }}"
  cancel-in-progress: false
checkout:
  ref: ${{ github.event.pull_request.base.sha }}
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
    toolsets: [repos, issues, pull_requests, actions]
safe-outputs:
  report-failure-as-issue: false
  report-failed-jobs: false
imports:
  - shared/triage-safe-job.md
jobs:
  ci-evidence:
    runs-on: ubuntu-slim
    permissions:
      contents: read
      actions: read
      pull-requests: read
    timeout-minutes: 12
    outputs:
      ready: ${{ steps.evidence.outputs.ready }}
      pr_number: ${{ steps.evidence.outputs.pr_number }}
      head_sha: ${{ steps.evidence.outputs.head_sha }}
      ci_conclusion: ${{ steps.evidence.outputs.ci_conclusion }}
      ci_run_id: ${{ steps.evidence.outputs.ci_run_id }}
    steps:
      - name: Wait for matching CI evidence
        id: evidence
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITHUB_EVENT_NAME: ${{ github.event_name }}
          GITHUB_EVENT_PATH: ${{ github.event_path }}
          GITHUB_REPOSITORY: ${{ github.repository }}
        run: |
          set -euo pipefail
          if [ "$GITHUB_EVENT_NAME" = "workflow_run" ]; then
            pr_count="$(jq '.workflow_run.pull_requests | length' "$GITHUB_EVENT_PATH")"
            source_event="$(jq -r '.workflow_run.event // ""' "$GITHUB_EVENT_PATH")"
            workflow_name="$(jq -r '.workflow_run.name // ""' "$GITHUB_EVENT_PATH")"
            conclusion="$(jq -r '.workflow_run.conclusion // ""' "$GITHUB_EVENT_PATH")"
            head_sha="$(jq -r '.workflow_run.head_sha // ""' "$GITHUB_EVENT_PATH")"
            pr_number="$(jq -r '.workflow_run.pull_requests[0].number // ""' "$GITHUB_EVENT_PATH")"
            repository_id="$(jq -r '.repository.id // ""' "$GITHUB_EVENT_PATH")"
            run_repository_id="$(jq -r '.workflow_run.repository.id // ""' "$GITHUB_EVENT_PATH")"
            case "$head_sha" in
              ''|*[!0-9a-f]*) echo 'ready=false' >> "$GITHUB_OUTPUT"; exit 0 ;;
            esac
            if [ "$pr_count" != "1" ] || [ "$source_event" != "pull_request" ] || [ "$workflow_name" != "CI" ] || [ "$repository_id" != "$run_repository_id" ]; then
              echo 'ready=false' >> "$GITHUB_OUTPUT"
              exit 0
            fi
            echo "ready=true" >> "$GITHUB_OUTPUT"
            echo "pr_number=$pr_number" >> "$GITHUB_OUTPUT"
            echo "head_sha=$head_sha" >> "$GITHUB_OUTPUT"
            echo "ci_conclusion=$conclusion" >> "$GITHUB_OUTPUT"
            echo "ci_run_id=$(jq -r '.workflow_run.id' "$GITHUB_EVENT_PATH")" >> "$GITHUB_OUTPUT"
            exit 0
          fi

          pr_number="$(jq -r '.pull_request.number // ""' "$GITHUB_EVENT_PATH")"
          head_sha="$(jq -r '.pull_request.head.sha // ""' "$GITHUB_EVENT_PATH")"
          head_repo_id="$(jq -r '.pull_request.head.repo.id // ""' "$GITHUB_EVENT_PATH")"
          base_repo_id="$(jq -r '.repository.id // ""' "$GITHUB_EVENT_PATH")"
          case "$head_sha" in
            ''|*[!0-9a-f]*) echo 'ready=false' >> "$GITHUB_OUTPUT"; exit 0 ;;
          esac
          if [ -z "$pr_number" ] || [ "$head_repo_id" = "$base_repo_id" ]; then
            echo 'ready=false' >> "$GITHUB_OUTPUT"
            exit 0
          fi
          for attempt in $(seq 1 40); do
            runs="$(gh api "repos/$GITHUB_REPOSITORY/actions/runs?event=pull_request&head_sha=$head_sha&per_page=100")"
            completed="$(jq -c --arg sha "$head_sha" '[.workflow_runs[] | select(.name == "CI" and .event == "pull_request" and .head_sha == $sha and .status == "completed")] | sort_by(.run_number) | last // empty' <<< "$runs")"
            if [ -n "$completed" ]; then
              echo 'ready=true' >> "$GITHUB_OUTPUT"
              echo "pr_number=$pr_number" >> "$GITHUB_OUTPUT"
              echo "head_sha=$head_sha" >> "$GITHUB_OUTPUT"
              echo "ci_conclusion=$(jq -r '.conclusion // ""' <<< "$completed")" >> "$GITHUB_OUTPUT"
              echo "ci_run_id=$(jq -r '.id' <<< "$completed")" >> "$GITHUB_OUTPUT"
              exit 0
            fi
            sleep 15
          done
          echo 'ready=false' >> "$GITHUB_OUTPUT"

  conclusion:
    permissions:
      contents: read
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
          GH_AW_AGENT_OUTPUT: ""
        run: python scripts/triage_conclusion.py
---

/triage

This workflow explicitly invokes the installed upstream `triage` skill. Use its state-machine roles
and context-gathering procedure for the current target. The local read-only and safe-output policy
below overrides any upstream instruction that would require direct tracker mutations, checkout, shell,
code edits, closing, or waiting for maintainer interaction.

{{#runtime-import .github/workflows/shared/triage-policy.md}}

## Trusted CI context

The deterministic `ci-evidence` job has already confirmed one matching completed `CI` run. Use these
trusted values exactly in the safe output:

- PR number: `${{ needs.ci-evidence.outputs.pr_number }}`
- Head SHA: `${{ needs.ci-evidence.outputs.head_sha }}`
- CI conclusion: `${{ needs.ci-evidence.outputs.ci_conclusion }}`
- CI run ID: `${{ needs.ci-evidence.outputs.ci_run_id }}`

Use event key `pr:<number>:sha:<head_sha>`. Read the PR description, linked Issue, comments, diff
metadata, and the completed CI checks through read-only GitHub tools. The only local checkout is the
explicitly configured trusted base ref; never checkout, download, import, or execute PR-controlled
files or artifacts. For a fork PR, this workflow is running from the base repository context and the
same restriction is absolute.
