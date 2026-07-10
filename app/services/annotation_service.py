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
import json
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


# ---------------------------------------------------------------------------
# Class management helpers (existing, preserved)
# ---------------------------------------------------------------------------

def sync_object_classes_to_labels(object_classes, replace=False):
    classes = [] if replace else read_classes()
    existing = {c.get('name') for c in classes}
    for index, obj in enumerate(object_classes):
        name = obj.get('id') or obj.get('name') if isinstance(obj, dict) else str(obj)
        display_name = obj.get('name') if isinstance(obj, dict) else name
        if name and name not in existing:
            classes.append({'name': name, 'display_name': display_name, 'color': color_for_index(len(classes))})
            existing.add(name)
    write_classes(classes)
    return classes


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
    traversal attempt appends an error instead of 500). The annotations
    update is an atomic RMW under the cross-process filelock via
    ``update_annotations`` (the original read->modify->write raced with
    concurrent saves); the observable return shape is unchanged.
    """
    deleted_count = 0
    errors = []

    def _mutate(annotations):
        nonlocal deleted_count
        local_changed = False
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
                    if image_name in annotations:
                        del annotations[image_name]
                        local_changed = True
                else:
                    errors.append(f"图片 '{image_name}' 不存在")
            except Exception as e:
                errors.append(f"删除图片 '{image_name}' 失败: {str(e)}")
        return (annotations if local_changed else None, None)

    update_annotations(_mutate)

    return deleted_count, errors


def save_image_annotations(image_name, data):
    """Persist ``data`` as the annotations for ``image_name`` under a file lock.

    Returns a ``metrics`` dict (all int ms values):
      ``lock_wait_ms, read_json_ms, backup_ms, write_verify_replace_ms, total_ms``.

    Raises ``AnnotationError(500)`` on read/write failure, ``AnnotationError(503)``
    on lock timeout - preserving the original route's exact error bodies.
    """
    req_started = time.perf_counter()
    metrics = dict.fromkeys(
        ('lock_wait_ms', 'read_json_ms', 'backup_ms', 'write_verify_replace_ms', 'total_ms'), 0)
    lock_file = PATHS['annotations'] + '.lock'
    lock = filelock.FileLock(lock_file, timeout=10)

    try:
        lock_wait_started = time.perf_counter()
        with lock:
            metrics['lock_wait_ms'] = int((time.perf_counter() - lock_wait_started) * 1000)
            annotations = _read_existing_annotations_or_raise(metrics)
            _backup_existing_annotations(metrics)
            annotations[image_name] = data
            _atomic_write_verified(image_name, annotations, metrics)
            metrics['total_ms'] = int((time.perf_counter() - req_started) * 1000)
    except filelock.Timeout:
        metrics['total_ms'] = int((time.perf_counter() - req_started) * 1000)
        logger.warning(
            '[annotations.save.timeout] image=%s waited_ms=%d total_ms=%d',
            image_name,
            int((time.perf_counter() - lock_wait_started) * 1000),
            metrics['total_ms'],
        )
        raise AnnotationError(503, '文件正在被其他操作使用，请稍后重试')

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


def _read_existing_annotations_or_raise(metrics):
    """Read annotations.json (utf-8). Raises AnnotationError(500) on failure."""
    annotations = {}
    if os.path.exists(PATHS['annotations']):
        try:
            read_started = time.perf_counter()
            with open(PATHS['annotations'], 'r', encoding='utf-8') as f:
                content = f.read()
                if content.strip():
                    annotations = json.loads(content)
            metrics['read_json_ms'] = int((time.perf_counter() - read_started) * 1000)
        except json.JSONDecodeError as e:
            print(f"JSON解析失败: {e}")
            raise AnnotationError(500, f'标注文件格式错误，无法保存: {str(e)}')
        except Exception as e:
            print(f"读取标注文件失败: {e}")
            raise AnnotationError(500, f'读取标注文件失败: {str(e)}')
    return annotations


def _backup_existing_annotations(metrics):
    """Back up annotations.json to .bak (best-effort, preserves last good copy)."""
    if os.path.exists(PATHS['annotations']):
        backup_file = PATHS['annotations'] + '.bak'
        try:
            backup_started = time.perf_counter()
            shutil.copy2(PATHS['annotations'], backup_file)
            metrics['backup_ms'] = int((time.perf_counter() - backup_started) * 1000)
        except Exception as e:
            print(f"备份失败: {e}")


def _atomic_write_verified(image_name, annotations, metrics):
    """Write annotations to a temp file, verify by re-reading, then replace.

    Raises AnnotationError(500) on write/verify failure (temp file cleaned up).
    """
    temp_file = PATHS['annotations'] + '.tmp'
    try:
        write_started = time.perf_counter()
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(annotations, f, indent=2, ensure_ascii=False)
        with open(temp_file, 'r', encoding='utf-8') as f:
            json.load(f)  # verify JSON is valid
        if os.path.exists(PATHS['annotations']):
            os.replace(temp_file, PATHS['annotations'])
        else:
            os.rename(temp_file, PATHS['annotations'])
        metrics['write_verify_replace_ms'] = int((time.perf_counter() - write_started) * 1000)
    except Exception as e:
        if os.path.exists(temp_file):
            os.remove(temp_file)
        print(f"写入标注文件失败: {e}")
        raise AnnotationError(500, f'保存失败: {str(e)}')
