# Domain

ph-auto-label: a Flask web app that auto-labels images and videos. It ships
trained depth/video backends (SAM3, MoGe-2, VLM labeling, deep-distillation
teacher) and a UI to upload media, run annotation/inference, and manage trained
models.

## Category guidance

- **bug** — a broken behavior: an endpoint erroring, inference producing wrong
  output, a model failing to load, a training run crashing, a UI flow not
  working as documented.
- **enhancement** — a new feature, model/backend, endpoint, or an improvement
  that changes behavior without fixing a defect.

## State guidance

- **needs-triage** — maintainer must evaluate; not enough certainty to class or
  spec it yet.
- **needs-info** — the report is missing concrete reproduction steps, inputs,
  exact error output, or expected-vs-actual that are required to act. Ask the
  concrete questions.
- **ready-for-agent** — fully specified; an agent can make the change with
  objective acceptance. Write an Agent Brief. No speculative scope: only what
  the report actually asks.
- **ready-for-human** — needs human implementation, judgment, or merge review
  (design trade-offs, cross-cutting changes, or a human should own it).
- **wontfix** — will not be acted on; give a durable reason.

## Evidence rules

Triage is read-only and must not claim reproduction, tests, or verification
that it did not actually obtain. Evidence comes from the trusted context file:
repository metadata, body, comments, and the bounded PR diff. If the diff is
absent or truncated, say so and prefer `needs-info` or `ready-for-human` over
`ready-for-agent`/`wontfix` for a PR.