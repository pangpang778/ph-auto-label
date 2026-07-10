"""Phase 0 characterization test — annotation flow (HTTP + persistence golden baseline).

Locks the CURRENT observable HTTP + on-disk behavior of the core annotation
CRUD + export flow implemented in the ``app.py`` monolith (2905 lines), so the
upcoming Phase 1 refactor (extracting ``app.py`` into a layered ``app/``
package) cannot silently change it.

Scope (endpoints locked):
  - GET  /api/classes                 -> bare list of class objects
  - GET  /api/images                  -> {"images": [image-meta, ...]}
  - GET  /api/annotations/<name>      -> bare list of annotation objects
  - POST /api/annotations/<name>      -> {message, metrics} + X-Annotations-* headers
                                          + persists annotations.json (+ .bak backup)
  - POST /api/export                  -> zip file attachment (datasets_<ts>.zip)
                                          + 400 / 500 error shapes

What is locked per endpoint: HTTP status code, top-level JSON shape (list vs
dict, keys present, stable value types), and persistence side-effects
(annotations.json / classes.json content snapshot after a POST). Non-
deterministic values (timestamps, perf metrics in ms, zip blob bytes) are
asserted only on their stable structural keys, never on concrete values.

The ``isolated_app`` fixture (tests/conftest.py) redirects every ``PATHS[key]``
to a per-test ``tmp_path`` and is used as-is — it is NOT redefined here.
"""
import io
import json
import re
import zipfile
from pathlib import Path

import pytest
from PIL import Image

import app as training_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_upload_image(name: str = "img1.jpg", size=(50, 50)) -> str:
    """Write a real image into the isolated uploads dir; return its filename."""
    upload_dir = Path(training_app.PATHS["uploads"])
    path = upload_dir / name
    Image.new("RGB", size, color=(120, 60, 30)).save(path)
    return name


def _annotation_payload():
    """A representative annotation payload the UI sends for one image."""
    return [
        {
            "class": "part",
            "points": [
                {"x": 10, "y": 10},
                {"x": 40, "y": 40},
            ],
        }
    ]


def _read_persisted_annotations() -> dict:
    return json.loads(Path(training_app.PATHS["annotations"]).read_text(encoding="utf-8"))


def _read_persisted_classes() -> list:
    return json.loads(Path(training_app.PATHS["classes"]).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# GET /api/classes — bare list of class objects
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_get_classes_returns_bare_list_of_class_objects(isolated_app):
    """GET /api/classes returns 200 with a JSON array (not wrapped in a dict).

    The fixture seeds classes.json with [{"name": "part", "color": "#fff"}],
    so the response must be a list whose members are dicts exposing at least
    the ``name`` and ``color`` keys."""
    client = isolated_app.test_client()

    response = client.get("/api/classes")

    assert response.status_code == 200
    body = response.get_json()
    assert isinstance(body, list), f"expected list, got {type(body).__name__}: {body!r}"
    assert len(body) >= 1
    first = body[0]
    assert isinstance(first, dict)
    assert "name" in first
    assert "color" in first
    assert first["name"] == "part"


@pytest.mark.integration
def test_get_classes_returns_empty_list_when_classes_file_missing(isolated_app):
    """When classes.json does not exist, GET /api/classes returns 200 + [].

    Locks the os.path.exists-guarded default (empty list), not an error."""
    Path(training_app.PATHS["classes"]).unlink()

    response = isolated_app.test_client().get("/api/classes")

    assert response.status_code == 200
    assert response.get_json() == []


# ---------------------------------------------------------------------------
# GET /api/images — {"images": [...]} with per-image annotation counts
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_get_images_returns_images_dict_with_dims_and_annotation_count(isolated_app):
    """GET /api/images -> 200 {"images": [{"name","width","height","annotation_count"}]}.

    Locks the top-level wrapper key, the per-image object shape, and that
    annotation_count reflects annotations.json (which the fixture seeds empty).
    """
    _create_upload_image("img1.jpg", size=(50, 50))
    client = isolated_app.test_client()

    response = client.get("/api/images")

    assert response.status_code == 200
    body = response.get_json()
    assert isinstance(body, dict)
    assert list(body.keys()) == ["images"]
    assert isinstance(body["images"], list)
    assert len(body["images"]) == 1

    img = body["images"][0]
    assert set(img.keys()) == {"name", "width", "height", "annotation_count"}
    assert img["name"] == "img1.jpg"
    assert img["width"] == 50
    assert img["height"] == 50
    assert img["annotation_count"] == 0
    assert isinstance(img["annotation_count"], int)


@pytest.mark.integration
def test_get_images_annotation_count_reflects_persisted_annotations(isolated_app):
    """annotation_count is derived from annotations.json[image_name] length."""
    name = _create_upload_image("img1.jpg")
    Path(training_app.PATHS["annotations"]).write_text(
        json.dumps({name: _annotation_payload()}), encoding="utf-8"
    )

    response = isolated_app.test_client().get("/api/images")

    assert response.status_code == 200
    imgs = response.get_json()["images"]
    assert imgs[0]["annotation_count"] == 1


@pytest.mark.integration
def test_get_images_ignores_non_image_files(isolated_app):
    """Only files with image extensions are listed; others are skipped."""
    _create_upload_image("img1.jpg")
    (Path(training_app.PATHS["uploads"]) / "notes.txt").write_text("ignore", encoding="utf-8")
    (Path(training_app.PATHS["uploads"]) / "readme.md").write_text("ignore", encoding="utf-8")

    response = isolated_app.test_client().get("/api/images")

    assert response.status_code == 200
    names = [img["name"] for img in response.get_json()["images"]]
    assert names == ["img1.jpg"]


@pytest.mark.integration
def test_get_images_returns_empty_list_when_uploads_empty(isolated_app):
    response = isolated_app.test_client().get("/api/images")

    assert response.status_code == 200
    assert response.get_json() == {"images": []}


# ---------------------------------------------------------------------------
# GET /api/annotations/<image_name> — bare list
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_get_annotations_returns_bare_list_for_annotated_image(isolated_app):
    """GET /api/annotations/<name> -> 200 bare list (the stored annotations)."""
    name = _create_upload_image("img1.jpg")
    payload = _annotation_payload()
    Path(training_app.PATHS["annotations"]).write_text(
        json.dumps({name: payload}), encoding="utf-8"
    )

    response = isolated_app.test_client().get(f"/api/annotations/{name}")

    assert response.status_code == 200
    body = response.get_json()
    assert isinstance(body, list)
    assert body == payload


@pytest.mark.integration
def test_get_annotations_returns_empty_list_for_unannotated_image(isolated_app):
    """Image with no stored annotations -> 200 + [] (not 404, not null)."""
    _create_upload_image("img1.jpg")

    response = isolated_app.test_client().get("/api/annotations/img1.jpg")

    assert response.status_code == 200
    assert response.get_json() == []


@pytest.mark.integration
def test_get_annotations_returns_empty_list_for_unknown_image(isolated_app):
    """Unknown image name -> 200 + [] (the .get(image_name, []) default)."""
    response = isolated_app.test_client().get("/api/annotations/does_not_exist.jpg")

    assert response.status_code == 200
    assert response.get_json() == []


# ---------------------------------------------------------------------------
# POST /api/annotations/<image_name> — persist + response shape + headers
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_post_annotations_persists_payload_keyed_by_image_name(isolated_app):
    """POST /api/annotations/<name> writes the payload under image_name in
    annotations.json (atomic temp-file + os.replace). Locks the persisted
    snapshot shape: a dict mapping image_name -> posted list."""
    name = _create_upload_image("img1.jpg")
    payload = _annotation_payload()
    client = isolated_app.test_client()

    response = client.post(f"/api/annotations/{name}", json=payload)

    assert response.status_code == 200
    persisted = _read_persisted_annotations()
    assert isinstance(persisted, dict)
    assert persisted[name] == payload

    # A follow-up GET reflects the persisted value (round-trip).
    got = client.get(f"/api/annotations/{name}").get_json()
    assert got == payload


@pytest.mark.integration
def test_post_annotations_creates_backup_file(isolated_app):
    """Saving backs up the existing annotations.json to annotations.json.bak."""
    name = _create_upload_image("img1.jpg")
    annotations_path = Path(training_app.PATHS["annotations"])
    annotations_path.write_text(json.dumps({name: [{"class": "old", "points": []}]}), encoding="utf-8")

    isolated_app.test_client().post(f"/api/annotations/{name}", json=_annotation_payload())

    backup = annotations_path.with_suffix(annotations_path.suffix + ".bak")
    assert backup.exists()
    assert json.loads(backup.read_text(encoding="utf-8")) == {name: [{"class": "old", "points": []}]}


@pytest.mark.integration
def test_post_annotations_preserves_other_images(isolated_app):
    """Saving for one image must not clobber annotations for other images."""
    img_a = _create_upload_image("a.jpg")
    img_b = _create_upload_image("b.jpg")
    existing = {img_a: [{"class": "part", "points": [{"x": 1, "y": 1}]}]}
    Path(training_app.PATHS["annotations"]).write_text(json.dumps(existing), encoding="utf-8")
    payload_b = _annotation_payload()

    isolated_app.test_client().post(f"/api/annotations/{img_b}", json=payload_b)

    persisted = _read_persisted_annotations()
    assert persisted[img_a] == existing[img_a]
    assert persisted[img_b] == payload_b


@pytest.mark.integration
def test_post_annotations_response_shape_and_metrics_headers(isolated_app):
    """POST returns {message, metrics} with stable metrics keys and emits five
    X-Annotations-* response headers. Metric VALUES are non-deterministic (ms),
    so only the keys and types are locked."""
    name = _create_upload_image("img1.jpg")

    response = isolated_app.test_client().post(f"/api/annotations/{name}", json=_annotation_payload())

    assert response.status_code == 200
    body = response.get_json()
    assert isinstance(body, dict)
    assert body["message"] == "Annotations saved successfully"

    metrics = body["metrics"]
    assert isinstance(metrics, dict)
    assert set(metrics.keys()) == {
        "lock_wait_ms",
        "read_json_ms",
        "backup_ms",
        "write_verify_replace_ms",
        "total_ms",
    }
    for value in metrics.values():
        assert isinstance(value, int)

    # The five performance headers must all be present (stringified ints).
    expected_headers = {
        "X-Annotations-Lock-Wait-Ms",
        "X-Annotations-Read-Ms",
        "X-Annotations-Backup-Ms",
        "X-Annotations-Write-Ms",
        "X-Annotations-Total-Ms",
    }
    present = {h for h in expected_headers if h in response.headers}
    assert present == expected_headers, f"missing headers: {expected_headers - present}"


@pytest.mark.integration
def test_post_annotations_overwrites_existing_for_same_image(isolated_app):
    """A second POST for the same image replaces (not appends to) its annotations."""
    name = _create_upload_image("img1.jpg")
    client = isolated_app.test_client()
    first = [{"class": "part", "points": [{"x": 0, "y": 0}]}]
    second = [{"class": "part", "points": [{"x": 5, "y": 5}, {"x": 9, "y": 9}]}]

    client.post(f"/api/annotations/{name}", json=first)
    client.post(f"/api/annotations/{name}", json=second)

    persisted = _read_persisted_annotations()
    assert persisted[name] == second
    assert len(persisted[name]) == 1  # replaced, not appended


# ---------------------------------------------------------------------------
# POST /api/export — zip artifact + error shapes
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_export_returns_zip_attachment_with_yolo_layout(isolated_app):
    """POST /api/export -> 200 with a zip file attachment.

    The zip must contain the YOLO directory layout (train/val/test with images/
    labels subdirs) and a data.yaml at the root. The Content-Disposition
    filename + zip bytes are non-deterministic (filename derives from an image
    name on this Flask/Werkzeug version, not the datasets_<ts> value passed to
    download_name), so we lock only: attachment disposition, .zip filename
    suffix, and the internal zip structure — not the blob."""
    _create_upload_image("img1.jpg", size=(50, 50))
    Path(training_app.PATHS["annotations"]).write_text(
        json.dumps({"img1.jpg": _annotation_payload()}), encoding="utf-8"
    )
    # classes.json fixture seed has class "part" — selected_classes must match.
    client = isolated_app.test_client()

    response = client.post(
        "/api/export",
        json={
            "train_ratio": 1.0,
            "val_ratio": 0.0,
            "test_ratio": 0.0,
            "selected_classes": ["part"],
            "sample_selection": "all",
            "export_data_type": "yolo",
            "export_prefix": "",
        },
    )

    assert response.status_code == 200
    # Zip mimetype varies by platform (application/zip on POSIX,
    # application/x-zip-compressed on Windows), and the Content-Disposition
    # filename is derived from an image name (not the datasets_<ts>.zip value
    # passed to download_name on this Flask/Werkzeug version). Lock only the
    # stable observable facts: attachment disposition, .zip filename suffix,
    # and a valid zip body with the YOLO layout.
    cd = response.headers.get("Content-Disposition", "")
    assert "attachment" in cd, cd
    assert re.search(r'filename=[^;]+\.zip', cd), f"unexpected Content-Disposition: {cd!r}"

    archive = zipfile.ZipFile(io.BytesIO(response.data))
    names = archive.namelist()
    # YOLO directory skeleton must be present.
    assert any(n.startswith("train/images/") for n in names)
    assert any(n.startswith("train/labels/") for n in names)
    assert "data.yaml" in names
    # The annotated image lands in train (train_ratio=1.0) — image copied + label written.
    assert any(n == "train/images/img1.jpg" for n in names), names
    assert any(n == "train/labels/img1.txt" for n in names), names
    label = archive.read("train/labels/img1.txt").decode("utf-8").strip()
    assert label != "", "label file for an annotated selected-class image must not be empty"
    # YOLO line format: "<class_id> <cx> <cy> <w> <h>" with 5 numeric tokens.
    tokens = label.split()
    assert len(tokens) == 5
    assert tokens[0] == "0"  # selected_classes.index("part") == 0


@pytest.mark.integration
def test_export_writes_empty_label_file_for_unannotated_image(isolated_app):
    """Unannotated images still get a (empty) label file alongside the image."""
    _create_upload_image("img1.jpg", size=(50, 50))  # no annotations persisted
    client = isolated_app.test_client()

    response = client.post(
        "/api/export",
        json={
            "train_ratio": 1.0,
            "val_ratio": 0.0,
            "test_ratio": 0.0,
            "selected_classes": ["part"],
            "sample_selection": "all",
            "export_data_type": "yolo",
        },
    )

    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.data))
    assert "train/labels/img1.txt" in archive.namelist()
    assert archive.read("train/labels/img1.txt").decode("utf-8") == ""


@pytest.mark.integration
def test_export_sample_selection_annotated_excludes_unannotated(isolated_app):
    """sample_selection='annotated' filters out images with no annotations:
    the unannotated image is dropped entirely from the zip."""
    _create_upload_image("annotated.jpg", size=(50, 50))
    _create_upload_image("bare.jpg", size=(50, 50))
    Path(training_app.PATHS["annotations"]).write_text(
        json.dumps({"annotated.jpg": _annotation_payload()}), encoding="utf-8"
    )
    client = isolated_app.test_client()

    response = client.post(
        "/api/export",
        json={
            "train_ratio": 1.0,
            "val_ratio": 0.0,
            "test_ratio": 0.0,
            "selected_classes": ["part"],
            "sample_selection": "annotated",
            "export_data_type": "yolo",
        },
    )

    assert response.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(response.data)).namelist()
    assert any(n == "train/images/annotated.jpg" for n in names)
    assert not any("bare.jpg" in n for n in names), names


@pytest.mark.integration
def test_export_rejects_unsupported_data_type(isolated_app):
    """Unsupported export_data_type -> 400 {"error": "不支持的导出数据类型"}."""
    response = isolated_app.test_client().post(
        "/api/export",
        json={"export_data_type": "coco"},
    )

    assert response.status_code == 400
    body = response.get_json()
    assert isinstance(body, dict)
    assert body["error"] == "不支持的导出数据类型"
