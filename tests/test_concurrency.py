"""H18: concurrent annotation writes do not corrupt or lose data.

Validates the filelock + ``update_annotations`` atomic RMW (Worker A/B1) under
real thread concurrency. N threads POST annotations concurrently - some to the
SAME image_name (last-writer-wins for that key, no interleave), some to DISTINCT
images (all must persist). After all threads finish, annotations.json must:

- be valid JSON (not torn / interleated bytes),
- contain every distinct image's payload intact,
- for the contended image, hold exactly one of the posted payloads (not a
  partial merge).

Each POST returns the ``metrics`` dict; ``lock_wait_ms`` must be present
(locking actually ran).
"""
import json
import sys
import threading
from collections import Counter
from pathlib import Path

import pytest
from PIL import Image

import app as training_app  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _seed_image(name: str) -> None:
    Path(training_app.PATHS["uploads"]).mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 40), color=(10, 20, 30)).save(
        Path(training_app.PATHS["uploads"]) / name
    )


def _payload_for(tag: str):
    return [{"class": "part", "points": [{"x": 1, "y": tag}, {"x": 2, "y": tag}]}]


@pytest.mark.integration
def test_concurrent_writes_to_distinct_images_all_persist_intact(isolated_app):
    """8 threads each POST to a DIFFERENT image - all 8 must land intact."""
    client = isolated_app.test_client()
    n = 8
    for i in range(n):
        _seed_image(f"img_{i}.jpg")

    errors = []

    def writer(i):
        try:
            resp = client.post(f"/api/annotations/img_{i}.jpg", json=_payload_for(i))
            if resp.status_code != 200:
                errors.append((i, resp.status_code, resp.get_data(as_text=True)))
            else:
                # lock_wait_ms metric must be present on every concurrent save.
                metrics = resp.get_json()["metrics"]
                assert "lock_wait_ms" in metrics
                assert isinstance(metrics["lock_wait_ms"], int)
        except Exception as exc:  # noqa: BLE001
            errors.append((i, "exc", str(exc)))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"concurrent writes failed: {errors}"

    # File must be valid JSON (no torn write).
    persisted = json.loads(Path(training_app.PATHS["annotations"]).read_text(encoding="utf-8"))
    assert isinstance(persisted, dict)
    # Every distinct image's payload persisted intact.
    for i in range(n):
        assert persisted[f"img_{i}.jpg"] == _payload_for(i), (
            f"img_{i}.jpg lost/corrupted under concurrency"
        )
    assert len(persisted) == n


@pytest.mark.integration
def test_concurrent_writes_to_same_image_no_interleave_or_corruption(isolated_app):
    """8 threads POST to the SAME image - exactly ONE payload wins, intact.

    Without the lock, concurrent read-modify-write of the shared annotations
    dict would interleave and could produce a torn file or a merged/partial
    payload. With the filelock, the final value is exactly one thread's posted
    list (last writer), never a splice of two.
    """
    client = isolated_app.test_client()
    _seed_image("shared.jpg")
    n = 8
    posted_payloads = [_payload_for(tag) for tag in range(n)]

    errors = []

    def writer(tag):
        try:
            resp = client.post("/api/annotations/shared.jpg", json=posted_payloads[tag])
            if resp.status_code != 200:
                errors.append((tag, resp.status_code))
        except Exception as exc:  # noqa: BLE001
            errors.append((tag, "exc", str(exc)))

    threads = [threading.Thread(target=writer, args=(tag,)) for tag in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"concurrent same-image writes failed: {errors}"

    persisted = json.loads(
        Path(training_app.PATHS["annotations"]).read_text(encoding="utf-8")
    )
    # The contended key holds exactly one payload (not a partial merge of two).
    final = persisted["shared.jpg"]
    assert final in posted_payloads, (
        f"final payload is not one of the posted payloads (interleave?): {final!r}"
    )
    assert isinstance(final, list)
    assert len(final) == 1, "expected exactly one annotation, got a splice"


@pytest.mark.integration
def test_concurrent_mixed_same_and_distinct_images(isolated_app):
    """4 threads hit 2 shared images + 4 distinct images simultaneously.

    A mixed contention pattern: the shared images each get a single intact
    winner; the distinct images all persist. No corruption, no loss.
    """
    client = isolated_app.test_client()
    for name in ("shared_a.jpg", "shared_b.jpg",
                 "distinct_0.jpg", "distinct_1.jpg", "distinct_2.jpg", "distinct_3.jpg"):
        _seed_image(name)

    # 2 writers per shared image + 1 per distinct = 8 threads
    targets = ["shared_a.jpg", "shared_a.jpg",
               "shared_b.jpg", "shared_b.jpg",
               "distinct_0.jpg", "distinct_1.jpg", "distinct_2.jpg", "distinct_3.jpg"]
    payloads = {t: _payload_for(i) for i, t in enumerate(targets)}
    errors = []

    def writer(target):
        try:
            resp = client.post(f"/api/annotations/{target}", json=payloads[target])
            if resp.status_code != 200:
                errors.append((target, resp.status_code))
        except Exception as exc:  # noqa: BLE001
            errors.append((target, "exc", str(exc)))

    threads = [threading.Thread(target=writer, args=(t,)) for t in targets]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"mixed concurrent writes failed: {errors}"

    persisted = json.loads(
        Path(training_app.PATHS["annotations"]).read_text(encoding="utf-8")
    )
    # All 6 distinct image keys present.
    expected_keys = {"shared_a.jpg", "shared_b.jpg",
                     "distinct_0.jpg", "distinct_1.jpg", "distinct_2.jpg", "distinct_3.jpg"}
    assert set(persisted.keys()) == expected_keys
    # Shared keys: one of the two posted payloads (not a splice). Reference the
    # original payloads by enumerate() index, NOT the ``payloads`` dict - that
    # dict is keyed by target name, and shared_a.jpg appears at both index 0
    # and 1, so the later write (index 1) clobbers the earlier (index 0) in the
    # dict. Using payloads["shared_a.jpg"] would silently collapse the check to
    # a single value and miss the index-0 winner. targets order:
    #   0,1 -> shared_a.jpg ; 2,3 -> shared_b.jpg.
    assert persisted["shared_a.jpg"] in [_payload_for(0), _payload_for(1)]
    assert persisted["shared_b.jpg"] in [_payload_for(2), _payload_for(3)]
    # Distinct keys: exact match.
    for d in ("distinct_0.jpg", "distinct_1.jpg", "distinct_2.jpg", "distinct_3.jpg"):
        assert persisted[d] == payloads[d]


@pytest.mark.integration
def test_concurrent_writes_do_not_clobber_existing_other_images(isolated_app):
    """Pre-existing annotations for OTHER images survive a burst of concurrent
    writes to unrelated images (the lock prevents read-modify-write races from
    dropping keys)."""
    client = isolated_app.test_client()
    _seed_image("preexisting.jpg")
    preexisting = [{"class": "part", "points": [{"x": 99, "y": 99}]}]
    Path(training_app.PATHS["annotations"]).write_text(
        json.dumps({"preexisting.jpg": preexisting}), encoding="utf-8"
    )
    for i in range(6):
        _seed_image(f"burst_{i}.jpg")

    errors = []

    def writer(i):
        try:
            resp = client.post(f"/api/annotations/burst_{i}.jpg", json=_payload_for(i))
            if resp.status_code != 200:
                errors.append((i, resp.status_code))
        except Exception as exc:  # noqa: BLE001
            errors.append((i, "exc", str(exc)))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], errors

    persisted = json.loads(
        Path(training_app.PATHS["annotations"]).read_text(encoding="utf-8")
    )
    # The pre-existing key is intact (not clobbered by the concurrent burst).
    assert persisted["preexisting.jpg"] == preexisting
    # All burst keys present.
    assert len(persisted) == 7
