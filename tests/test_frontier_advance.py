from scripts.frontier_advance import run


class FakeClient:
    def __init__(self, *, labels=None, state="open"):
        self.item = {
            "state": state,
            "labels": [{"name": label} for label in (labels or [])],
        }
        self.comments = []
        self.operations = []

    def get_issue_or_pr(self, number):
        return self.item

    def list_comments(self, number):
        return self.comments

    def replace_labels(self, number, labels):
        self.operations.append(("labels", tuple(sorted(labels))))

    def add_comment(self, number, body):
        self.operations.append(("comment", body))
        self.comments.append({"body": body})

    def close_item(self, number):
        self.operations.append(("close", number))


def merged_event(*, merged=True, labels=None, body="PH_AUTO_LABEL_TARGET: issue:7"):
    return {
        "action": "closed",
        "pull_request": {
            "number": 19,
            "merged": merged,
            "merge_commit_sha": "b" * 40,
            "body": body,
            "labels": [{"name": label} for label in (labels or ["ai-generated"])],
        },
    }


def test_merged_agent_pr_closes_issue_and_releases_lock():
    client = FakeClient(labels=["bug", "ready-for-agent", "agent-running", "customer"])

    assert run(merged_event(), client) == "applied: issue closed and execution lock released"
    assert ("labels", ("bug", "customer")) in client.operations
    assert client.operations[-1] == ("close", 7)
    assert "Implementation merged: pr:19:merge:" in client.comments[0]["body"]


def test_merged_agent_pr_is_idempotent():
    client = FakeClient(labels=["agent-running"])
    event = merged_event()

    assert run(event, client).startswith("applied:")
    count = len(client.operations)
    assert run(event, client) == "skipped: merge event already processed"
    assert len(client.operations) == count


def test_unrelated_or_unmerged_pr_is_a_noop():
    client = FakeClient(labels=["agent-running"])
    assert run(merged_event(merged=False), client) == "skipped: pull request was not merged"
    assert run(merged_event(labels=["bug"]), client) == "skipped: pull request is not an implementation PR"
    assert run(merged_event(body="ordinary PR"), client) == "skipped: implementation marker is missing"
    assert client.operations == []
