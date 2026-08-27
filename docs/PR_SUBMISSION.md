# PR 合规提交与合并流程

本文档是本项目的强制 PR 流程。目标分支只能通过受审查的工作分支合并，不能直接向 `master` 或 `main` 提交代码。

## 1. 硬性规则

1. 所有代码变更必须通过 Pull Request 合并。
2. PR 源分支必须使用允许的工作分支前缀：`feature/`、`fix/`、`hotfix/`、`refactor/`、`test/`、`docs/` 或 `chore/`。
3. `master` 和 `main` 只能作为目标分支，不能作为 PR 源分支。
4. PR 必须通过当前 CI 的 `Lint and Test` 检查。
5. PR 必须至少获得一名维护者批准，且不能有未解决的阻塞性意见。
6. 合并前源分支必须基于目标分支的最新提交，并重新通过 CI。
7. 禁止绕过 PR 直接 push、强制 push 或直接修改受保护分支。
8. 默认使用 Squash merge，合并后删除源分支。

当前仓库的 CI 尚未自动检查所有分支命名、审批和合并方式；这些规则必须通过 GitHub Branch protection 或 Ruleset 配置，并由审查者在配置完成前人工执行。

## 2. 分支流程

### 2.1 创建 issue

实现类变更先建立 issue，写清楚背景、目标、非目标、验收标准、约束和验证方式。一个 issue 对应一个独立 PR，避免把多个无关目标混在一起。

### 2.2 创建工作分支

从最新的 `master` 创建分支：

```powershell
git fetch origin
git switch master
git pull --ff-only origin master
git switch -c feature/<issue-number>-<short-slug>
```

分支命名示例：

```text
feature/123-vlm-video-timing
fix/124-sse-job-recovery
docs/pr-submission-process
```

issue 自动化使用以下固定格式：

```text
feature/issue-<issue-number>-<short-slug>
```

以下分支不能直接发起面向 `master/main` 的 PR：

- `master`
- `main`
- `develop`
- `release/*`
- `wip/*`
- 没有上述允许前缀的临时或个人分支

紧急生产修复使用 `hotfix/*`，仍然必须经过 PR 和 CI。

### 2.3 保持分支同步

PR 存续期间定期同步目标分支。需要更新时优先使用 rebase，并在强制 push 前确认该分支只有当前作者使用：

```powershell
git fetch origin
git rebase origin/master
git push --force-with-lease origin <work-branch>
```

不能使用裸 `--force`。

## 3. Commit 规范

Commit 首行描述为什么需要这个变更，而不是只罗列修改了哪些文件。正文说明上下文、约束和取舍；相关决策使用 Lore trailers：

```text
<why this change exists>

<context and approach>

Constraint: <external constraint>
Rejected: <alternative> | <reason>
Confidence: <low|medium|high>
Scope-risk: <narrow|moderate|broad>
Directive: <warning for future changes>
Tested: <verification performed>
Not-tested: <known gaps>
```

不要提交凭据、私有地址、密钥、模型敏感配置或无关生成文件。

## 4. 创建 PR 前

提交前必须确认：

```powershell
git diff --check
python -m ruff check app tests
python -m pytest
```

PR 描述至少包含：

```markdown
## Problem

## Solution

## Scope

## Verification

- [ ] `python -m ruff check app tests`
- [ ] `python -m pytest`
- [ ] 已完成必要的手工验证

## Risks
```

涉及 UI、视频、VLM、GPU 或外部服务时，必须补充实际手工验证结果；CI 不启动真实 GPU、Docker 模型服务或完整浏览器播放链路。

视频类 PR 至少验证：

- 原视频和 AI 视频的帧数、帧率、PTS、时长和音频同步。
- CFR 与 VFR 输入（如果改动涉及时基）。
- 推理慢、请求失败、结果乱序和页面刷新恢复。
- `http://localhost:5000/video-test` 的实际播放结果。

## 5. CI 门禁

当前 `.github/workflows/ci.yml` 在以下场景触发：

- push 到 `master` 或 `main`。
- 面向 `master` 或 `main` 的 Pull Request。
- 手动触发 workflow。

主门禁 `Lint and Test` 使用 Python 3.12，并依次执行：

1. 安装 `requirements.txt`。
2. 安装 pytest 和 Ruff。
3. 执行 `ruff check app tests`。
4. 执行 `pytest`，超时为 15 分钟。

PR 必须等待 `Lint and Test` 通过后才能合并。CI 取消同一分支上较旧的运行结果时，应以最新一次运行结果为准。

## 6. OpenCodeReview

OpenCodeReview 不是无条件门禁，只有同时满足以下条件才运行：

- 事件是 Pull Request。
- 仓库变量 `OCR_REVIEW_ENABLED` 等于 `true`。
- PR 没有 `trusted-ai-provider` 标签。
- `Lint and Test` 已通过。

运行后按以下规则处理：

- `Critical` 或 `High`：禁止合并，必须修复并重新验证。
- `Medium`：维护者必须明确决定修复、延期或接受风险。
- 只有 `Low` 或没有中高风险意见：进入正常合并流程。

跳过 OpenCodeReview 不代表跳过人工审查。

## 7. Issue 自动化

当前 issue 自动化使用 `ready-for-agent`，`ready-for-dev` 是旧标签，不再触发实现动作。完整 Issue 先经过 triage，再由受信任的实现工作流处理；`agent-running` 是执行锁，不是替代 PR 审查的标签。

Issue 必须包含以下非空章节：

- `Background`
- `Goal`
- `Non-goals`
- `Acceptance criteria`
- `Constraints`
- `Verification`

自动化只在验证成功后创建一个 draft PR，并添加 `ai-generated` 标签；PR 仍然必须从合规工作分支进入受保护的 `master`，由人工完成最终合并。PR 描述必须包含 `PH_AUTO_LABEL_TARGET: issue:<issue-number>` 关联标记。验证失败、缺少章节或涉及受保护文件时，自动化必须停止并转人工处理，不得创建或合并 PR。

## 8. 合并前检查

合并人必须逐项确认：

- [ ] 源分支符合允许的工作分支前缀。
- [ ] 目标分支是 `master` 或 `main`。
- [ ] PR 有清晰的 Problem、Solution、Scope 和 Verification。
- [ ] 至少一名维护者已批准。
- [ ] 所有阻塞性 review 意见已关闭或明确记录决定。
- [ ] `Lint and Test` 通过。
- [ ] 源分支已同步目标分支最新提交。
- [ ] 必要的 UI、视频或外部服务手工验证已完成。
- [ ] 没有凭据、私有配置或无关文件进入变更。
- [ ] 使用 Squash merge，合并后删除源分支。

任何一项不满足，都不能合并。

## 9. GitHub 保护配置

要让本规范真正具备强制力，`master` 和 `main` 应配置 Branch protection 或 Ruleset：

- 禁止直接 push。
- 要求 Pull Request 才能合并。
- 要求至少 1 个维护者 approval。
- 要求状态检查 `Lint and Test` 通过。
- 要求分支在合并前是最新状态。
- 禁止 force push 和删除受保护分支。
- 允许的合并方式仅保留 Squash merge。
- 对源分支增加前缀检查，允许 `feature/*`、`fix/*`、`hotfix/*`、`refactor/*`、`test/*`、`docs/*`、`chore/*`。

当前 CI 文件没有分支前缀检查 job；在该检查加入并设为 required 之前，维护者必须人工拒绝不合规源分支的 PR。
