"""LabelMe dataset import service.

Imports an uploaded LabelMe dataset (images + paired ``.json`` shape files)
into the internal annotations + classes stores. Extracted verbatim from the
``upload_labelme_dataset`` route handler (no char test covers it). The handler
passes ``[(filename, bytes), ...]`` so this service stays Flask-agnostic.
"""
import json
import math
import os

from app.common.config import PATHS
from app.common.path_safety import PathSafetyError, secure_save_path
from app.services.annotation_service import read_classes, update_annotations, update_classes

_IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.bmp', '.gif')
_JSON_EXTENSIONS = ('.json',)


def import_labelme_dataset(files):
    """Import a LabelMe dataset. ``files`` is a list of ``(filename, bytes)``.

    Returns ``{"message", "files", "annotations_processed"}``.

    H2: the slow image-writing + JSON parsing happens lock-free; only the
    final merge into annotations.json/classes.json is done under the
    ``update_annotations`` / ``update_classes`` locks. Previously a bare
    ``read_annotations()`` -> mutate -> ``write_annotations()`` overwrote the
    whole file with a stale snapshot, racing concurrent single-image saves
    and batch inference. Now only the newly-discovered classes and the
    imported images' annotations are merged into the current locked state.
    """
    existing_classes = read_classes()
    existing_names = {cls['name'] for cls in existing_classes}
    # Local copy mutated during parse so color lookup works; only entries not
    # in ``existing_names`` are merged under the lock below.
    local_classes = list(existing_classes)

    image_files, json_files = _split_labelme_files(files)
    uploaded_files = []
    parsed = {}  # safe_image_name -> image_annotations (lock-free parse)

    for image_filename, image_bytes in image_files.items():
        # secure_save_path runs secure_filename + containment: a name with '..'
        # or separators is rejected (PathSafetyError) instead of writing outside uploads/.
        try:
            image_path = secure_save_path(
                PATHS['uploads'], image_filename, extensions=_IMAGE_EXTENSIONS,
            )
        except PathSafetyError as e:
            # Re-raise as ValueError so the handler can map it (PathSafetyError is
            # already a ValueError subclass). Traversal input must NOT be written.
            raise ValueError(f'非法图片文件名: {image_filename}: {e}') from e
        safe_image_name = os.path.basename(image_path)
        with open(image_path, 'wb') as f:
            f.write(image_bytes)
        uploaded_files.append(safe_image_name)

        # Pair the LabelMe JSON by the sanitized stem so image<->json still match.
        json_filename = os.path.splitext(safe_image_name)[0] + '.json'
        # json_files was keyed by original upload names; also try the original
        # image_filename's stem in case the client sent the JSON under that name.
        json_content_bytes = json_files.get(json_filename)
        if json_content_bytes is None:
            orig_json = os.path.splitext(image_filename)[0] + '.json'
            json_content_bytes = json_files.get(orig_json)
        if json_content_bytes is not None:
            json_content = json.loads(json_content_bytes.decode('utf-8'))
            image_annotations = _parse_labelme_shapes(json_content, local_classes, existing_names)
            parsed[safe_image_name] = image_annotations

    new_classes = [cls for cls in local_classes if cls['name'] not in existing_names]
    if new_classes:
        def _merge_classes(current):
            have = {c.get('name') for c in current}
            for cls in new_classes:
                if cls['name'] not in have:
                    current.append(cls)
            return current, None
        update_classes(_merge_classes)

    if parsed:
        def _merge_annotations(current):
            current.update(parsed)
            return current, None
        update_annotations(_merge_annotations)

    return {
        'message': 'LabelMe dataset uploaded successfully',
        'files': uploaded_files,
        'annotations_processed': len(parsed),
    }


def _split_labelme_files(files):
    """Partition uploaded files into image_files + json_files dicts keyed by filename."""
    image_files = {}
    json_files = {}
    for filename, content in files:
        if not filename:
            continue
        lowered = filename.lower()
        if lowered.endswith(_IMAGE_EXTENSIONS):
            image_files[filename] = content
        elif lowered.endswith('.json'):
            json_files[filename] = content
    return image_files, json_files


def _parse_labelme_shapes(json_content, classes, existing_class_names):
    """Parse LabelMe shapes into internal annotation format.

    Mutates ``classes`` + ``existing_class_names`` when new labels appear.
    """
    image_annotations = []
    if 'shapes' not in json_content:
        return image_annotations
    for shape in json_content['shapes']:
        label = shape.get('label', '')
        points = shape.get('points', [])
        if label and label not in existing_class_names:
            new_color = '#{:06x}'.format(hash(label) % 0x1000000)
            classes.append({'name': label, 'color': new_color})
            existing_class_names.add(label)
        if points and label:
            image_annotations.append(_convert_labelme_shape(shape, label, points, classes))
    return image_annotations


def _convert_labelme_shape(shape, label, points, classes):
    """Convert one LabelMe shape to the internal annotation format."""
    color = '#000000'
    for cls in classes:
        if cls['name'] == label:
            color = cls['color']
            break
    shape_type = shape.get('shape_type', 'polygon')
    internal_points, internal_type = _normalize_shape_points(shape_type, points)
    return {'class': label, 'color': color, 'points': internal_points, 'type': internal_type}


def _normalize_shape_points(shape_type, points):
    """Normalize LabelMe points (rectangle/circle/line/polygon) verbatim from the monolith."""
    if shape_type == 'rectangle' and len(points) == 2:
        x1, y1 = points[0]
        x2, y2 = points[1]
        return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]], 'rectangle'
    if shape_type == 'circle' and len(points) == 2:
        cx, cy = points[0]
        radius = ((points[1][0] - cx) ** 2 + (points[1][1] - cy) ** 2) ** 0.5
        poly = []
        for i in range(16):
            angle = (i / 16) * 2 * 3.14159
            poly.append([cx + radius * math.cos(angle), cy + radius * math.sin(angle)])
        return poly, 'polygon'
    if shape_type == 'line' and len(points) >= 2:
        return points, 'line'
    return points, 'polygon'
