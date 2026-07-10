"""Annotation dataset export service.

Builds a YOLO-format dataset zip from uploads + annotations. Extracted
verbatim from the ``export_dataset`` route handler; the HTTP-adapter
(send_from_directory / error jsonify) stays in the blueprint.

Behavior is locked by tests/test_char_annotation_flow.py (zip layout,
data.yaml, YOLO label line format, sample_selection filtering, 400 error
shape for unsupported types) - do not change observable output without
synchronizing the freeze.
"""
import datetime
import os
import re
import tempfile
import zipfile
from shutil import copyfile

import numpy as np
from PIL import Image

from app.common.config import PATHS
from app.services.annotation_service import AnnotationError, read_annotations, read_classes

_EXPORT_IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.bmp')


def export_yolo_dataset(params):
    """Build a YOLO dataset zip. Returns ``{"temp_dir", "zip_filename"}``.

    Raises ``AnnotationError(400, "不支持的导出数据类型")`` for unsupported
    export_data_type; other errors propagate to the handler (500).
    """
    ratios, selected_classes, sample_selection, export_data_type, export_prefix = \
        _parse_export_params(params)
    if export_data_type not in ['yolo']:
        raise AnnotationError(400, '不支持的导出数据类型')

    annotations = read_annotations()
    images = _list_export_images(annotations, sample_selection)
    np.random.shuffle(images)

    train_images, val_images, test_images = _compute_splits(images, ratios)
    splits = [('train', train_images), ('val', val_images), ('test', test_images)]

    temp_dir = tempfile.mkdtemp()
    base_name = f"datasets_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
    yolo_base = os.path.join(temp_dir, base_name)
    for split in ('train', 'val', 'test'):
        os.makedirs(os.path.join(yolo_base, split, 'images'), exist_ok=True)
        os.makedirs(os.path.join(yolo_base, split, 'labels'), exist_ok=True)

    _write_data_yaml(yolo_base, selected_classes)
    _populate_splits(yolo_base, splits, annotations, selected_classes, sample_selection, export_prefix)

    zip_filename = f"{base_name}.zip"
    _zip_directory(yolo_base, os.path.join(temp_dir, zip_filename))
    return {"temp_dir": temp_dir, "zip_filename": zip_filename}


def _parse_export_params(params):
    """Extract + default the export ratios/options (None -> default)."""
    def _ratio(key, default):
        value = params.get(key)
        return float(value) if value is not None else default

    raw_prefix = params.get('export_prefix', '') or ''
    export_prefix = re.sub(r'[^A-Za-z0-9_-]', '_', raw_prefix)

    return (
        (_ratio('train_ratio', 0.7), _ratio('val_ratio', 0.2), _ratio('test_ratio', 0.1)),
        params.get('selected_classes', []),
        params.get('sample_selection', 'all'),
        params.get('export_data_type', 'yolo'),
        export_prefix,
    )


def _list_export_images(annotations, sample_selection):
    """List upload image filenames filtered by sample_selection."""
    images = [
        filename for filename in os.listdir(PATHS['uploads'])
        if filename.lower().endswith(_EXPORT_IMAGE_EXTENSIONS)
    ]
    if sample_selection == 'annotated':
        images = [img for img in images if img in annotations and annotations[img]]
    elif sample_selection == 'unannotated':
        images = [img for img in images if img not in annotations or not annotations[img]]
    return images


def _compute_splits(images, ratios):
    """Ratio split assigning any remainder to train so no image is dropped.

    Preserves the original per-ratio counting (``int(total * ratio)``) but
    hands the leftover ``total - (n_train+n_val+n_test)`` to train so rounding
    never silently loses an image. Zero ratios stay empty.
    """
    train_ratio, val_ratio, test_ratio = ratios
    total_images = len(images)
    train_images, val_images, test_images = [], [], []

    if train_ratio > 0:
        train_count = int(total_images * train_ratio)
        train_images = images[:train_count]

    val_start = len(train_images) if train_ratio > 0 else 0
    if val_ratio > 0:
        val_count = int(total_images * val_ratio)
        val_images = images[val_start:val_start + val_count]

    test_start = (len(train_images) + len(val_images)) if (train_ratio > 0 or val_ratio > 0) else 0
    if test_ratio > 0:
        test_count = int(total_images * test_ratio)
        test_images = images[test_start:test_start + test_count]

    # Remainder from int() truncation goes to train (if train is active).
    if train_ratio > 0:
        used = len(train_images) + len(val_images) + len(test_images)
        leftover = images[used:used + (total_images - used)]
        train_images = train_images + leftover

    if train_ratio == 0:
        train_images = []
    if val_ratio == 0:
        val_images = []
    if test_ratio == 0:
        test_images = []
    return train_images, val_images, test_images


def _write_data_yaml(yolo_base, selected_classes):
    """Write data.yaml with YOLOv11 layout + selected_classes as names."""
    data_yaml = f"""path: .
train: train/images
val: val/images
test: test/images

nc: {len(selected_classes)}
names: {selected_classes}
"""
    with open(os.path.join(yolo_base, 'data.yaml'), 'w') as f:
        f.write(data_yaml)


def _under(path, base):
    """True if realpath(path) is base itself or a descendant of it.

    ``os.path.normcase`` makes the comparison case-insensitive on Windows,
    where ``realpath`` may return different casings for the same logical
    path (the documented source of intermittent false-negatives).
    """
    try:
        base_nc = os.path.normcase(base)
        common = os.path.commonpath([base_nc, os.path.normcase(os.path.realpath(path))])
    except ValueError:
        return False
    return common == base_nc


def _populate_splits(yolo_base, splits, annotations, selected_classes, sample_selection, export_prefix):
    """Copy images + write YOLO label files for every split.

    ``export_prefix`` is pre-sanitized to ``[A-Za-z0-9_-]`` in
    ``_parse_export_params``, so it can introduce no path separators; each
    destination is additionally containment-checked under ``yolo_base``.
    """
    base_real = os.path.realpath(yolo_base)
    for split_name, split_images in splits:
        img_dir = os.path.join(yolo_base, split_name, 'images')
        label_dir = os.path.join(yolo_base, split_name, 'labels')
        for image_name in split_images:
            src_img_path = os.path.join(PATHS['uploads'], image_name)
            dst_img_name = f"{export_prefix}_{image_name}" if export_prefix else image_name
            dst_img_path = os.path.join(img_dir, dst_img_name)
            if not _under(dst_img_path, base_real):
                print(f"非法导出路径 '{dst_img_path}'")
                continue
            try:
                img = Image.open(src_img_path)
                width, height = img.size
            except Exception as e:
                print(f"无法读取图片 {src_img_path}: {str(e)}")
                continue
            copyfile(src_img_path, dst_img_path)

            base_name = os.path.splitext(image_name)[0]
            label_name = f"{export_prefix}_{base_name}.txt" if export_prefix else f"{base_name}.txt"
            label_path = os.path.join(label_dir, label_name)
            _write_yolo_label_file(
                label_path, annotations.get(image_name, []),
                selected_classes, width, height, sample_selection,
            )


def _write_yolo_label_file(label_path, image_annotations, selected_classes, width, height, sample_selection):
    """Write one image's YOLO label file (empty for unannotated/unselected).

    Verbatim from the original handler: handles both points-array and
    rect (x/y/width/height) annotation formats, normalizes to YOLO
    (cx cy w h) using the selected_classes local index as class_id.
    """
    with open(label_path, 'w') as f:
        if not (image_annotations and sample_selection != 'unannotated'):
            return
        for ann in image_annotations:
            if ann['class'] not in selected_classes:
                continue
            class_id = selected_classes.index(ann['class'])
            points = ann.get('points', [])
            if isinstance(points, list) and len(points) > 0:
                _write_bbox_from_points(f, class_id, points, width, height)
            elif 'x' in ann and 'y' in ann and 'width' in ann and 'height' in ann:
                _write_bbox_from_rect(f, class_id, ann, width, height)
            else:
                print(f"Invalid points data for annotation: {ann}")


def _write_bbox_from_points(f, class_id, points, width, height):
    """Write a YOLO bbox line from a points array (dict or [x,y] pairs)."""
    valid_points = []
    if isinstance(points[0], dict):
        for point in points:
            if 'x' in point and 'y' in point and point['x'] is not None and point['y'] is not None:
                valid_points.append([point['x'], point['y']])
    else:
        for point in points:
            if isinstance(point, (list, tuple)) and len(point) >= 2 and point[0] is not None and point[1] is not None:
                valid_points.append([point[0], point[1]])
    if len(valid_points) == 0:
        return
    points = np.array(valid_points)
    x_min = np.min(points[:, 0])
    y_min = np.min(points[:, 1])
    x_max = np.max(points[:, 0])
    y_max = np.max(points[:, 1])
    if x_min is None or y_min is None or x_max is None or y_max is None:
        return
    center_x = ((x_min + x_max) / 2) / width
    center_y = ((y_min + y_max) / 2) / height
    bbox_width = (x_max - x_min) / width
    bbox_height = (y_max - y_min) / height
    f.write(f"{class_id} {center_x:.6f} {center_y:.6f} {bbox_width:.6f} {bbox_height:.6f}\n")


def _write_bbox_from_rect(f, class_id, ann, width, height):
    """Write a YOLO bbox line from a rect annotation (x/y/width/height)."""
    x, y, w, h = ann['x'], ann['y'], ann['width'], ann['height']
    if x is None or y is None or w is None or h is None:
        return
    x_min, y_min = x, y
    x_max, y_max = x + w, y + h
    center_x = ((x_min + x_max) / 2) / width
    center_y = ((y_min + y_max) / 2) / height
    bbox_width = (x_max - x_min) / width
    bbox_height = (y_max - y_min) / height
    f.write(f"{class_id} {center_x:.6f} {center_y:.6f} {bbox_width:.6f} {bbox_height:.6f}\n")


def _zip_directory(yolo_base, zip_path):
    """Zip yolo_base contents with paths relative to yolo_base (flat train/... layout)."""
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for root, dirs, files in os.walk(yolo_base):
            for file in files:
                file_path = os.path.join(root, file)
                arc_name = os.path.relpath(file_path, yolo_base)
                zipf.write(file_path, arc_name)
