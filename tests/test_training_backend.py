import json
import os
import random
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as training_app
from training_artifacts import read_training_metrics_series, resolve_artifact


@pytest.fixture()
def isolated_app(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    annotations_dir = tmp_path / "static" / "annotations"
    train_work_dir = tmp_path / "static" / "train_work"
    models_dir = tmp_path / "plugins" / "yolo11" / "models"
    for path in (upload_dir, annotations_dir, train_work_dir, models_dir):
        path.mkdir(parents=True, exist_ok=True)

    files = {
        "ANNOTATIONS_FILE": annotations_dir / "annotations.json",
        "CLASSES_FILE": annotations_dir / "classes.json",
        "TRAINING_SPLITS_FILE": annotations_dir / "training_splits.json",
        "TRAIN_JOBS_FILE": annotations_dir / "train_jobs.json",
        "MODEL_REGISTRY_FILE": annotations_dir / "model_registry.json",
        "ACTIVE_MODEL_FILE": annotations_dir / "active_model.json",
    }
    defaults = {
        "ANNOTATIONS_FILE": {},
        "CLASSES_FILE": [{"name": "part", "color": "#fff"}],
        "TRAINING_SPLITS_FILE": {},
        "TRAIN_JOBS_FILE": [],
        "MODEL_REGISTRY_FILE": [],
        "ACTIVE_MODEL_FILE": {"model_id": "", "model_name": "", "model_path": ""},
    }
    for name, path in files.items():
        path.write_text(json.dumps(defaults[name]), encoding="utf-8")
        monkeypatch.setattr(training_app, name, str(path))

    monkeypatch.setitem(training_app.app.config, "UPLOAD_FOLDER", str(upload_dir))
    monkeypatch.setattr(training_app.app, "root_path", str(tmp_path))
    yield training_app.app


def _write_training_images(upload_dir: Path, annotations_path: Path, count: int = 20) -> list[str]:
    annotations = {}
    names = []
    for index in range(count):
        name = f"image_{index:02d}.jpg"
        path = upload_dir / name
        Image.new("RGB", (100, 80), color=(index, 20, 40)).save(path)
        annotations[name] = [
            {
                "class": "part",
                "points": [
                    {"x": 10, "y": 10},
                    {"x": 50, "y": 40},
                ],
            }
        ]
        names.append(name)
    annotations_path.write_text(json.dumps(annotations), encoding="utf-8")
    return names


@pytest.mark.unit
def test_normalize_split_config_accepts_percentages_and_rejects_invalid_ratios():
    config = training_app.normalize_split_config(
        {"train_ratio": 70, "val_ratio": 20, "test_ratio": 10, "class_filter": "part, other"}
    )

    assert config["train_ratio"] == pytest.approx(0.7)
    assert config["val_ratio"] == pytest.approx(0.2)
    assert config["test_ratio"] == pytest.approx(0.1)
    assert config["class_filter"] == ["part", "other"]

    with pytest.raises(ValueError):
        training_app.normalize_split_config({"train_ratio": 0.8, "val_ratio": 0.3, "test_ratio": 0.1})

    with pytest.raises(ValueError):
        training_app.normalize_split_config({"train_ratio": -0.1, "val_ratio": 0.6, "test_ratio": 0.5})


@pytest.mark.integration
def test_split_save_and_reset_endpoints_persist_assignments(isolated_app):
    _write_training_images(
        Path(isolated_app.config["UPLOAD_FOLDER"]),
        Path(training_app.ANNOTATIONS_FILE),
        count=20,
    )

    client = isolated_app.test_client()
    save_response = client.post(
        "/api/train/split",
        json={"train_ratio": 0.6, "val_ratio": 0.2, "test_ratio": 0.2, "profile_id": "default"},
    )

    assert save_response.status_code == 200
    saved = save_response.get_json()
    assert saved["counts"] == {"train": 12, "val": 4, "test": 4}
    assert sum(len(saved["split_config"]["assignments"][split]) for split in ("train", "val", "test")) == 20

    persisted = json.loads(Path(training_app.TRAINING_SPLITS_FILE).read_text(encoding="utf-8"))
    assert "default" in persisted
    assert persisted["default"]["split_config"]["assignments"]["train"]

    reset_response = client.post("/api/train/split/reset", json={"profile_id": "default"})

    assert reset_response.status_code == 200
    reset = reset_response.get_json()
    assert reset["counts"] == {"train": 16, "val": 3, "test": 1}
    assert json.loads(Path(training_app.TRAINING_SPLITS_FILE).read_text(encoding="utf-8")) == {}


@pytest.mark.integration
def test_split_endpoint_rejects_invalid_ratio(isolated_app):
    client = isolated_app.test_client()

    response = client.post(
        "/api/train/split",
        json={"train_ratio": 0.9, "val_ratio": 0.2, "test_ratio": 0.1},
    )

    assert response.status_code == 400
    assert "sum to 1.0 or 100" in response.get_json()["error"]


@pytest.mark.unit
def test_metrics_parser_returns_numeric_rows_and_preserves_text(tmp_path):
    results_csv = tmp_path / "results.csv"
    results_csv.write_text(
        "epoch, train/box_loss, metrics/mAP50(B), note\n"
        "1, 0.25, 0.70, warmup\n"
        "2, 0.10, 0.88, done\n",
        encoding="utf-8",
    )

    series = read_training_metrics_series(str(results_csv))

    assert series["available"] is True
    assert series["columns"] == ["epoch", "train/box_loss", "metrics/mAP50(B)", "note"]
    assert series["rows"] == [
        {"epoch": 1.0, "train/box_loss": 0.25, "metrics/mAP50(B)": 0.70, "note": "warmup"},
        {"epoch": 2.0, "train/box_loss": 0.10, "metrics/mAP50(B)": 0.88, "note": "done"},
    ]


def test_metrics_parser_reports_unavailable_for_missing_file(tmp_path):
    assert read_training_metrics_series(str(tmp_path / "missing.csv")) == {
        "available": False,
        "columns": [],
        "rows": [],
    }


@pytest.mark.unit
def test_resolve_artifact_allows_recorded_file_and_rejects_traversal(tmp_path):
    allowed_root = tmp_path / "static" / "train_work"
    job_dir = allowed_root / "job-1"
    job_dir.mkdir(parents=True)
    model_file = job_dir / "best.pt"
    model_file.write_bytes(b"model")

    assert resolve_artifact({"artifact_path": str(model_file)}, "model", [str(allowed_root)]) == str(model_file)

    outside_file = tmp_path / "secret.txt"
    outside_file.write_text("secret", encoding="utf-8")
    with pytest.raises(PermissionError):
        resolve_artifact({"artifact_path": str(outside_file)}, "model", [str(allowed_root)])

    with pytest.raises(ValueError):
        resolve_artifact({"artifact_path": str(model_file)}, "../../secret", [str(allowed_root)])


@pytest.mark.integration
def test_artifact_endpoint_rejects_corrupted_traversal_path(isolated_app, tmp_path):
    outside_file = tmp_path / "outside-results.csv"
    outside_file.write_text("epoch,metric\n1,0.9\n", encoding="utf-8")
    Path(training_app.TRAIN_JOBS_FILE).write_text(
        json.dumps([{"id": "job-1", "results_csv": str(outside_file)}]),
        encoding="utf-8",
    )

    response = isolated_app.test_client().get("/api/train/jobs/job-1/download/results_csv")

    assert response.status_code == 403
    assert response.get_json() == {"error": "artifact is not allowed"}


@pytest.mark.integration
def test_non_download_artifact_endpoints_reject_corrupted_outside_paths(isolated_app, tmp_path):
    outside_log = tmp_path / "outside.log"
    outside_csv = tmp_path / "outside-results.csv"
    outside_log.write_text("secret log", encoding="utf-8")
    outside_csv.write_text("epoch,metric\n1,0.9\n", encoding="utf-8")
    Path(training_app.TRAIN_JOBS_FILE).write_text(
        json.dumps([{"id": "job-1", "log_path": str(outside_log), "results_csv": str(outside_csv)}]),
        encoding="utf-8",
    )
    client = isolated_app.test_client()

    logs_response = client.get("/api/train/jobs/job-1/logs")
    metrics_response = client.get("/api/train/jobs/job-1/metrics")

    assert logs_response.status_code == 403
    assert logs_response.get_json() == {"error": "artifact is not allowed"}
    assert metrics_response.status_code == 403
    assert metrics_response.get_json() == {"error": "artifact is not allowed"}


@pytest.mark.integration
def test_native_yolo_image_gallery_lists_and_serves_allowed_images(isolated_app, tmp_path):
    run_dir = tmp_path / "static" / "train_work" / "gallery-job" / "runs" / "detector"
    run_dir.mkdir(parents=True)
    (run_dir / "results.png").write_bytes(b"results-png")
    (run_dir / "confusion_matrix.png").write_bytes(b"confusion-png")
    (run_dir / "BoxPR_curve.png").write_bytes(b"pr-png")
    (run_dir / "train_batch0.jpg").write_bytes(b"jpg-bytes")
    (run_dir / "notes.txt").write_text("ignore", encoding="utf-8")
    Path(training_app.TRAIN_JOBS_FILE).write_text(
        json.dumps([{"id": "gallery-job", "status": "completed", "run_dir": str(run_dir)}]),
        encoding="utf-8",
    )
    client = isolated_app.test_client()

    gallery_response = client.get("/api/train/jobs/gallery-job/native-images")
    image_response = client.get("/api/train/jobs/gallery-job/native-images/confusion_matrix.png")
    traversal_response = client.get("/api/train/jobs/gallery-job/native-images/..%2Fsecret.png")

    assert gallery_response.status_code == 200
    gallery = gallery_response.get_json()
    names = [item["name"] for item in gallery["images"]]
    assert names == ["results.png", "confusion_matrix.png", "BoxPR_curve.png", "train_batch0.jpg"]
    assert gallery["images"][0]["title"] == "训练总览图"
    assert image_response.status_code == 200
    assert image_response.data == b"confusion-png"
    assert traversal_response.status_code in {400, 403, 404}


@pytest.mark.integration
def test_legacy_completed_job_artifacts_fall_back_to_run_dir(isolated_app, tmp_path):
    run_dir = tmp_path / "static" / "train_work" / "legacy-job" / "runs" / "detector"
    run_dir.mkdir(parents=True)
    (run_dir / "results.csv").write_text("epoch,metrics/mAP50(B)\n1,0.75\n", encoding="utf-8")
    (run_dir / "results.png").write_bytes(b"png-bytes")
    Path(training_app.TRAIN_JOBS_FILE).write_text(
        json.dumps([{"id": "legacy-job", "status": "completed", "run_dir": str(run_dir)}]),
        encoding="utf-8",
    )
    client = isolated_app.test_client()

    metrics_response = client.get("/api/train/jobs/legacy-job/metrics")
    image_response = client.get("/api/train/jobs/legacy-job/results-image")

    assert metrics_response.status_code == 200
    assert metrics_response.get_json()["rows"] == [{"epoch": 1.0, "metrics/mAP50(B)": 0.75}]
    assert image_response.status_code == 200
    assert image_response.data == b"png-bytes"


@pytest.mark.unit
def test_legacy_dataset_split_matches_previous_deterministic_fallback(isolated_app, tmp_path):
    image_names = _write_training_images(
        Path(isolated_app.config["UPLOAD_FOLDER"]),
        Path(training_app.ANNOTATIONS_FILE),
        count=20,
    )
    expected = list(sorted(image_names))
    random.Random(42).shuffle(expected)

    dataset = training_app.build_yolo_training_dataset(str(tmp_path / "work"), split_config=None)

    assert dataset["assignments"]["train"] == expected[:16]
    assert dataset["assignments"]["val"] == expected[16:19]
    assert dataset["assignments"]["test"] == expected[19:]
    assert dataset["split_counts"] == {"train": 16, "val": 3, "test": 1}
    for split, names in dataset["assignments"].items():
        for image_name in names:
            assert (Path(dataset["dataset_root"]) / split / "images" / image_name).is_file()
            assert (Path(dataset["dataset_root"]) / split / "labels" / f"{Path(image_name).stem}.txt").is_file()
