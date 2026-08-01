"""Comprehensive tests for the Model Evaluation & Comparison feature.

Covers:
  A. evaluation_repo (upsert/list/get/find_running/update_evaluations)
  B. evaluation_service (start_evaluation, build_comparison, export_evaluation)
  C. evaluation_job_runner.run_evaluation_job with a FAKE ultralytics.YOLO (no GPU)
  D. evaluation blueprint (6 routes) end-to-end via the test client

The ``isolated_app`` fixture redirects every PATHS entry to a per-test tmp_path
and seeds empty JSON stores, so all persistence lands in tmp. The fake
``ultralytics.YOLO`` mirrors the pattern in ``tests/test_training_job_runner.py``
so the runner exercises the real metric-extraction path without a GPU or real
model file.
"""
import csv
import io
import json
import os
import sys
import types
from pathlib import Path

import pytest

import app as training_app  # noqa: E402
from app.repositories import evaluation_repo  # noqa: E402
from app.services import evaluation_service  # noqa: E402
from app.services.evaluation_service import EvaluationBusyError  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _eval_record(
    rid="eval_a",
    model_id="model_1",
    status="completed",
    started_at="2026-01-01T00:00:00Z",
    val=None,
):
    """Build a minimal but realistic evaluation record for tests."""
    return {
        "id": rid,
        "job_id": f"job_{rid}",
        "model_id": model_id,
        "model_name": f"model-{model_id}",
        "status": status,
        "progress": 100 if status == "completed" else 0,
        "started_at": started_at,
        "completed_at": started_at if status == "completed" else "",
        "val": val or {},
        "test": {},
        "run_meta": {"imgsz": 640, "base_model": "/tmp/x.pt"},
        "error": "",
    }


def _seed_registry(model_id="model_1", model_name="model-1", path="/tmp/model_1.pt"):
    """Write a single model record into model_registry.json (registry store)."""
    records = [{
        "id": model_id,
        "name": model_name,
        "path": path,
        "version": "v1.0",
        "status": "production",
    }]
    Path(training_app.PATHS["model_registry"]).write_text(
        json.dumps(records), encoding="utf-8"
    )


def _seed_running_train_job():
    """Seed train_jobs.json with one status='running' job (eval mutex trigger)."""
    Path(training_app.PATHS["train_jobs"]).write_text(
        json.dumps([{
            "id": "train_running_1",
            "status": "running",
            "mode": "incremental",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }]),
        encoding="utf-8",
    )


def _seed_running_eval(record_id="eval_running_existing"):
    """Seed evaluations.json with one status='running' record."""
    evaluation_repo.upsert_evaluation(_eval_record(
        rid=record_id, status="running", started_at="2026-01-01T00:00:00Z"
    ))


# ---------------------------------------------------------------------------
# A. REPO UNIT TESTS
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_upsert_inserts_then_replaces_same_id(isolated_app):
    rec = _eval_record(rid="eval_x", status="completed", val={"map50": 0.5})
    evaluation_repo.upsert_evaluation(rec)

    fetched = evaluation_repo.get_evaluation("eval_x")
    assert fetched is not None
    assert fetched["val"]["map50"] == 0.5

    # Upsert again with same id -> replaces (not appends).
    rec2 = dict(rec)
    rec2["val"] = {"map50": 0.9}
    rec2["status"] = "completed"
    evaluation_repo.upsert_evaluation(rec2)

    all_recs = evaluation_repo.read_evaluations()
    assert len([r for r in all_recs if r["id"] == "eval_x"]) == 1
    fetched2 = evaluation_repo.get_evaluation("eval_x")
    assert fetched2["val"]["map50"] == 0.9


@pytest.mark.integration
def test_list_evaluations_newest_first_and_model_filter(isolated_app):
    old = _eval_record(rid="eval_old", model_id="m1",
                       started_at="2026-01-01T00:00:00Z")
    new = _eval_record(rid="eval_new", model_id="m2",
                       started_at="2026-02-01T00:00:00Z")
    mid = _eval_record(rid="eval_mid", model_id="m1",
                       started_at="2026-01-15T00:00:00Z")
    for r in (old, new, mid):
        evaluation_repo.upsert_evaluation(r)

    listed = evaluation_repo.list_evaluations()
    ids = [r["id"] for r in listed]
    # Newest first by started_at.
    assert ids == ["eval_new", "eval_mid", "eval_old"]

    # Filter by model_id.
    listed_m1 = evaluation_repo.list_evaluations(model_id="m1")
    assert [r["id"] for r in listed_m1] == ["eval_mid", "eval_old"]


@pytest.mark.integration
def test_get_evaluation_hit_and_miss(isolated_app):
    evaluation_repo.upsert_evaluation(_eval_record(rid="eval_hit"))

    assert evaluation_repo.get_evaluation("eval_hit") is not None
    assert evaluation_repo.get_evaluation("eval_hit")["id"] == "eval_hit"
    assert evaluation_repo.get_evaluation("does_not_exist") is None


@pytest.mark.integration
def test_find_running_evaluation_returns_running_or_none(isolated_app):
    # No running record -> None.
    assert evaluation_repo.find_running_evaluation() is None

    evaluation_repo.upsert_evaluation(_eval_record(
        rid="eval_done", status="completed"))
    evaluation_repo.upsert_evaluation(_eval_record(
        rid="eval_running", status="running"))

    running = evaluation_repo.find_running_evaluation()
    assert running is not None
    assert running["status"] == "running"
    assert running["id"] == "eval_running"


@pytest.mark.integration
def test_update_evaluations_skips_write_when_new_data_is_none(isolated_app):
    """Mutator returning (None, result) must NOT persist any change."""
    evaluation_repo.upsert_evaluation(_eval_record(rid="eval_keep"))

    # Mutator that returns None as new_data -> no write, returns the result.
    result = evaluation_repo.update_evaluations(
        lambda records: (None, "saw_" + str(len(records)))
    )
    assert result == "saw_1"

    # Store unchanged.
    all_recs = evaluation_repo.read_evaluations()
    assert len(all_recs) == 1
    assert all_recs[0]["id"] == "eval_keep"


# ---------------------------------------------------------------------------
# B. SERVICE UNIT TESTS
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_start_evaluation_creates_queued_record_for_registered_model(isolated_app, monkeypatch):
    _seed_registry(model_id="model_1", path="/tmp/model_1.pt")
    # Stub out the background runner so the thread does not mutate the record.
    monkeypatch.setattr(
        "app.services.evaluation_service.evaluation_job_runner.run_evaluation_job",
        lambda record: None,
    )

    record = evaluation_service.start_evaluation("model_1")

    assert record["id"].startswith("eval_")
    assert record["job_id"].startswith("evaljob_")
    assert record["model_id"] == "model_1"
    assert record["status"] == "queued"
    assert record["progress"] == 0
    assert record["val"] == {}
    assert record["test"] == {}
    assert record["run_meta"]["base_model"] == "/tmp/model_1.pt"
    assert record["started_at"]

    # Persisted.
    fetched = evaluation_repo.get_evaluation(record["id"])
    assert fetched is not None
    assert fetched["status"] == "queued"


@pytest.mark.integration
def test_start_evaluation_raises_value_error_for_unknown_model(isolated_app, monkeypatch):
    monkeypatch.setattr(
        "app.services.evaluation_service.evaluation_job_runner.run_evaluation_job",
        lambda record: None,
    )
    # Empty registry -> model_id not found.
    with pytest.raises(ValueError):
        evaluation_service.start_evaluation("no_such_model")


@pytest.mark.integration
def test_start_evaluation_raises_busy_when_training_running(isolated_app, monkeypatch):
    _seed_registry(model_id="model_1")
    _seed_running_train_job()
    monkeypatch.setattr(
        "app.services.evaluation_service.evaluation_job_runner.run_evaluation_job",
        lambda record: None,
    )

    with pytest.raises(EvaluationBusyError) as exc_info:
        evaluation_service.start_evaluation("model_1")
    # Training mutex message mentions training.
    assert "训练" in str(exc_info.value)

    # No eval record was persisted.
    assert evaluation_repo.read_evaluations() == []


@pytest.mark.integration
def test_start_evaluation_raises_busy_when_eval_running(isolated_app, monkeypatch):
    _seed_registry(model_id="model_1")
    _seed_running_eval()
    monkeypatch.setattr(
        "app.services.evaluation_service.evaluation_job_runner.run_evaluation_job",
        lambda record: None,
    )

    with pytest.raises(EvaluationBusyError) as exc_info:
        evaluation_service.start_evaluation("model_1")
    # Eval mutex message mentions running eval.
    assert "评估" in str(exc_info.value)

    # Still only the one seeded running record.
    all_recs = evaluation_repo.read_evaluations()
    assert len(all_recs) == 1


@pytest.mark.integration
def test_build_comparison_picks_best_per_metric(isolated_app):
    """max for map50/map50_95/precision/recall/f1; MIN for speed_ms."""
    recs = [
        _eval_record(rid="eval_low", val={
            "map50": 0.4, "map50_95": 0.3, "precision": 0.5,
            "recall": 0.6, "f1": 0.55, "speed_ms": 10.0,
        }),
        _eval_record(rid="eval_high_acc", val={
            "map50": 0.8, "map50_95": 0.7, "precision": 0.9,
            "recall": 0.85, "f1": 0.87, "speed_ms": 50.0,
        }),
        _eval_record(rid="eval_fast", val={
            "map50": 0.5, "map50_95": 0.4, "precision": 0.6,
            "recall": 0.5, "f1": 0.55, "speed_ms": 2.0,
        }),
    ]
    for r in recs:
        evaluation_repo.upsert_evaluation(r)

    result = evaluation_service.build_comparison(
        ["eval_low", "eval_high_acc", "eval_fast"]
    )

    best = result["best"]
    assert best["map50"] == "eval_high_acc"
    assert best["map50_95"] == "eval_high_acc"
    assert best["precision"] == "eval_high_acc"
    assert best["recall"] == "eval_high_acc"
    assert best["f1"] == "eval_high_acc"
    # Lower speed_ms is better.
    assert best["speed_ms"] == "eval_fast"
    assert len(result["records"]) == 3


@pytest.mark.integration
def test_build_comparison_empty_ids_returns_none_best(isolated_app):
    result = evaluation_service.build_comparison([])
    assert result["records"] == []
    for key in ("map50", "map50_95", "precision", "recall", "f1", "speed_ms"):
        assert result["best"][key] is None


@pytest.mark.integration
def test_build_comparison_unknown_ids_skipped(isolated_app):
    evaluation_repo.upsert_evaluation(_eval_record(
        rid="eval_real", val={"map50": 0.7, "speed_ms": 5.0}))

    result = evaluation_service.build_comparison(["eval_real", "ghost_id"])
    assert [r["id"] for r in result["records"]] == ["eval_real"]
    assert result["best"]["map50"] == "eval_real"


@pytest.mark.integration
def test_export_evaluation_json_single_record(isolated_app):
    evaluation_repo.upsert_evaluation(_eval_record(
        rid="eval_j1", val={"map50": 0.7}, started_at="2026-01-01T00:00:00Z"))

    body, mimetype, filename = evaluation_service.export_evaluation(
        ["eval_j1"], "json")

    assert mimetype == "application/json"
    assert filename.endswith(".json")
    payload = json.loads(body.decode("utf-8"))
    # Single record -> dict payload.
    assert isinstance(payload, dict)
    assert payload["id"] == "eval_j1"


@pytest.mark.integration
def test_export_evaluation_json_multiple_records(isolated_app):
    evaluation_repo.upsert_evaluation(_eval_record(rid="eval_j1"))
    evaluation_repo.upsert_evaluation(_eval_record(rid="eval_j2"))

    body, mimetype, filename = evaluation_service.export_evaluation(
        ["eval_j1", "eval_j2"], "json")

    assert mimetype == "application/json"
    assert filename == "evaluations_2.json"
    payload = json.loads(body.decode("utf-8"))
    assert isinstance(payload, list)
    assert len(payload) == 2


@pytest.mark.integration
def test_export_evaluation_csv_header_and_rows(isolated_app):
    rec = _eval_record(
        rid="eval_c1", model_id="m1", started_at="2026-01-01T00:00:00Z",
        val={"map50": 0.7, "map50_95": 0.6, "precision": 0.8,
             "recall": 0.75, "f1": 0.77, "speed_ms": 12.5, "fps": 80.0},
    )
    rec["test"] = {"map50": 0.6, "map50_95": 0.5, "precision": 0.7,
                   "recall": 0.65, "f1": 0.67, "speed_ms": 14.0, "fps": 71.0}
    evaluation_repo.upsert_evaluation(rec)

    body, mimetype, filename = evaluation_service.export_evaluation(
        ["eval_c1"], "csv")

    assert mimetype == "text/csv"
    assert filename.endswith(".csv")
    text = body.decode("utf-8")
    reader = list(csv.reader(io.StringIO(text)))
    header = reader[0]
    assert header == [
        "id", "model_name", "dataset", "map50", "map50_95",
        "precision", "recall", "f1", "speed_ms", "fps", "started_at",
    ]
    # One row per split (val + test).
    data_rows = reader[1:]
    assert len(data_rows) == 2
    datasets = [row[2] for row in data_rows]
    assert "val" in datasets and "test" in datasets


@pytest.mark.integration
def test_export_evaluation_unknown_format_raises(isolated_app):
    evaluation_repo.upsert_evaluation(_eval_record(rid="eval_u1"))
    with pytest.raises(ValueError):
        evaluation_service.export_evaluation(["eval_u1"], "xml")


# ---------------------------------------------------------------------------
# C. JOB RUNNER UNIT TEST (fake ultralytics.YOLO, no GPU)
# ---------------------------------------------------------------------------

class _FakeBox:
    """Stand-in for ultralytics result.box with the metric attributes the
    extractor reads (map50, map, mp, mr, maps, ap_class_index, p, r)."""

    def __init__(self):
        self.map50 = 0.75
        self.map = 0.65           # map50_95
        self.mp = 0.80            # precision
        self.mr = 0.70            # recall
        self.maps = [0.75, 0.60]  # per-class mAP
        self.ap_class_index = [0, 1]
        self.p = [0.8, 0.6]
        self.r = [0.7, 0.5]


class _FakeConfusionMatrix:
    def __init__(self):
        # Use a plain nested list (no .tolist -> exercises the list-comprehension path).
        self.matrix = [[10, 1], [2, 20]]


class _FakeValResult:
    def __init__(self):
        self.box = _FakeBox()
        self.speed = {"inference": 12.5, "preprocess": 1.0, "postprocess": 1.0}
        self.confusion_matrix = _FakeConfusionMatrix()
        self.names = {0: "cat", 1: "dog"}


class _FakeYOLO:
    """Stand-in for ultralytics.YOLO. ``val`` returns a fake result object."""

    def __init__(self, model_path):
        self.model_path = model_path

    def val(self, **kwargs):
        return _FakeValResult()


class _MalformedValResult:
    """Missing box/speed/confusion_matrix to exercise defensive defaults."""
    pass


class _MalformedYOLO:
    def __init__(self, model_path):
        self.model_path = model_path

    def val(self, **kwargs):
        return _MalformedValResult()


def _install_fake_ultralytics(monkeypatch, yolo_cls):
    fake_module = types.ModuleType("ultralytics")
    fake_module.YOLO = yolo_cls
    monkeypatch.setitem(sys.modules, "ultralytics", fake_module)


def _seed_train_job_with_data_yaml(tmp_path):
    """Seed a train job whose run_dir contains a data.yaml (runner reuses it)."""
    run_dir = tmp_path / "trainrun"
    run_dir.mkdir(parents=True)
    (run_dir / "data.yaml").write_text(
        "path: .\ntrain: train\nval: val\ntest: test\nnames:\n  0: cat\n  1: dog\n",
        encoding="utf-8",
    )
    Path(training_app.PATHS["train_jobs"]).write_text(
        json.dumps([{
            "id": "train_with_yaml",
            "status": "completed",
            "run_dir": str(run_dir),
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }]),
        encoding="utf-8",
    )
    return str(run_dir / "data.yaml")


@pytest.mark.integration
def test_run_evaluation_job_completes_with_metrics(isolated_app, monkeypatch, tmp_path):
    _seed_registry(model_id="model_1", path="/tmp/model_1.pt")
    _install_fake_ultralytics(monkeypatch, _FakeYOLO)
    _seed_train_job_with_data_yaml(tmp_path)

    record = _eval_record(rid="eval_run_ok", model_id="model_1", status="queued")
    evaluation_repo.upsert_evaluation(record)

    from app.services.evaluation_job_runner import run_evaluation_job
    run_evaluation_job(record)

    assert record["status"] == "completed"
    assert record["progress"] == 100
    assert record["completed_at"]

    val = record["val"]
    assert val["map50"] == pytest.approx(0.75)
    assert val["map50_95"] == pytest.approx(0.65)
    assert val["precision"] == pytest.approx(0.80)
    assert val["recall"] == pytest.approx(0.70)
    assert val["f1"] == pytest.approx(2 * 0.80 * 0.70 / (0.80 + 0.70))
    assert val["speed_ms"] == pytest.approx(12.5)
    assert val["fps"] == pytest.approx(1000.0 / 12.5)
    assert val["per_class"] != []
    assert val["confusion_matrix"]["matrix"] == [[10, 1], [2, 20]]

    # test split also populated.
    assert record["test"]["map50"] == pytest.approx(0.75)

    # Persisted to disk as completed.
    persisted = evaluation_repo.get_evaluation("eval_run_ok")
    assert persisted["status"] == "completed"
    assert persisted["progress"] == 100


@pytest.mark.integration
def test_run_evaluation_job_missing_model_marks_failed(isolated_app, monkeypatch, tmp_path):
    # Registry empty -> model not found.
    _install_fake_ultralytics(monkeypatch, _FakeYOLO)
    _seed_train_job_with_data_yaml(tmp_path)

    record = _eval_record(rid="eval_run_nomodel", model_id="ghost", status="queued")
    evaluation_repo.upsert_evaluation(record)

    from app.services.evaluation_job_runner import run_evaluation_job
    run_evaluation_job(record)

    assert record["status"] == "failed"
    assert record["error"]
    assert record["completed_at"]

    persisted = evaluation_repo.get_evaluation("eval_run_nomodel")
    assert persisted["status"] == "failed"


@pytest.mark.integration
def test_run_evaluation_job_no_dataset_marks_failed(isolated_app, monkeypatch, tmp_path):
    _seed_registry(model_id="model_1", path="/tmp/model_1.pt")
    _install_fake_ultralytics(monkeypatch, _FakeYOLO)
    # No train job with a data.yaml, and force the fresh-dataset builder to
    # raise (no annotated images available) -> _resolve_data_yaml returns None
    # -> runner fails the job with "no dataset with val+test splits available".
    monkeypatch.setattr(
        "app.services.training_service.build_yolo_training_dataset",
        lambda work_dir: (_ for _ in ()).throw(
            RuntimeError("no annotated images available")),
    )

    record = _eval_record(rid="eval_run_nodata", model_id="model_1", status="queued")
    evaluation_repo.upsert_evaluation(record)

    from app.services.evaluation_job_runner import run_evaluation_job
    run_evaluation_job(record)

    assert record["status"] == "failed"
    assert record["error"]


@pytest.mark.integration
def test_run_evaluation_job_never_raises_on_malformed_result(isolated_app, monkeypatch, tmp_path):
    _seed_registry(model_id="model_1", path="/tmp/model_1.pt")
    _install_fake_ultralytics(monkeypatch, _MalformedYOLO)
    _seed_train_job_with_data_yaml(tmp_path)

    record = _eval_record(rid="eval_run_malformed", model_id="model_1", status="queued")
    evaluation_repo.upsert_evaluation(record)

    from app.services.evaluation_job_runner import run_evaluation_job
    # Must NOT raise even though the fake result is missing every attribute.
    run_evaluation_job(record)

    # Status still completed (extraction defaults to 0.0/[]).
    assert record["status"] == "completed"
    assert record["progress"] == 100
    val = record["val"]
    assert val["map50"] == 0.0
    assert val["per_class"] == []
    assert val["confusion_matrix"] == {"matrix": [], "classes": []}


# ---------------------------------------------------------------------------
# D. ENDPOINT INTEGRATION TESTS
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_post_evaluate_registered_model_returns_200(isolated_app, monkeypatch):
    _seed_registry(model_id="model_1", path="/tmp/model_1.pt")
    monkeypatch.setattr(
        "app.services.evaluation_service.evaluation_job_runner.run_evaluation_job",
        lambda record: None,
    )

    resp = isolated_app.test_client().post("/api/models/model_1/evaluate")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["id"].startswith("eval_")
    assert body["status"] in ("queued", "running")
    assert body["model_id"] == "model_1"


@pytest.mark.integration
def test_post_evaluate_while_training_running_returns_409(isolated_app, monkeypatch):
    _seed_registry(model_id="model_1")
    _seed_running_train_job()
    monkeypatch.setattr(
        "app.services.evaluation_service.evaluation_job_runner.run_evaluation_job",
        lambda record: None,
    )

    resp = isolated_app.test_client().post("/api/models/model_1/evaluate")

    assert resp.status_code == 409
    assert resp.get_json()["status"] == "busy"


@pytest.mark.integration
def test_post_evaluate_unknown_model_returns_404(isolated_app, monkeypatch):
    monkeypatch.setattr(
        "app.services.evaluation_service.evaluation_job_runner.run_evaluation_job",
        lambda record: None,
    )

    resp = isolated_app.test_client().post("/api/models/ghost/evaluate")

    assert resp.status_code == 404
    assert "error" in resp.get_json()


@pytest.mark.integration
def test_get_evaluations_list(isolated_app):
    evaluation_repo.upsert_evaluation(_eval_record(rid="eval_l1"))
    evaluation_repo.upsert_evaluation(_eval_record(rid="eval_l2"))

    resp = isolated_app.test_client().get("/api/evaluations")

    assert resp.status_code == 200
    body = resp.get_json()
    assert "records" in body
    ids = [r["id"] for r in body["records"]]
    assert "eval_l1" in ids and "eval_l2" in ids


@pytest.mark.integration
def test_get_evaluation_detail_hit_and_miss(isolated_app):
    evaluation_repo.upsert_evaluation(_eval_record(rid="eval_d1"))

    client = isolated_app.test_client()
    ok = client.get("/api/evaluations/eval_d1")
    assert ok.status_code == 200
    assert ok.get_json()["id"] == "eval_d1"

    miss = client.get("/api/evaluations/does_not_exist")
    assert miss.status_code == 404


@pytest.mark.integration
def test_get_evaluations_compare(isolated_app):
    evaluation_repo.upsert_evaluation(_eval_record(
        rid="eval_cmp_a", val={"map50": 0.4, "speed_ms": 10.0}))
    evaluation_repo.upsert_evaluation(_eval_record(
        rid="eval_cmp_b", val={"map50": 0.9, "speed_ms": 20.0}))

    resp = isolated_app.test_client().get(
        "/api/evaluations/compare?ids=eval_cmp_a,eval_cmp_b")

    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body["records"]) == 2
    assert body["best"]["map50"] == "eval_cmp_b"
    assert body["best"]["speed_ms"] == "eval_cmp_a"


@pytest.mark.integration
def test_get_evaluation_export_json(isolated_app):
    evaluation_repo.upsert_evaluation(_eval_record(rid="eval_exj"))

    resp = isolated_app.test_client().get(
        "/api/evaluations/eval_exj/export?format=json")

    assert resp.status_code == 200
    assert resp.mimetype == "application/json"
    cd = resp.headers.get("Content-Disposition", "")
    assert "attachment" in cd
    assert cd.endswith(".json")
    # Body is valid JSON.
    json.loads(resp.data.decode("utf-8"))


@pytest.mark.integration
def test_get_evaluation_export_csv(isolated_app):
    rec = _eval_record(rid="eval_exc")
    rec["val"] = {"map50": 0.7, "speed_ms": 12.0}
    rec["test"] = {"map50": 0.6, "speed_ms": 14.0}
    evaluation_repo.upsert_evaluation(rec)

    resp = isolated_app.test_client().get(
        "/api/evaluations/eval_exc/export?format=csv")

    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    text = resp.data.decode("utf-8")
    lines = list(csv.reader(io.StringIO(text)))
    assert lines[0][0] == "id"
    # Header + 2 split rows.
    assert len(lines) == 3


@pytest.mark.integration
def test_get_evaluation_page_renders_html(isolated_app):
    resp = isolated_app.test_client().get("/evaluation")

    assert resp.status_code == 200
    assert "text/html" in resp.mimetype
