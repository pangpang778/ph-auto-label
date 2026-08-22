---
safe-outputs:
  data:
    schema_version: integer
    target_type: string
    target_number: string
    event_key: string
    head_sha: string
    category: string
    state: string
    confidence: number
    reason: string
    missing_info: string
  jobs:
    apply-triage:
      description: >-
        Submit exactly one structured triage decision. The trusted job validates
        target identity, current SHA, confidence, legal transition, label allowlist,
        and idempotency before writing one comment and managed labels.
      if: >-
        (!cancelled()) &&
        needs.agent.result != 'skipped' &&
        needs.detection.result == 'success' &&
        contains(needs.agent.outputs.output_types, 'apply_triage')
      runs-on: ubuntu-slim
      permissions:
        contents: read
        issues: write
        pull-requests: write
      timeout-minutes: 5
      inputs:
        schema_version:
          description: "The integer schema version; must be 1."
          required: true
          type: choice
          options: ["1"]
        target_type:
          description: "The target type."
          required: true
          type: choice
          options: [issue, pull_request]
        target_number:
          description: "The target Issue or PR number."
          required: true
          type: string
        event_key:
          description: "The exact event key supplied by the trusted trigger context."
          required: true
          type: string
        head_sha:
          description: "The PR head SHA, or an empty string for an Issue."
          required: false
          type: string
          default: ""
        category:
          description: "Exactly one canonical category."
          required: true
          type: choice
          options: [bug, enhancement]
        state:
          description: "Exactly one canonical triage state."
          required: true
          type: choice
          options: [needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix]
        confidence:
          description: "A decimal confidence score from 0 to 1."
          required: true
          type: string
        reason:
          description: "A bounded plain-text explanation without secrets."
          required: true
          type: string
        missing_info:
          description: "A JSON array of bounded missing-information strings."
          required: false
          type: string
          default: "[]"
      steps:
        - name: Checkout trusted default-branch validator
          uses: actions/checkout@v7
          with:
            ref: ${{ github.event.repository.default_branch }}
            persist-credentials: false
            sparse-checkout: |
              scripts/triage_conclusion.py
        - name: Validate and apply triage decision
          env:
            GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          run: python scripts/triage_conclusion.py
---
