"""MEDIUM: POST /api/images/delete endpoint behavior.

Covers happy path (multi-image delete + annotation key removal), partial
failure (one missing image -> 400 with errors but deleted_count reflects the
one that succeeded), and path-traversal containment (``../etc/passwd`` does not
touch system files and is reported as an error, not a 500).
"""
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

import app as training_app  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _seed_image(name: str) -> str:
    upload_dir = Path(training_app.PATHS["uploads"])
    upload_dir.mkdir(parents=True, exist_ok=True)
    path = upload_dir / name
    Image.new("RGB", (30, 30), color=(5, 5, 5)).save(path)
    return name


def _read_annotations() -> dict:
    return json.loads(Path(training_app.PATHS["annotations"]).read_text(encoding="utf-8"))


@pytest.mark.integration
def test_delete_images_happy_path_removes_files_and_annotation_keys(isolated_app):
    img_a = _seed_image("a.jpg")
    img_b = _seed_image("b.jpg")
    # Seed annotations for both.
    Path(training_app.PATHS["annotations"]).write_text(
        json.dumps({img_a: [{"class": "part"}], img_b: [{"class": "part"}]}),
        encoding="utf-8",
    )
    client = isolated_app.test_client()

    response = client.post("/api/images/delete", json={"images": [img_a, img_b]})

    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert body["deleted_count"] == 2
    # Files gone.
    assert not (Path(training_app.PATHS["uploads"]) / img_a).exists()
    assert not (Path(training_app.PATHS["uploads"]) / img_b).exists()
    # Annotation keys removed.
    persisted = _read_annotations()
    assert img_a not in persisted
    assert img_b not in persisted


@pytest.mark.integration
def test_delete_images_partial_failure_reports_missing_and_counts_succeeded(isolated_app):
    img_a = _seed_image("exists.jpg")
    client = isolated_app.test_client()

    response = client.post(
        "/api/images/delete",
        json={"images": [img_a, "ghost.jpg"]},
    )

    # One missing -> errors present -> 400 (per handler: errors -> 400).
    assert response.status_code == 400
    body = response.get_json()
    assert body["success"] is False
    assert body["deleted_count"] == 1
    assert "error" in body
    assert "ghost.jpg" in body["error"]
    # The existing one was still deleted.
    assert not (Path(training_app.PATHS["uploads"]) / img_a).exists()


@pytest.mark.integration
def test_delete_images_rejects_path_traversal_without_500(isolated_app, tmp_path):
    # A traversal attempt must NOT delete anything outside uploads and must
    # return an error response (400), never a 500.
    outside = tmp_path / "secret.txt"
    outside.write_text("do-not-delete", encoding="utf-8")
    client = isolated_app.test_client()

    response = client.post(
        "/api/images/delete",
        json={"images": ["../secret.txt", "../../../etc/passwd"]},
    )

    assert response.status_code in {400, 403, 404}
    # No system/outside file touched.
    assert outside.read_text(encoding="utf-8") == "do-not-delete"
    body = response.get_json()
    assert body is not None
    # The response carries an error message (not a bare 500 traceback).
    assert "error" in body or "success" in body


@pytest.mark.integration
def test_delete_images_rejects_traversal_with_valid_image_extension(isolated_app, tmp_path):
    # The .txt traversal test above passes partly because .txt is rejected by
    # the extension allowlist BEFORE commonpath runs. This case uses a VALID
    # image extension (.png) so the ONLY thing that can block the traversal is
    # resolve_child_path's commonpath containment check. Locks H2 path safety.
    uploads_dir = Path(training_app.PATHS["uploads"])
    uploads_dir.mkdir(parents=True, exist_ok=True)
    # Place secret.png in uploads' PARENT (tmp_path), i.e. outside uploads.
    # "../secret.png" from uploads resolves to tmp_path/secret.png.
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"\x89PNG\r\n\x1a\nfake-png-do-not-delete")
    assert outside.parent == uploads_dir.parent  # sanity: outside is not under uploads
    client = isolated_app.test_client()

    response = client.post(
        "/api/images/delete",
        json={"images": ["../secret.png"]},
    )

    assert response.status_code in {400, 403, 404}
    body = response.get_json()
    assert body is not None
    assert "error" in body
    # CRITICAL: the outside file must survive - commonpath blocked the escape,
    # not the extension allowlist (extension .png is valid).
    assert outside.exists(), "traversal with valid .png extension deleted a file outside uploads"
    assert outside.read_bytes() == b"\x89PNG\r\n\x1a\nfake-png-do-not-delete"


@pytest.mark.integration
def test_delete_images_empty_list_is_a_noop(isolated_app):
    client = isolated_app.test_client()

    response = client.post("/api/images/delete", json={"images": []})

    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert body["deleted_count"] == 0
