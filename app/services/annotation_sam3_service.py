"""SAM3 open-vocabulary auto-annotation service.

Single-image and batch auto-annotation via the SAM3 plugin (text prompts).
Extracted from the ``ai_annotate_sam3`` / ``ai_annotate_sam3_batch`` route
handlers. No characterization test covers these routes; logic is moved
verbatim (class color/id assignment is shared via annotation_service).
"""
import filelock
import os

from plugins.sam3_service import sam3_service

from app.common.config import PATHS
from app.repositories.annotation_repo import update_annotations
from app.services.annotation_service import (
    AnnotationError,
    assign_class_colors_and_ids,
    parse_target_classes,
    update_classes,
)

_MISSING_TARGET_CLASSES = '请至少配置一个目标类别（例如 base,frame,mirror,screw）'


def run_sam3_single(data):
    """Single-image SAM3 auto-annotate. Returns the success body dict."""
    image_name = data.get('image_name', '')
    confidence = float(data.get('confidence', 0.5))
    target_classes = parse_target_classes(data.get('target_classes') or data.get('world_classes'))

    if not image_name:
        raise AnnotationError(400, '未指定图片')
    if not target_classes:
        raise AnnotationError(400, _MISSING_TARGET_CLASSES)
    _require_sam3_loaded()

    image_path = os.path.abspath(os.path.join(PATHS['uploads'], image_name))
    if not os.path.exists(image_path):
        raise AnnotationError(400, f'图片不存在: {image_name}')

    annotations = sam3_service.detect_from_file(image_path, text=target_classes, conf=confidence)
    # ponytail: classes RMW under one lock (H5/H10); assign mutates the
    # locked-in current list so a concurrent run cannot lose a class.
    new_classes_added = _merge_classes_for(annotations, id_suffix="")

    return {
        'success': True,
        'annotations': annotations,
        'new_classes_added': new_classes_added,
        'engine': 'sam3',
        'target_classes': target_classes,
    }


def run_sam3_batch(data):
    """Batch SAM3 auto-annotate. Returns the success body dict."""
    image_names = data.get('image_names', [])
    confidence = float(data.get('confidence', 0.5))
    target_classes = parse_target_classes(data.get('target_classes') or data.get('world_classes'))

    if not image_names:
        raise AnnotationError(400, '未指定图片')
    if not target_classes:
        raise AnnotationError(400, _MISSING_TARGET_CLASSES)
    _require_sam3_loaded()

    image_paths, valid_image_names = _collect_valid_image_paths(image_names)
    if not image_paths:
        raise AnnotationError(400, '没有有效的图片')

    all_results = sam3_service.detect_batch_from_files(image_paths, text=target_classes, conf=confidence)
    return _build_sam3_batch_response(all_results, image_paths, valid_image_names, target_classes)


def _build_sam3_batch_response(all_results, image_paths, valid_image_names, target_classes):
    """Assign colors/ids, persist, build the batch response.

    Classes RMW (read-assign-write) is atomic under ``update_classes`` (H5/H10);
    annotations RMW under ``update_annotations`` (H6). Existing AUTO
    annotations for an image are replaced while manual ones are preserved,
    matching the YOLO batch strategy in
    ``annotation_inference_service._merge_batch_results`` so a re-run is
    idempotent and manual annotations are never clobbered.

    ``new_classes_added`` is an int count of distinct new classes discovered
    (matching the original route). Computed via the length delta around
    ``assign_class_colors_and_ids``, which appends each distinct new class
    exactly once.
    """
    # Pre-collect per-image annotations (no store access yet).
    per_image = []
    for i, image_name in enumerate(valid_image_names):
        image_path = image_paths[i]
        image_annotations = all_results.get(image_path, [])
        per_image.append((image_name, image_annotations))

    # Assign colors/ids + merge new classes under the classes lock (H5/H10).
    # id_suffix per image, matching the original id formula.
    new_class_count = _merge_classes_for_batch(per_image)

    total_detected = 0

    def _mutate(current):
        nonlocal total_detected
        for image_name, image_annotations in per_image:
            if not image_annotations:
                continue
            existing = current.get(image_name, [])
            # Preserve manual annotations; replace prior AUTO detections
            # (aligned with YOLO _merge_batch_results).
            current[image_name] = [a for a in existing if not a.get('auto')] + image_annotations
            total_detected += len(image_annotations)
        return current, None

    try:
        update_annotations(_mutate, timeout=10)
    except filelock.Timeout:
        raise AnnotationError(503, '文件正在被其他操作使用，请稍后重试')

    response_results = [
        {
            'image_name': image_name,
            'success': True,
            'count': len(image_annotations),
            'annotations': image_annotations,
        }
        for image_name, image_annotations in per_image
    ]
    return {
        'success': True,
        'results': response_results,
        'total_processed': len(valid_image_names),
        'total_detected': total_detected,
        'new_classes_added': new_class_count,
        'engine': 'sam3',
        'target_classes': target_classes,
    }


def _merge_classes_for(annotations, id_suffix=""):
    """Assign colors/ids + merge new classes atomically under the classes lock.

    Returns ``new_classes_added`` (bool). Mirrors
    ``annotation_inference_service._merge_classes_for``.
    """
    def _mutate(current_classes):
        added = assign_class_colors_and_ids(annotations, current_classes, id_suffix=id_suffix)
        return (current_classes if added else None, added)

    try:
        return update_classes(_mutate, timeout=10)
    except filelock.Timeout:
        raise AnnotationError(503, '文件正在被其他操作使用，请稍后重试')


def _merge_classes_for_batch(per_image):
    """Assign colors/ids (per-image id_suffix) + merge new classes under lock.

    Returns the int count of distinct new classes added across all images.
    """
    def _mutate(current_classes):
        added_count = 0
        for image_name, image_annotations in per_image:
            before = len(current_classes)
            assign_class_colors_and_ids(image_annotations, current_classes, id_suffix=image_name)
            added_count += len(current_classes) - before
        return (current_classes if added_count else None, added_count)

    try:
        return update_classes(_mutate, timeout=10)
    except filelock.Timeout:
        raise AnnotationError(503, '文件正在被其他操作使用，请稍后重试')


def _require_sam3_loaded():
    if not sam3_service.is_loaded:
        raise AnnotationError(503, 'SAM3模型未加载，请检查模型文件')


def _collect_valid_image_paths(image_names):
    """Filter image_names to those existing in uploads. Returns (paths, names)."""
    image_paths = []
    valid_image_names = []
    for img_name in image_names:
        img_path = os.path.abspath(os.path.join(PATHS['uploads'], img_name))
        if os.path.exists(img_path):
            image_paths.append(img_path)
            valid_image_names.append(img_name)
    return image_paths, valid_image_names
