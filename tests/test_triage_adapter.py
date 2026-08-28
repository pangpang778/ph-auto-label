"""Tests for the native triage adapter.

Covers schema validation, both category and all five state enums, required
sections / disclaimer, label reconciliation preserving unrelated labels,
label-conflict rejection, stale-pull-request-head rejection, external PR
diff-incomplete state gating, marker-bound idempotency, wontfix closure, and
the no-forbidden-mutation guarantee.
"""

from __future__ import annotations

import json

import pytest
from pytest import fixture

from scripts.triage_context import build_context
from scripts.triage_adapter import (
    AI_DISCLAIMER,
    GitHubClient,
    MARKER_OPEN,
    MANAGED_CATEGORIES,
    MANAGED_LABELS,
    MANAGED_STATES,
    REASON_CONFLICT,
    REASON_CONTEXT,
    REASON_MALFORMED,
    REASON_STALE,
    decide_preflight,
    derive_event_key,
    Mutation,
    Result,
    Target,
    TriageError,
    apply_mutations,
    find_trusted_marker,
    load_result,
    marker_for,
    plan_mutations,
    read_marker_result,
    validate_result,
)

H = "a" * 40
G = "b" * 40
BOT = "98765"


@fixture
def target() -> Target:
    return Target("issue", 42, "issue:42:opened:2026-08-28T00:00:00Z", "")


@fixture
def ctx(target):
    """A convenience builder returning the plan_mutations kwargs with sane defaults."""

    def build(result: Result, **over):
        base = dict(
            target=target,
            current_head=None,
            labels=set(),
            comments=[],
            writer_bot_id=BOT,
            open_state=True,
            diff_complete=True,
        )
        base.update(over)
        return plan_mutations(result, **base)

    return build


def result(category="bug", state="needs-info", reason="rt", comment=None):
    if comment is None:
        comment = f"{AI_DISCLAIMER}\n\nWhen did it break? Provide steps."
        if state in {"ready-for-agent", "ready-for-human"}:
            comment = f"{AI_DISCLAIMER}\n\n## Agent Brief\n\nImplement X."
        if state == "wontfix":
            comment = f"{AI_DISCLAIMER}\n\nAlready implemented in app.py."
        if state == "needs-triage":
            comment = f"{AI_DISCLAIMER}\n\nNeeds maintainer evaluation."
    return Result(category, state, reason, comment)


def dump(result: Result, path, **over):
    data = {
        "category": result.category,
        "state": result.state,
        "reason": result.reason,
        "comment": result.comment,
    }
    data.update({k: v for k, v in over.items() if v is not None})
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


# --- validate_result ------------------------------------------------------


class TestValidateResult:
    def test_accepts_every_category_and_state(self):
        for cat in MANAGED_CATEGORIES:
            for state in MANAGED_STATES:
                r = result(category=cat, state=state)
                assert validate_result(r.__dict__).state == state

    def test_rejects_non_object(self):
        with pytest.raises(TriageError) as e:
            validate_result(["bug"])
        assert e.value.reason == REASON_MALFORMED

    def test_rejects_unknown_fields(self):
        with pytest.raises(TriageError) as e:
            validate_result(result().__dict__ | {"confidence": 0.9})
        assert e.value.reason == REASON_MALFORMED

    @pytest.mark.parametrize("cat", ["todo", ""])
    def test_rejects_bad_category(self, cat):
        with pytest.raises(TriageError) as e:
            validate_result(result(category=cat).__dict__)
        assert e.value.reason == REASON_MALFORMED

    def test_rejects_bad_state(self):
        with pytest.raises(TriageError) as e:
            validate_result(result(state="won't").__dict__)
        assert e.value.reason == REASON_MALFORMED

    def test_requires_disclaimer_prefix(self):
        bad = result().__dict__ | {"comment": "no disclaimer"}
        with pytest.raises(TriageError) as e:
            validate_result(bad)
        assert e.value.reason == REASON_MALFORMED

    def test_needs_info_requires_question(self):
        bad = result(state="needs-info", comment=f"{AI_DISCLAIMER}\n\nHere is context.")
        with pytest.raises(TriageError) as e:
            validate_result(bad.__dict__)
        assert e.value.reason == REASON_MALFORMED

    def test_ready_state_requires_agent_brief_section(self):
        bad = result(state="ready-for-agent", comment=f"{AI_DISCLAIMER}\n\nDo the thing.")
        with pytest.raises(TriageError) as e:
            validate_result(bad.__dict__)
        assert e.value.reason == REASON_MALFORMED

    def test_rejects_oversized_reason(self):
        with pytest.raises(TriageError) as e:
            validate_result(result(reason="x" * 4001).__dict__)
        assert e.value.reason == REASON_MALFORMED

    def test_rejects_missing_reason(self):
        with pytest.raises(TriageError) as e:
            validate_result({"category": "bug", "state": "wontfix", "comment": "c"})
        assert e.value.reason == REASON_MALFORMED


# --- plan_mutations -------------------------------------------------------


class TestPlanMutations:
    def test_adds_category_and_state_labels(self, ctx):
        m = ctx(result())
        assert m[0] == Mutation("add_label", name="bug")
        assert m[1] == Mutation("add_label", name="needs-info")
        assert any(x.op == "add_comment" for x in m)

    def test_reconciles_sibling_labels_but_preserves_unrelated(self, ctx):
        m = ctx(result(state="ready-for-agent"), labels={"bug", "needs-info", "good-first-issue"})
        ops = [(x.op, x.name) for x in m]
        assert ("remove_label", "needs-info") in ops
        assert ("add_label", "ready-for-agent") in ops
        assert not any(x[1] == "good-first-issue" for x in ops if x[0].startswith("label"))
        assert ("add_label", "bug") not in ops  # already present, untouched

    def test_conflicting_category_rejected(self, ctx):
        with pytest.raises(TriageError) as e:
            ctx(result(), labels={"bug", "enhancement"})
        assert e.value.reason == REASON_CONFLICT

    def test_conflicting_state_rejected(self, ctx):
        with pytest.raises(TriageError) as e:
            ctx(result(), labels={"needs-info", "wontfix"})
        assert e.value.reason == REASON_CONFLICT

    def test_stale_pr_head_rejected(self):
        pr = Target("pr", 7, "pr:7:opened:" + H, H)
        with pytest.raises(TriageError) as e:
            plan_mutations(
                result(),
                target=pr,
                current_head=G,
                labels=set(),
                comments=[],
                writer_bot_id=BOT,
                open_state=True,
                diff_complete=True,
            )
        assert e.value.reason == REASON_STALE

    def test_pr_missing_head_is_context_failure(self):
        pr = Target("pr", 7, "pr:7:opened:" + H, H)
        with pytest.raises(TriageError) as e:
            plan_mutations(
                result(),
                target=pr,
                current_head=None,
                labels=set(),
                comments=[],
                writer_bot_id=BOT,
                open_state=True,
                diff_complete=True,
            )
        assert e.value.reason == REASON_CONTEXT

    @pytest.mark.parametrize("state", ["needs-triage", "ready-for-agent", "wontfix"])
    def test_incomplete_external_pr_diff_rejects_non_partial_states(self, state):
        pr = Target("pr", 7, "pr:7:opened:" + H, H)
        with pytest.raises(TriageError) as e:
            plan_mutations(
                result(state=state),
                target=pr,
                current_head=H,
                labels=set(),
                comments=[],
                writer_bot_id=BOT,
                open_state=True,
                diff_complete=False,
            )
        assert e.value.reason == REASON_CONTEXT

    @pytest.mark.parametrize("state", ["needs-info", "ready-for-human"])
    def test_incomplete_external_pr_diff_accepts_partial_states(self, state):
        pr = Target("pr", 7, "pr:7:opened:" + H, H)
        m = plan_mutations(
            result(state=state),
            target=pr,
            current_head=H,
            labels=set(),
            comments=[],
            writer_bot_id=BOT,
            open_state=True,
            diff_complete=False,
        )
        assert any(x.op == "add_label" for x in m)

    def test_wontfix_closes_when_open(self, ctx):
        m = ctx(result(state="wontfix"))
        assert any(x.op == "close" for x in m)

    def test_wontfix_does_not_close_when_already_closed(self, ctx):
        m = ctx(result(state="wontfix"), open_state=False)
        assert not any(x.op == "close" for x in m)

    def test_non_wontfix_never_closes(self, ctx):
        for state in MANAGED_STATES - {"wontfix"}:
            assert not any(x.op == "close" for x in ctx(result(state=state)))

    def test_marker_present_from_bot_skips_duplicate_comment(self, target):
        tk = result()
        marker = marker_for(tk, target)
        user_comment = {"user": {"id": 1}, "body": "Looks good to me."}
        bot_comment = {"user": {"id": BOT}, "body": f"text\n\n{marker}"}
        m = plan_mutations(
            tk,
            target=target,
            current_head=None,
            labels={"bug", "needs-info"},
            comments=[user_comment, bot_comment],
            writer_bot_id=BOT,
            open_state=True,
            diff_complete=True,
        )
        assert not any(x.op == "add_comment" for x in m)

    def test_similar_text_from_user_is_not_a_duplicate_marker(self, target):
        forged = marker_for(result(), target)
        user_comment = {"user": {"id": 1}, "body": forged}  # attacker is not the bot
        m = plan_mutations(
            result(),
            target=target,
            current_head=None,
            labels={"bug", "needs-info"},
            comments=[user_comment],
            writer_bot_id=BOT,
            open_state=True,
            diff_complete=True,
        )
        assert any(x.op == "add_comment" for x in m)


class TestMarker:
    def test_marker_embeds_event_key_and_opens_with_tag(self, target):
        m = marker_for(result(), target)
        assert m.startswith(MARKER_OPEN)
        assert f"event_key: {target.event_key}" in m

    def test_find_trusted_marker_requires_bot_author(self, target):
        marker = marker_for(result(), target)
        assert find_trusted_marker([{"user": {"id": BOT}, "body": marker}], BOT, target.event_key)
        assert not find_trusted_marker([{"user": {"id": "1"}, "body": marker}], BOT, target.event_key)
        assert not find_trusted_marker([{"user": {"id": BOT}, "body": "no marker"}], BOT, target.event_key)

    def test_read_marker_result_rebuilds_category_and_state(self, target):
        r = result(category="enhancement", state="wontfix")
        marker = marker_for(r, target)
        rebuilt = read_marker_result([{"user": {"id": BOT}, "body": marker}], BOT, target.event_key)
        assert rebuilt == Result("enhancement", "wontfix", reason="", comment="")

    def test_read_marker_result_rejects_foreign_author_or_key(self, target):
        marker = marker_for(result(), target)
        assert read_marker_result([{"user": {"id": "1"}, "body": marker}], BOT, target.event_key) is None
        assert read_marker_result([], BOT, target.event_key) is None

    def test_read_marker_result_rejects_bad_embedded_values(self, target):
        marker = marker_for(result(), target).replace("state: needs-info", "state: nope")
        with pytest.raises(TriageError) as e:
            read_marker_result([{"user": {"id": BOT}, "body": marker}], BOT, target.event_key)
        assert e.value.reason == REASON_MALFORMED


# --- apply_mutations & load_result ---------------------------------------


class FakeClient:
    def __init__(self):
        self.ops = []

    def add_label(self, number, name):
        self.ops.append(f"add_label:{number}:{name}")

    def remove_label(self, number, name):
        self.ops.append(f"remove_label:{number}:{name}")

    def add_comment(self, number, body):
        self.ops.append(f"add_comment:{number}")

    def close_item(self, number):
        self.ops.append(f"close:{number}")


def test_apply_mutations_executes_in_order():
    client = FakeClient()
    apply_mutations(client, 42, [Mutation("add_label", name="bug"), Mutation("add_comment")])
    assert client.ops == ["add_label:42:bug", "add_comment:42"]


def test_load_result_missing_file_reports_malformed(tmp_path):
    with pytest.raises(TriageError) as e:
        load_result(str(tmp_path / "nope.json"))
    assert e.value.reason == REASON_MALFORMED


def test_load_result_roundtrip(tmp_path):
    p = tmp_path / "r.json"
    dump(result(), p)
    assert load_result(str(p)) == result()


class TestPreflight:
    def issue_event(
        self, action="opened", created="2026-08-28T00:00:00Z", updated="2026-08-28T01:00:00Z"
    ):
        return {
            "action": action,
            "sender": {"login": "contrib", "type": "User"},
            "issue": {"number": 42, "created_at": created, "updated_at": updated, "user": {"type": "User"}},
            "repository": {"id": 100},
        }

    class Client:
        def __init__(self, comments=None, labels=None, permission="admin"):
            self.comments = comments or []
            self.labels = labels or list(MANAGED_LABELS)
            self.permission = permission

        def collaborator_permission(self, login):
            return self.permission

        def list_labels(self):
            return [{"name": name} for name in self.labels]

        def list_comments(self, number):
            return self.comments

    def test_issue_opened_runs_analysis(self):
        d = decide_preflight("issues", self.issue_event(), client=self.Client(), writer_bot_id=BOT)
        assert (d["run"], d["analyze"], d["reconcile"]) == (True, True, False)
        assert d["target_type"] == "issue" and d["target_number"] == 42
        assert d["event_key"].startswith("issue:42:opened:")

    def test_bot_actor_skipped(self):
        ev = self.issue_event()
        ev["sender"]["type"] = "Bot"
        d = decide_preflight("issues", ev, client=self.Client(), writer_bot_id=BOT)
        assert not d["run"] and d["reason"] == "bot"

    def test_ordinary_comment_skipped(self):
        ev = self.issue_event(action="created")
        ev.update({"comment": {"id": 7, "body": "thanks!", "user": {"type": "User"}}})
        d = decide_preflight("issue_comment", ev, client=self.Client(), writer_bot_id=BOT)
        assert not d["run"] and d["reason"] == "ordinary_issue_comment"

    def test_triage_command_without_write_skipped(self):
        ev = self.issue_event(action="created")
        ev.update({"comment": {"id": 7, "body": "/triage", "user": {"type": "User"}}})
        d = decide_preflight("issue_comment", ev, client=self.Client(permission="read"), writer_bot_id=BOT)
        assert not d["run"] and d["reason"] == "unauthorized_triage_command"

    def test_triage_command_with_write_runs(self):
        ev = self.issue_event(action="created")
        ev.update({"comment": {"id": 7, "body": "/triage", "user": {"type": "User"}}})
        d = decide_preflight("issue_comment", ev, client=self.Client(permission="write"), writer_bot_id=BOT)
        assert d["run"] and d["analyze"] and d["event_key"] == "comment:7:created"

    def test_owner_association_authorizes_without_permission_check(self):
        # Owners are not "collaborators" (GitHub returns 404 on the permission
        # endpoint); author_association must grant authority on its own.
        ev = self.issue_event(action="created")
        ev.update({"comment": {"id": 7, "body": "/triage", "user": {"type": "User"}, "author_association": "OWNER"}})
        d = decide_preflight("issue_comment", ev, client=self.Client(permission="read"), writer_bot_id=BOT)
        assert d["run"] and d["analyze"] and d["event_key"] == "comment:7:created"

    def test_collaborator_permission_404_means_no_write(self, monkeypatch):
        import urllib.error

        client = GitHubClient(token="x", api_url="https://api.github.com", repository="o/r")
        calls = []

        def boom(url, **_kw):
            calls.append(url)
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        monkeypatch.setattr("urllib.request.urlopen", boom)
        assert client.collaborator_permission("pangpang778") == ""
        assert calls, "collaborator permission endpoint should be consulted"

    def test_internal_pr_skipped(self):
        ev = {
            "action": "opened",
            "sender": {"login": "owner", "type": "User"},
            "pull_request": {
                "number": 9,
                "author_association": "OWNER",
                "head": {"sha": H, "repo": {"id": 100}},
            },
            "repository": {"id": 100},
        }
        d = decide_preflight("pull_request_target", ev, client=self.Client(), writer_bot_id=BOT)
        assert not d["run"] and d["reason"] == "ineligible_pr"

    def test_external_contributor_pr_runs(self):
        ev = {
            "action": "opened",
            "sender": {"login": "outsider", "type": "User"},
            "pull_request": {
                "number": 9,
                "author_association": "FIRST_TIME_CONTRIBUTOR",
                "head": {"sha": H, "repo": {"id": 200}},
            },
            "repository": {"id": 100},
        }
        d = decide_preflight("pull_request_target", ev, client=self.Client(), writer_bot_id=BOT)
        assert d["run"] and d["analyze"] and d["target_type"] == "pr"
        assert d["event_key"] == f"pr:9:opened:{H}"

    def test_missing_managed_label_skips(self):
        labels = list(MANAGED_LABELS - {"bug"})
        d = decide_preflight("issues", self.issue_event(), client=self.Client(labels=labels), writer_bot_id=BOT)
        assert not d["run"] and d["reason"] == "missing_label"

    def test_existing_marker_reconciles_without_analysis(self, target):
        marker = marker_for(result(), target)
        client = self.Client(comments=[{"user": {"id": BOT}, "body": marker}])
        d = decide_preflight("issues", self.issue_event(), client=client, writer_bot_id=BOT)
        assert (d["run"], d["analyze"], d["reconcile"]) == (True, False, True)
        assert d["reason"] == "marker_found"

    def test_outside_trigger_action_is_passthrough(self):
        ev = self.issue_event(action="edited")
        d = decide_preflight("issues", ev, client=self.Client(), writer_bot_id=BOT)
        assert not d["run"] and d["reason"] == "untriggered_event"


class TestContext:
    def issue(self, body="desc", comments=()):
        return {
            "title": "t",
            "body": body,
            "user": {"login": "someone"},
            "labels": [{"name": "bug"}],
        }

    class Client:
        def __init__(self, comments=None, diff="", issue=None):
            self.item = issue or {}
            self.comments = list(comments or ())
            self.diff = diff

        def get_issue_or_pr(self, number):
            return self.item

        def list_comments(self, number):
            return self.comments

        def pull_diff(self, number):
            return self.diff

    def test_context_writes_files_for_issue(self, tmp_path):
        c = self.Client(issue=self.issue(), comments=[{"user": {"login": "x"}, "body": "hi"}])
        ctx = build_context(c, "o/r", "issue", 42, "", str(tmp_path), "issue:42:opened:X")
        data = json.loads((tmp_path / "triage-context.json").read_text(encoding="utf-8"))
        assert data["target_type"] == "issue" and data["diff_available"] is False
        assert data["diff_complete"] is True and data["comments"][0]["author"] == "x"
        assert ctx.diff_path == str(tmp_path / "triage-diff.patch")

    def test_pr_diff_size_cap_marks_incomplete(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRIAGE_DIFF_MAX_BYTES", "20")
        c = self.Client(issue=self.issue(body="# PR branch"), diff="x" * 100)
        build_context(c, "o/r", "pr", 7, H, str(tmp_path), f"pr:7:opened:{H}")
        data = json.loads((tmp_path / "triage-context.json").read_text(encoding="utf-8"))
        assert data["diff_available"] is True
        assert data["diff_complete"] is False and data["diff_truncated"] is True
        patch = (tmp_path / "triage-diff.patch").read_text(encoding="utf-8")
        assert patch.endswith("[...diff truncated...]\n")

    def test_pr_full_diff_is_complete(self, tmp_path):
        c = self.Client(issue=self.issue(body="# PR branch"), diff="diff --git a/x b/x\n+1")
        build_context(c, "o/r", "pr", 7, H, str(tmp_path), f"pr:7:opened:{H}")
        data = json.loads((tmp_path / "triage-context.json").read_text(encoding="utf-8"))
        assert data["diff_complete"] is True


class TestRun:
    def test_duplicate_replay_returns_skip(self, target, tmp_path):
        from scripts.triage_adapter import run

        class Client:
            def get_pull_request(self, n):  # not used for issue
                raise AssertionError

            def get_issue_or_pr(self, n):
                return {"state": "open", "labels": [{"name": "bug"}, {"name": "needs-info"}]}

            def list_comments(self, n):
                marker = marker_for(result(), target)
                return [{"user": {"id": BOT}, "body": f"x\n\n{marker}"}]

        p = tmp_path / "r.json"
        dump(result(), p)
        outcome = run(
            Client(),
            result_path=str(p),
            target_type="issue",
            target_number=42,
            event_key=target.event_key,
            head_sha="",
            diff_complete=True,
            writer_bot_id=BOT,
        )
        assert outcome == "skipped: duplicate_event"

    def test_reconcile_only_repairs_missing_label_from_marker(self, target):
        from scripts.triage_adapter import Mutation, apply_mutations, run

        class Client:
            def __init__(self):
                self.item = {"state": "open", "labels": [{"name": "bug"}]}
                self._comments = None

            def get_pull_request(self, n):
                raise AssertionError

            def get_issue_or_pr(self, n):
                return self.item

            def list_comments(self, n):
                # marker present (event fully applied) but the needs-info label
                # was later lost: a reconcile should restore just that label.
                self._comments = [{"user": {"id": BOT}, "body": marker_for(result(), target)}]
                return self._comments

            def add_label(self, n, name):
                self.item["labels"].append({"name": name})

        from scripts.triage_adapter import plan_mutations

        client = Client()
        # result_path is None -> rebuild from marker
        outcome = run(
            client,
            result_path=None,
            target_type="issue",
            target_number=42,
            event_key=target.event_key,
            head_sha="",
            diff_complete=True,
            writer_bot_id=BOT,
        )
        assert outcome == "applied"
        assert any(lbl["name"] == "needs-info" for lbl in client.item["labels"])
