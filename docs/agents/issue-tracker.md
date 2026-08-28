# Issue Tracker

## Request surfaces

- **Issues: yes**
- **PRs as a request surface: yes**

A pull request is an issue with attached code: the same roles, states, and
machine apply, with the PR-specific deltas called out in the skill.

## External author-association allowlist

Triage covers external PRs whose author association is one of:

- `CONTRIBUTOR`
- `FIRST_TIME_CONTRIBUTOR`
- `NONE`

Banked `OWNER`, `MEMBER`, and `COLLABORATOR` PRs are not triage requests and
are ignored.

## Current head rule

A triage verdict is only written against the head SHA observed at triage time.
If the PR head changes before the write window, the prior verdict is discarded
as stale and no further writes are made for it; a fresh `synchronize` event
starts a new triage.

## Manual re-analysis

Comment `/triage` on an Issue (requires write authority) or on an eligible
external PR to request a fresh analysis.