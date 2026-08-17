"""Annotation domain service.

Core class/image/annotation CRUD plus shared helpers consumed by the
annotation blueprint and its sibling service modules
(``annotation_export_service``, ``annotation_inference_service``,
``annotation_sam3_service``, ``annotation_import_service``).

Closed-world contract (.omc/plans/api-freeze.md section 2): cross-domain
callers (e.g. training_service) use ``read_annotations()`` / ``read_classes()``
here, never ``app.repositories.annotation_repo`` directly.
"""
import hashlib
import logging
import os
import shutil
import time

import filelock
from PIL import Image

from app.common.config import PATHS
from app.common.path_safety import PathSafetyError, resolve_child_path
from app.common.utils import color_for_index
from app.repositories.annotation_repo import (
    read_annotations as _read_annotations_repo,
    read_classes as _read_classes_repo,
    update_annotations as _update_annotations_repo,
    update_classes as _update_classes_repo,
    write_annotations as _write_annotations_repo,
    write_classes as _write_classes_repo,
)

logger = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.bmp')


class AnnotationError(Exception):
    """Handled error from an annotation service.

    The blueprint handler maps this to ``jsonify({"error": message, **body}, status)``.
    Carrying the status (and optional extra ``body`` fields) on the exception
    keeps the service Flask-context-free while preserving each route's exact
    HTTP status + body shape (e.g. the ``output`` field on parse failures).
    """

    def __init__(self, status, message, body=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.body = body or {}


# ---------------------------------------------------------------------------
# Public read/write API (cross-domain entry points)
# ---------------------------------------------------------------------------

def read_annotations() -> dict:
    """Public read API: all annotations keyed by image_name."""
    return _read_annotations_repo()


def read_classes() -> list:
    """Public read API: configured object classes."""
    return _read_classes_repo()


def write_classes(data: list) -> None:
    _write_classes_repo(data)


def write_annotations(data: dict) -> None:
    _write_annotations_repo(data)


def update_annotations(mutator, *, timeout=10):
    """Atomic RMW under the cross-process filelock (delegates to repo)."""
    return _update_annotations_repo(mutator, timeout=timeout)


def update_classes(mutator, *, timeout=10):
    """Atomic RMW for classes.json under the cross-process filelock (delegates to repo)."""
    return _update_classes_repo(mutator, timeout=timeout)


# ---------------------------------------------------------------------------
# Class management helpers (existing, preserved)
# ---------------------------------------------------------------------------

def sync_object_classes_to_labels(object_classes, replace=False):
    """Sync object classes into classes.json atomically (H8: was a bare
    ``read_classes`` -> mutate -> ``write_classes`` that raced concurrent
    class additions). Returns the resulting classes list."""
    def _mutate(current):
        classes = [] if replace else current
        existing = {c.get('name') for c in classes}
        for index, obj in enumerate(object_classes):
            name = obj.get('id') or obj.get('name') if isinstance(obj, dict) else str(obj)
            display_name = obj.get('name') if isinstance(obj, dict) else name
            if name and name not in existing:
                classes.append({'name': name, 'display_name': display_name, 'color': color_for_index(len(classes))})
                existing.add(name)
        return classes, classes
    return update_classes(_mutate)


def parse_target_classes(raw_value):
    """Parse target classes for SAM3 from list/string; fallback to local class config."""
    parsed = []
    if isinstance(raw_value, list):
        for item in raw_value:
            name = str(item or '').strip()
            if name and name not in parsed:
                parsed.append(name)
    elif isinstance(raw_value, str):
        normalized = raw_value.replace('\n', ',').replace(';', ',').replace('，', ',')
        for item in normalized.split(','):
            name = item.strip()
            if name and name not in parsed:
                parsed.append(name)

    if parsed:
        return parsed

    local_classes = read_classes()
    fallback = []
    for cls in local_classes:
        name = str(cls.get('name') or '').strip()
        if name and name not in fallback:
            fallback.append(name)
    return fallback


def assign_class_colors_and_ids(annotations, existing_classes, id_suffix=""):
    """Mutate ``annotations`` in place: set ``color`` + ``id``, create missing classes.

    Shared by the YOLO and SAM3 inference paths. ``existing_classes`` is mutated
    when new classes are discovered; the caller persists it when this returns
    True. ``id_suffix`` (image_name) is appended to the id hash only for the
    SAM3 batch path, matching the original per-route id formulas exactly.

    Returns ``new_classes_added`` (bool).
    """
    new_classes_added = False
    for ann in annotations:
        cls_name = ann.get('class')
        if not cls_name:
            continue
        color = None
        for cls in existing_classes:
            if cls.get('name') == cls_name:
                color = cls.get('color')
                break
        if color is None:
            color = '#{:06x}'.format(int(hashlib.sha1(cls_name.encode()).hexdigest(), 16) % 0x1000000)
            existing_classes.append({'name': cls_name, 'color': color})
            new_classes_added = True
        ann['color'] = color
        id_key = f"{cls_name}_{ann['points'][0][0]}_{ann['points'][0][1]}"
        if id_suffix:
            id_key = f"{id_key}_{id_suffix}"
        ann['id'] = int(hashlib.sha1(id_key.encode()).hexdigest(), 16) % 1000000000
    return new_classes_added


# ---------------------------------------------------------------------------
# Image / annotation CRUD (extracted from annotation blueprint handlers)
# ---------------------------------------------------------------------------

def list_images():
    """Return ``{"images": [{name, width, height, annotation_count}]}``.

    Lists image files in uploads with dimensions and per-image annotation
    count (derived from annotations.json). Non-image files are skipped.
    """
    annotations = read_annotations()
    images = []
    for filename in os.listdir(PATHS['uploads']):
        if not filename.lower().endswith(_IMAGE_EXTENSIONS):
            continue
        try:
            with Image.open(os.path.join(PATHS['uploads'], filename)) as img:
                width, height = img.size
        except Exception:
            width, height = 0, 0
        images.append({
            'name': filename,
            'width': width,
            'height': height,
            'annotation_count': len(annotations.get(filename, [])),
        })
    return {'images': images}


def delete_images(image_names):
    """Delete images + their annotations. Returns ``(deleted_count, errors)``.

    File deletion uses ``resolve_child_path`` for uploads containment (a
    traversal attempt appends an error instead of 500). H3: file deletion
    (slow disk I/O) happens OUTSIDE the annotations lock - previously it ran
    inside the ``update_annotations`` mutator, holding the cross-process lock
    for the whole bulk-delete duration and blocking concurrent saves (10s
    timeout -> 503). Only the quick annotation-key removal is now done under
    the lock. The observable return shape is unchanged.
    """
    deleted_count = 0
    errors = []
    removed_names = []

    # File deletion: lock-free (image files have no lock relationship).
    for image_name in image_names:
        try:
            image_path = resolve_child_path(
                PATHS['uploads'], image_name, extensions=_IMAGE_EXTENSIONS,
            )
        except PathSafetyError as e:
            errors.append(f"删除图片 '{image_name}' 失败: {str(e)}")
            continue
        try:
            if os.path.exists(image_path):
                os.remove(image_path)
                deleted_count += 1
                removed_names.append(image_name)
            else:
                errors.append(f"图片 '{image_name}' 不存在")
        except Exception as e:
            errors.append(f"删除图片 '{image_name}' 失败: {str(e)}")

    # Short critical section: drop annotation keys for the deleted images.
    if removed_names:
        def _mutate(annotations):
            changed = False
            for image_name in removed_names:
                if image_name in annotations:
                    del annotations[image_name]
                    changed = True
            return (annotations if changed else None, None)
        update_annotations(_mutate)

    return deleted_count, errors


def save_image_annotations(image_name, data):
    """Persist ``data`` as the annotations for ``image_name`` via atomic RMW.

    Uses ``update_annotations(mutator)`` so the read-backup-write happens under
    one cross-process filelock acquisition (the original hand-rolled lock +
    read + backup + atomic-write raced with concurrent batch inference writes;
    json_store.write_json_file is already atomic: tmp+fsync+replace, so the
    separate verify-by-reread step was redundant).

    Returns a ``metrics`` dict (all int ms values, stable keys locked by
    ``test_char_annotation_flow.test_post_annotations_response_shape_and_metrics_headers``):
      ``lock_wait_ms, read_json_ms, backup_ms, write_verify_replace_ms, total_ms``.

    Raises ``AnnotationError(500)`` on read/write failure, ``AnnotationError(503)``
    on lock timeout - preserving the original route's exact error bodies.
    """
    req_started = time.perf_counter()
    metrics = dict.fromkeys(
        ('lock_wait_ms', 'read_json_ms', 'backup_ms', 'write_verify_replace_ms', 'total_ms'), 0)

    # ponytail: metrics filled inside mutator via closure; read/backup happen
    # under the lock so the backup reflects the exact pre-image state.
    def _mutate(current):
        read_started = time.perf_counter()
        # current already read by update_annotations under the lock; emulate
        # read_json_ms as the time spent touching the in-memory dict here.
        metrics['read_json_ms'] = int((time.perf_counter() - read_started) * 1000)

        if os.path.exists(PATHS['annotations']):
            try:
                backup_started = time.perf_counter()
                shutil.copy2(PATHS['annotations'], PATHS['annotations'] + '.bak')
                metrics['backup_ms'] = int((time.perf_counter() - backup_started) * 1000)
            except Exception as e:
                logger.warning('[annotations.save.backup_failed] image=%s error=%s', image_name, e)

        current[image_name] = data
        return current, None

    lock_wait_started = time.perf_counter()
    try:
        _update_annotations_repo(_mutate, timeout=10)
    except filelock.Timeout:
        metrics['lock_wait_ms'] = int((time.perf_counter() - lock_wait_started) * 1000)
        metrics['total_ms'] = int((time.perf_counter() - req_started) * 1000)
        logger.warning(
            '[annotations.save.timeout] image=%s waited_ms=%d total_ms=%d',
            image_name,
            metrics['lock_wait_ms'],
            metrics['total_ms'],
        )
        raise AnnotationError(503, '文件正在被其他操作使用，请稍后重试')
    except (OSError, ValueError) as e:
        metrics['total_ms'] = int((time.perf_counter() - req_started) * 1000)
        logger.error('[annotations.save.failed] image=%s error=%s', image_name, e)
        raise AnnotationError(500, f'保存失败: {str(e)}')

    metrics['lock_wait_ms'] = int((time.perf_counter() - lock_wait_started) * 1000)
    # write happens inside update_annotations -> write_json_file (atomic); no
    # separate verify-by-reread step, so this stays 0 (key preserved for the
    # locked response-shape contract).
    metrics['total_ms'] = int((time.perf_counter() - req_started) * 1000)

    logger.info(
        '[annotations.save] image=%s lock_wait_ms=%d read_json_ms=%d backup_ms=%d '
        'write_verify_replace_ms=%d total_ms=%d payload_len=%d',
        image_name,
        metrics['lock_wait_ms'],
        metrics['read_json_ms'],
        metrics['backup_ms'],
        metrics['write_verify_replace_ms'],
        metrics['total_ms'],
        len(data) if isinstance(data, list) else -1,
    )
    return metrics
