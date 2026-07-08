"""SAM3 open-vocabulary auto-annotation service.

Single-image and batch auto-annotation via the SAM3 plugin (text prompts).
Extracted from the ``ai_annotate_sam3`` / ``ai_annotate_sam3_batch`` route
handlers. No characterization test covers these routes; logic is moved
verbatim (class color/id assignment is shared via annotation_service).
"""
import os

from plugins.sam3_service import sam3_service

from app.common.config import PATHS
from app.services.annotation_service import (
    AnnotationError,
    assign_class_colors_and_ids,
    parse_target_classes,
    read_annotations,
    read_classes,
    write_annotations,
    write_classes,
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
    existing_classes = read_classes()
    new_classes_added = bool(assign_class_colors_and_ids(annotations, existing_classes))
    if new_classes_added:
        write_classes(existing_classes)

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

    ``new_classes_added`` is an int count of distinct new classes discovered
    (matching the original route). Computed via the ``existing_classes``
    length delta around ``assign_class_colors_and_ids``, which appends each
    distinct new class exactly once - equivalent to the original per-class
    increment.
    """
    existing_classes = read_classes()
    annotations = read_annotations()
    response_results = []
    total_detected = 0
    new_class_count = 0

    for i, image_name in enumerate(valid_image_names):
        image_path = image_paths[i]
        image_annotations = all_results.get(image_path, [])
        before = len(existing_classes)
        assign_class_colors_and_ids(image_annotations, existing_classes, id_suffix=image_name)
        new_class_count += len(existing_classes) - before

        if image_annotations:
            annotations[image_name] = image_annotations
            total_detected += len(image_annotations)

        response_results.append({
            'image_name': image_name,
            'success': True,
            'count': len(image_annotations),
            'annotations': image_annotations,
        })

    write_annotations(annotations)
    write_classes(existing_classes)
    return {
        'success': True,
        'results': response_results,
        'total_processed': len(valid_image_names),
        'total_detected': total_detected,
        'new_classes_added': new_class_count,
        'engine': 'sam3',
        'target_classes': target_classes,
    }


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
