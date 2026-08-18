# AI SOP: Configure the PR Quality Gate

## Required Outcome

Implement this sequence for every PR to `master` or `main`:

```text
Ruff + pytest
  -> OpenCodeReview only when both pass
  -> one severity-sorted PR report
  -> Critical/High fails CI and blocks merge
```

| Severity | CI result | Merge |
| --- | --- | --- |
| Critical | fail | block |
| High | fail | block |
| Medium | pass with manual-review report | allow |
| Low | pass with advisory report | allow |

## Credential Policy

Never commit, echo, print, copy into PR text, or document a real provider URL, token, model name, account name, or credential.

Use only these GitHub repository secret names in committed workflow YAML:

```text
OCR_LLM_URL
OCR_LLM_AUTH_TOKEN
OCR_LLM_MODEL
OCR_LLM_USE_ANTHROPIC
```

Use this GitHub repository variable:

```text
OCR_REVIEW_ENABLED=true
```

Set the secrets interactively. Do not put their values in scripts:

```powershell
gh secret set OCR_LLM_URL --repo OWNER/REPO
gh secret set OCR_LLM_AUTH_TOKEN --repo OWNER/REPO
gh secret set OCR_LLM_MODEL --repo OWNER/REPO
gh secret set OCR_LLM_USE_ANTHROPIC --repo OWNER/REPO
gh variable set OCR_REVIEW_ENABLED --body true --repo OWNER/REPO
```

Never expose a local proxy to the public internet solely for CI. The configured endpoint must be reachable from GitHub-hosted runners and authenticated.

## Bootstrap Steps

1. Start from the target branch, then create `feature/ocr-gate-bootstrap`.
2. Add `ruff.toml` and migrate the existing linter to `ruff check`.
3. Add `.github/workflows/ci.yml` following the existing implementation in this repository. Adapt Python version, package installation, Ruff paths, and test command only where the target project differs.
4. Keep these exact job names: `Lint and Test` and `OpenCodeReview`.
5. The lint job must have only `contents: read`. It executes untrusted PR code.
6. The review job must declare `needs: lint-and-test`, have `contents: read` plus `pull-requests: write`, and use full Git history (`fetch-depth: 0`).
7. Reference the LLM settings only through `${{ secrets.OCR_LLM_* }}`.
8. Pin `alibaba/open-code-review` to a verified immutable release commit SHA, never a mutable tag.
9. Do not add a second review bot unless it has a documented non-overlapping responsibility.

Required review-job shape:

```yaml
open-code-review:
  name: OpenCodeReview
  needs: lint-and-test
  if: github.event_name == 'pull_request' && vars.OCR_REVIEW_ENABLED == 'true'
  runs-on: ubuntu-latest
  timeout-minutes: 20
  permissions:
    contents: read
    pull-requests: write
  steps:
    - uses: actions/checkout@v4
      with:
        fetch-depth: 0
        persist-credentials: false
    - uses: alibaba/open-code-review@ACTION_COMMIT_SHA
      with:
        llm_url: ${{ secrets.OCR_LLM_URL }}
        llm_auth_token: ${{ secrets.OCR_LLM_AUTH_TOKEN }}
        llm_model: ${{ secrets.OCR_LLM_MODEL }}
        llm_use_anthropic: ${{ secrets.OCR_LLM_USE_ANTHROPIC }}
        github_token: ${{ secrets.GITHUB_TOKEN }}
```

Replace `ACTION_COMMIT_SHA` with the full SHA of the intended upstream release before committing.

## Report and Gate Logic

After OpenCodeReview, add an `actions/github-script@v7` step that reads `/tmp/ocr-result.json` created by the action.

The step must:

- count `critical`, `high`, `medium`, `low` in that order;
- create or update one comment identified by `<!-- ocr-decision-summary -->`;
- show an explicit `BLOCK`, `MANUAL REVIEW`, or `PASS` conclusion;
- show a severity table and up to eight highest-risk findings;
- publish the report before failing the job;
- call `core.setFailed(...)` only if Critical or High is nonzero.

Required gate decision:

```js
const blocked = counts.critical || counts.high;
const decision = blocked
  ? 'BLOCK: Critical or High findings require resolution.'
  : counts.medium
    ? 'MANUAL REVIEW: Medium findings need a maintainer decision.'
    : 'PASS: No Critical, High, or Medium findings.';

if (blocked) {
  core.setFailed(
    `OpenCodeReview found ${counts.critical} Critical and ${counts.high} High finding(s).`,
  );
}
```

The canonical complete summary-step implementation is `.github/workflows/ci.yml` in this repository. Reuse its structure rather than inventing another format.

## Verification and Rollout

Before opening the bootstrap PR, run Ruff, pytest, YAML parsing, and `git diff --check`.

Open the bootstrap PR. Resolve all Critical and High findings found in the workflow itself. Do not lower the gate policy simply to merge it.

After the bootstrap PR is green, merge it to `master`, then enable branch protection requiring both check names:

```text
Lint and Test
OpenCodeReview
```

Set strict status checks and enforce them for administrators. Verify with:

```powershell
gh api repos/OWNER/REPO/branches/master/protection --jq '{required: .required_status_checks.contexts, enforce_admins: .enforce_admins.enabled}'
```

## Final Validation

After the configuration is in `master`, create `feature/ocr-gate-validation` from current `master`. Add one small, intentional production-security regression that passes Ruff and tests. Mark the PR **DO NOT MERGE**.

Success evidence is:

1. `Lint and Test` succeeds.
2. OpenCodeReview posts the severity-sorted report.
3. OCR classifies the regression as Critical or High.
4. OpenCodeReview fails after posting the report.
5. The PR becomes `BLOCKED` by branch protection.

Close the validation PR after inspection. Never merge its unsafe change.

## Ongoing Rules

- Use `feature/<topic>` for features and `fix/<topic>` for fixes.
- OCR is a post-test semantic gate; it must not run when lint or tests fail.
- Keep `OCR_REVIEW_ENABLED=true` except for documented temporary operational pauses.
- Do not weaken the Critical/High failure logic to make a PR green.
- Keep review comments readable and sorted: Critical, High, Medium, Low.
