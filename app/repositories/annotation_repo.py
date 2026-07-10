"""Annotations + classes JSON stores.

All annotation writes go through the SAME cross-process ``filelock``
(``annotations.json.lock``) that ``annotation_service.save_image_annotations``
acquires, so concurrent writers (single-image save vs. batch inference vs.
delete) can no longer clobber each other's read-modify-write cycles.

Callers that read-then-modify-then-write MUST use :func:`update_annotations`
so the read and write happen under one lock acquisition; a bare
``read_annotations()`` + ``write_annotations()`` pair is NOT atomic.
"""
import filelock

from app.common.config import PATHS
from app.common.json_store import read_json_file, write_json_file

_ANNOTATIONS_LOCK_PATH = PATHS['annotations'] + '.lock'


def read_annotations() -> dict:
    return read_json_file(PATHS['annotations'], {})


def write_annotations(data: dict) -> None:
    """Overwrite annotations.json under the cross-process lock.

    Prefer :func:`update_annotations` for read-modify-write flows; this helper
    only guards the write itself (callers that read first still race).
    """
    lock = filelock.FileLock(_ANNOTATIONS_LOCK_PATH, timeout=10)
    with lock:
        write_json_file(PATHS['annotations'], data)


def update_annotations(mutator, *, timeout=10):
    """Atomically read-modify-write annotations.json under the file lock.

    ``mutator(current: dict) -> (new_data, result)``: receives the current
    annotations dict and returns ``(new_data, result)`` where ``new_data`` is
    the dict to persist (or ``None`` to skip the write, e.g. read-only check)
    and ``result`` is any value returned to the caller (counts, errors, ...).

    Raises ``filelock.Timeout`` if the lock cannot be acquired within
    ``timeout`` seconds - callers map this to HTTP 503.
    """
    lock = filelock.FileLock(_ANNOTATIONS_LOCK_PATH, timeout=timeout)
    with lock:
        current = read_json_file(PATHS['annotations'], {})
        new_data, result = mutator(current)
        if new_data is not None:
            write_json_file(PATHS['annotations'], new_data)
        return result


def read_classes() -> list:
    return read_json_file(PATHS['classes'], [])


def write_classes(data: list) -> None:
    write_json_file(PATHS['classes'], data)
