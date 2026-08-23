import json

import pytest

from scripts.triage_conclusion import (
    AI_DISCLAIMER,
    TriageError,
    already_processed,
    apply_decision,
    comment_body,
    derive_target,
    fallback_decision,
    label_names,
    load_agent_decision,
    parse_decision,
    run,
    Target,
    validate_transition,
)


class FakeClient:
    def __init__(self, *, labels=None, head_sha="a" * 40):
        self.item = {"labels": [{"name": name} for name in (labels or [])]}
        self.pull_request = {"labels": list(self.item["labels"]), "head": {"sha": head_sha}}
        self.comments = []
        self.operations = []
        self.fail_dispatch = False

    def get_issue_or_pr(self, number):
        return self.item

    def get_pull_request(self, number):
        return self.pull_request

    def list_comments(self, number):
        return self.comments

    def replace_labels(self, number, labels):
        self.operations.append(("labels", tuple(sorted(labels))))

    def add_comment(self, number, body):
        self.operations.append(("comment", body))
        self.comments.append({"body": body})

    def close_item(self, number):
        self.operations.append(("close", number))

    def dispatch_workflow(self, workflow, ref, inputs):
        if self.fail_dispatch:
            raise TriageError("dispatch failed")
        self.operations.append(("dispatch", workflow, ref, inputs))

def valid_item(**overrides):
    item = {
        "schema_version": 1,
        "target_type": "issue",
        "target_number": "7",
        "event_key": "issue:7:opened:2026-08-22T00:00:00Z",
        "head_sha": "",
        "category": "bug",
        "state": "needs-info",
        "confidence": "0.91",
        "reason": "Please provide a minimal reproduction.",
        "missing_info": "[\"reproduction steps\"]",
    }
    item.update(overrides)
    return item


def test_parse_valid_decision():
    decision = parse_decision(valid_item())
    assert decision.target_number == 7
    assert decision.missing_info == ("reproduction steps",)


@pytest.mark.parametrize(
    "overrides",
    [
        {"category": "question"},
        {"state": "closed"},
        {"confidence": "0.75x"},
        {"event_key": "event with spaces"},
        {"missing_info": "{\"not\": \"an array\"}"},
    ],
)
def test_parse_rejects_invalid_schema(overrides):
    with pytest.raises(TriageError):
        parse_decision(valid_item(**overrides))


def test_pull_request_requires_sha():
    with pytest.raises(TriageError):
        parse_decision(valid_item(target_type="pull_request"))


def test_needs_info_requires_concrete_missing_items():
    with pytest.raises(TriageError):
        validate_transition(
            parse_decision(valid_item(missing_info="[]")),
            {"labels": []},
        )

    with pytest.raises(TriageError):
        validate_transition(
            parse_decision(valid_item(state="ready-for-human", missing_info='["still missing"]')),
            {"labels": []},
        )


def test_load_agent_decision_requires_exactly_one(tmp_path):
    path = tmp_path / "agent_output.json"
    path.write_text(json.dumps({"items": [{"type": "apply_triage", **valid_item()}]}), encoding="utf-8")
    assert load_agent_decision(str(path)).target_number == 7
    path.write_text(json.dumps({"items": []}), encoding="utf-8")
    assert load_agent_decision(str(path)) is None
    path.write_text(
        json.dumps(
            {
                "items": [
                    {"type": "apply_triage", **valid_item()},
                    {"type": "apply_triage", **valid_item(event_key="issue:7:opened:other")},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert load_agent_decision(str(path)) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 1.5),
        ("schema_version", True),
        ("target_number", 3.7),
        ("target_number", False),
    ],
)
def test_parse_rejects_non_integer_values(field, value):
    with pytest.raises(TriageError):
        parse_decision(valid_item(**{field: value}))


def test_derive_event_keys():
    issue_event = {
        "action": "opened",
        "issue": {"number": 7, "created_at": "2026-08-22T00:00:00Z"},
    }
    comment_event = {
        "action": "created",
        "issue": {"number": 7, "pull_request": {}},
        "comment": {"id": 99},
    }
    assert derive_target("issues", issue_event).event_key == "issue:7:opened:2026-08-22T00:00:00Z"
    assert derive_target("issue_comment", comment_event).event_key == "comment:99:created"


def test_fallback_preserves_one_existing_category():
    item = {"labels": [{"name": "bug"}, {"name": "customer"}]}
    target = Target("issue", 7, "comment:99:created", "")
    decision = fallback_decision(target, item, "failed")
    assert decision.category == "bug"
    assert decision.state == "needs-triage"


def test_fallback_does_not_guess_when_category_is_missing_or_conflicting():
    target = Target("issue", 7, "comment:99:created", "")
    assert fallback_decision(target, {"labels": []}, "failed").category == ""
    assert fallback_decision(target, {"labels": [{"name": "bug"}, {"name": "enhancement"}]}, "failed").category == ""


def test_conflicting_state_is_rejected():
    decision = parse_decision(valid_item(state="needs-info"))
    with pytest.raises(TriageError):
        validate_transition(decision, {"labels": [{"name": "needs-info"}, {"name": "wontfix"}]})


def test_conflicting_category_is_rejected():
    decision = parse_decision(valid_item(state="ready-for-human"))
    with pytest.raises(TriageError):
        validate_transition(decision, {"labels": [{"name": "bug"}, {"name": "enhancement"}]})


def test_needs_triage_must_be_re_evaluated():
    decision = parse_decision(valid_item(state="needs-triage"))
    with pytest.raises(TriageError):
        validate_transition(decision, {"labels": [{"name": "needs-triage"}]})


def test_needs_info_must_return_to_needs_triage_before_final_route():
    decision = parse_decision(valid_item(state="needs-triage", missing_info="[]"))
    validate_transition(decision, {"labels": [{"name": "needs-info"}]})

    direct_route = parse_decision(valid_item(state="ready-for-agent", missing_info="[]"))
    with pytest.raises(TriageError):
        validate_transition(direct_route, {"labels": [{"name": "needs-info"}]})


def test_comment_disclaimer_and_footer():
    decision = parse_decision(valid_item())
    body = comment_body(decision)
    assert body.startswith(AI_DISCLAIMER)
    assert "Triage event: issue:7:opened:2026-08-22T00:00:00Z" in body


def test_long_comment_keeps_footer():
    decision = parse_decision(valid_item(reason="x" * 4000, missing_info=json.dumps(["y" * 500] * 20)))
    body = comment_body(decision)
    assert len(body) <= 12000
    assert body.endswith("Triage event: issue:7:opened:2026-08-22T00:00:00Z")


def test_event_footer_is_idempotency_marker():
    assert already_processed([{"body": "Triage event: comment:99:created"}], "comment:99:created")
    assert not already_processed([{"body": "Triage event: comment:98:created"}], "comment:99:created")


def test_label_names_ignores_malformed_labels():
    assert label_names({"labels": [{"name": "bug"}, {"bad": True}, "wrong"]}) == {"bug"}


def test_paused_and_bot_events_are_noops():
    issue_event = {
        "action": "opened",
        "issue": {"number": 7, "created_at": "2026-08-22T00:00:00Z", "user": {"type": "User"}},
    }
    paused = FakeClient(labels=["triage-paused", "customer"])
    assert run("issues", issue_event, paused, None) == "skipped: triage-paused"
    assert paused.operations == []

    bot_event = {
        "action": "opened",
        "issue": {"number": 7, "created_at": "2026-08-22T00:00:00Z", "user": {"type": "Bot"}},
    }
    bot = FakeClient(labels=["customer"])
    assert run("issues", bot_event, bot, None) == "skipped: bot or system event"
    assert bot.operations == []

    system_event = {
        **issue_event,
        "issue": {**issue_event["issue"], "labels": [{"name": "agentic-workflows"}]},
    }
    system = FakeClient(labels=["customer"])
    assert run("issues", system_event, system, None) == "skipped: bot or system event"
    assert system.operations == []


def test_fallback_preserves_nonmanaged_labels_and_is_idempotent():
    event = {
        "action": "opened",
        "issue": {"number": 7, "created_at": "2026-08-22T00:00:00Z", "user": {"type": "User"}},
    }
    client = FakeClient(labels=["customer"])
    assert run("issues", event, client, None) == "applied: fallback"
    assert ("labels", ("customer", "needs-triage")) in client.operations
    body = next(value for kind, value in client.operations if kind == "comment")
    assert body.startswith(AI_DISCLAIMER)
    assert body.endswith("Triage event: issue:7:opened:2026-08-22T00:00:00Z")
    operation_count = len(client.operations)
    assert run("issues", event, client, None) == "skipped: event already processed"
    assert len(client.operations) == operation_count


def test_ready_for_agent_dispatches_implementation_once(tmp_path):
    event = {
        "action": "opened",
        "issue": {"number": 7, "created_at": "2026-08-22T00:00:00Z", "user": {"type": "User"}},
    }
    item = valid_item(
        state="ready-for-agent",
        missing_info="[]",
        reason="The request has a complete implementation brief.",
    )
    path = tmp_path / "agent_output.json"
    path.write_text(json.dumps({"items": [{"type": "apply_triage", **item}]}), encoding="utf-8")
    client = FakeClient(labels=["customer"])

    assert run("issues", event, client, str(path)) == "applied: agent-dispatched"
    assert ("dispatch", "implementation.lock.yml", "master", {
        "target_type": "issue",
        "target_number": "7",
        "event_key": "issue:7:opened:2026-08-22T00:00:00Z",
        "head_sha": "",
    }) in client.operations
    assert any(kind == "labels" and "agent-running" in labels for kind, labels in client.operations)
    assert client.operations[-1][0] == "comment"
    operation_count = len(client.operations)
    assert run("issues", event, client, str(path)) == "skipped: event already processed"
    assert len(client.operations) == operation_count


def test_ready_for_agent_does_not_dispatch_when_an_agent_is_running(tmp_path):
    event = {
        "action": "opened",
        "issue": {"number": 7, "created_at": "2026-08-22T00:00:00Z", "user": {"type": "User"}},
    }
    item = valid_item(state="ready-for-agent", missing_info="[]")
    path = tmp_path / "agent_output.json"
    path.write_text(json.dumps({"items": [{"type": "apply_triage", **item}]}), encoding="utf-8")
    client = FakeClient(labels=["customer", "agent-running"])

    assert run("issues", event, client, str(path)) == "applied: applied"
    assert not any(operation[0] == "dispatch" for operation in client.operations)


def test_ready_for_agent_dispatch_failure_rolls_back_labels_and_comment(tmp_path):
    event = {
        "action": "opened",
        "issue": {"number": 7, "created_at": "2026-08-22T00:00:00Z", "user": {"type": "User"}},
    }
    item = valid_item(state="ready-for-agent", missing_info="[]")
    path = tmp_path / "agent_output.json"
    path.write_text(json.dumps({"items": [{"type": "apply_triage", **item}]}), encoding="utf-8")
    client = FakeClient(labels=["customer"])
    client.fail_dispatch = True

    with pytest.raises(TriageError):
        run("issues", event, client, str(path))
    assert client.operations[-1] == ("labels", ("customer",))
    assert not any(operation[0] == "comment" for operation in client.operations)


def test_wontfix_closes_open_issue(tmp_path):
    event = {
        "action": "opened",
        "issue": {"number": 7, "created_at": "2026-08-22T00:00:00Z", "user": {"type": "User"}},
    }
    item = valid_item(state="wontfix", missing_info="[]", reason="This request is outside the supported scope.")
    path = tmp_path / "agent_output.json"
    path.write_text(json.dumps({"items": [{"type": "apply_triage", **item}]}), encoding="utf-8")
    client = FakeClient(labels=["customer"])

    assert run("issues", event, client, str(path)) == "applied: closed-wontfix"
    assert ("close", 7) in client.operations
    assert client.operations[-1] == ("close", 7)


def test_successful_reevaluation_preserves_nonmanaged_labels(tmp_path):
    item = valid_item(
        target_type="pull_request",
        target_number="7",
        event_key="comment:99:created",
        head_sha="a" * 40,
        category="bug",
        state="needs-triage",
        missing_info="[]",
    )
    path = tmp_path / "agent_output.json"
    path.write_text(json.dumps({"items": [{"type": "apply_triage", **item}]}), encoding="utf-8")
    event = {
        "action": "created",
        "issue": {"number": 7, "pull_request": {}},
        "comment": {"id": 99, "user": {"type": "User"}},
    }
    client = FakeClient(labels=["enhancement", "needs-info", "customer"], head_sha="a" * 40)
    assert run("issue_comment", event, client, str(path)) == "applied: applied"
    assert ("labels", ("bug", "customer", "needs-triage")) in client.operations
    assert client.operations[-1][0] == "comment"


def test_successful_issue_comment_is_idempotent(tmp_path):
    event = {
        "action": "created",
        "issue": {"number": 7, "pull_request": {}},
        "comment": {"id": 99, "user": {"type": "User"}},
    }
    item = valid_item(
        target_type="pull_request",
        target_number="7",
        event_key="comment:99:created",
        head_sha="a" * 40,
        state="ready-for-human",
        missing_info="[]",
    )
    path = tmp_path / "agent_output.json"
    path.write_text(json.dumps({"items": [{"type": "apply_triage", **item}]}), encoding="utf-8")
    client = FakeClient(labels=["enhancement", "customer"], head_sha="a" * 40)

    assert run("issue_comment", event, client, str(path)) == "applied: applied"
    first_operation_count = len(client.operations)
    assert run("issue_comment", event, client, str(path)) == "skipped: event already processed"
    assert len(client.operations) == first_operation_count
    assert client.operations[-1][0] == "comment"


def test_stale_pull_request_head_has_no_writes(tmp_path):
    event = {
        "action": "completed",
        "repository": {"id": 1},
        "workflow_run": {
            "name": "CI",
            "event": "pull_request",
            "repository": {"id": 1},
            "head_sha": "a" * 40,
            "pull_requests": [{"number": 7}],
        },
    }
    client = FakeClient(head_sha="b" * 40)
    assert run("workflow_run", event, client, None) == "skipped: stale pull request head"
    assert client.operations == []
