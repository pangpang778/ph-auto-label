# Triage Labels

Triage reconciles exactly one **category** label (`bug` or `enhancement`) and
exactly one **state** label (one of the five native states) on the target. All
other labels are preserved untouched.

## Managed labels

**Category:** `bug`, `enhancement`

**State**

| State label | Meaning |
|---|---|
| `needs-triage` | maintainer must evaluate |
| `needs-info` | waiting on reporter for info |
| `ready-for-agent` | fully specified, agent-ready |
| `ready-for-human` | needs human implementation/merge |
| `wontfix` | will not be acted on (closes the target) |

## Reconcile rules

- Replace the existing state label with the new state (remove the old, add the
  new) and the existing category label with the new category.
- Never add a second category or second state label; two managed category
  labels or two managed state labels at once is a conflict and is rejected
  with zero writes rather than guessing.
- `wontfix` also closes the Issue/PR with `state_reason: not_planned`.
- Reconcile each operation independently and idempotently so a retry repairs a
  partial prior run instead of duplicating labels or comments.