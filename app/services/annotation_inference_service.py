"""YOLO subprocess auto-annotation service.

Runs the plugin YOLO model in a child process (plugin venv or current
interpreter) for single-image and batch auto-annotation. Extracted from
the ``ai_annotate`` / ``ai_annotate_batch`` route handlers.

Fixes two latent bugs from the monolith: ``sys.executable`` and
``resolve_training_device`` were both used but never imported, so both
routes always raised ``NameError`` -> 500. They are now imported properly.
No characterization test covered these routes (they could not run), so the
fix is non-breaking; the success response shapes match the original design.
"""
import json
import os
import subprocess
import sys

from app.common.config import PATHS
from app.services import models_service
from app.services.annotation_service import (
    AnnotationError,
    assign_class_colors_and_ids,
    read_annotations,
    read_classes,
    write_annotations,
    write_classes,
)
from app.services.training_service import resolve_training_device

_JSON_START = "###JSON_START###"
_JSON_END = "###JSON_END###"


def run_yolo_single(data):
    """Single-image YOLO auto-annotate. Returns the success body dict.

    Raises ``AnnotationError(400/500)`` for handled conditions; lets
    ``subprocess.TimeoutExpired`` / ``json.JSONDecodeError`` propagate.
    """
    image_name = data.get('image_name', '')
    confidence = float(data.get('confidence', 0.5))
    install_path = data.get('install_path', 'plugins/yolo11')
    device_literal = repr(resolve_training_device(data.get('device', 'auto')))

    if not image_name:
        raise AnnotationError(400, '未指定图片')
    model_name = _resolve_model_name(data.get('model_name', ''))
    install_path = _resolve_install_path(install_path)
    image_path = _validate_image_path(image_name)
    model_path = _validate_model_path(model_name, install_path)
    python_path = _resolve_python_path(install_path)

    script = _build_single_script(model_path, image_path, confidence, device_literal)
    output = _run_inference(python_path, script, install_path, timeout=60)
    annotations = _extract_json(output, include_output_fallback=True)

    existing_classes = read_classes()
    new_classes_added = assign_class_colors_and_ids(annotations, existing_classes)
    if new_classes_added:
        write_classes(existing_classes)

    return {'success': True, 'annotations': annotations, 'new_classes_added': new_classes_added}


def run_yolo_batch(data):
    """Batch YOLO auto-annotate. Returns the success body dict."""
    image_names = data.get('image_names', [])
    confidence = float(data.get('confidence', 0.5))
    install_path = data.get('install_path', 'plugins/yolo11')
    device_literal = repr(resolve_training_device(data.get('device', 'auto')))

    if not image_names:
        raise AnnotationError(400, '未指定图片')
    model_name = _resolve_model_name(data.get('model_name', ''))
    install_path = _resolve_install_path(install_path)
    model_path = _validate_model_path(model_name, install_path)
    python_path = _resolve_python_path(install_path)

    image_paths, valid_image_names = _collect_valid_images(image_names)
    if not image_paths:
        raise AnnotationError(400, '没有有效的图片')

    script = _build_batch_script(model_path, json.dumps(image_paths), confidence, device_literal)
    output = _run_inference(python_path, script, install_path, timeout=300)
    all_annotations = _extract_json(output, include_output_fallback=False)

    return _merge_batch_results(all_annotations, image_paths, valid_image_names)


def _merge_batch_results(all_annotations, image_paths, valid_image_names):
    """Assign colors/ids, merge into persisted annotations, build summary."""
    existing_classes = read_classes()
    all_saved_annotations = read_annotations()
    new_classes_added = False
    results_summary = []
    for i, img_path in enumerate(image_paths):
        img_name = valid_image_names[i]
        annotations = all_annotations.get(img_path, [])
        new_classes_added = assign_class_colors_and_ids(annotations, existing_classes) or new_classes_added
        existing_anns = all_saved_annotations.get(img_name, [])
        all_saved_annotations[img_name] = existing_anns + annotations
        results_summary.append({'image_name': img_name, 'count': len(annotations), 'success': True})
    write_annotations(all_saved_annotations)
    if new_classes_added:
        write_classes(existing_classes)
    return {
        'success': True,
        'results': results_summary,
        'total_processed': len(results_summary),
        'new_classes_added': new_classes_added,
    }


def _resolve_model_name(model_name):
    """Active model name fallback; raises AnnotationError(400) if none."""
    if not model_name:
        active = models_service.get_active_model()
        model_name = active.get('model_name') or os.path.basename(active.get('model_path', ''))
    if not model_name:
        raise AnnotationError(400, 'Model not specified')
    return model_name


def _resolve_install_path(install_path):
    """Make install_path absolute (relative to PATHS['root'])."""
    if not os.path.isabs(install_path):
        install_path = os.path.join(PATHS['root'], install_path)
    return install_path


def _validate_image_path(image_name):
    """Absolute image path; raises AnnotationError(400) if missing."""
    image_path = os.path.abspath(os.path.join(PATHS['uploads'], image_name))
    if not os.path.exists(image_path):
        raise AnnotationError(400, f'图片不存在: {image_name}')
    return image_path


def _validate_model_path(model_name, install_path):
    """Absolute model path; raises AnnotationError(400) if missing."""
    model_path = os.path.abspath(os.path.join(install_path, 'models', model_name))
    if not os.path.exists(model_path):
        raise AnnotationError(400, f'模型不存在: {model_name}')
    return model_path


def _resolve_python_path(install_path):
    """Plugin venv python if present, else current interpreter (sys.executable)."""
    if os.name == 'nt':
        venv_python = os.path.join(install_path, 'venv', 'Scripts', 'python.exe')
    else:
        venv_python = os.path.join(install_path, 'venv', 'bin', 'python')
    return venv_python if os.path.exists(venv_python) else sys.executable


def _collect_valid_images(image_names):
    """Filter image_names to those existing in uploads. Returns (paths, names)."""
    image_paths = []
    valid_image_names = []
    for img_name in image_names:
        img_path = os.path.abspath(os.path.join(PATHS['uploads'], img_name))
        if os.path.exists(img_path):
            image_paths.append(img_path)
            valid_image_names.append(img_name)
    return image_paths, valid_image_names


def _run_inference(python_path, script, install_path, timeout):
    """Run the YOLO script in a child process; return stdout.

    Raises ``AnnotationError(500)`` on non-zero returncode; lets
    ``subprocess.TimeoutExpired`` propagate to the handler.
    """
    result = subprocess.run(
        [python_path, '-c', script],
        capture_output=True, text=True, cwd=install_path,
        timeout=timeout, encoding='utf-8', errors='ignore',
    )
    if result.returncode != 0:
        error_msg = result.stderr.strip() if result.stderr else '推理失败'
        raise AnnotationError(500, f'模型推理失败: {error_msg}')
    return result.stdout


def _extract_json(output, include_output_fallback):
    """Extract the JSON payload between markers.

    Preserves the original monolith's two extraction modes verbatim
    (annotation.py:642-656): single-image uses a ``rfind('[')``/``rfind(']')``
    secondary scan before failing (and includes ``output`` in the error body);
    batch fails immediately. ``json.JSONDecodeError`` propagates to the handler.
    """
    json_start = output.find(_JSON_START)
    json_end = output.find(_JSON_END)
    if json_start == -1 or json_end == -1:
        if include_output_fallback:
            json_start = output.rfind('[')
            json_end = output.rfind(']')
            if json_start == -1 or json_end == -1:
                raise AnnotationError(500, '无法解析模型输出', body={'output': output[:500]})
            json_str = output[json_start:json_end + 1]
        else:
            raise AnnotationError(500, '无法解析模型输出')
    else:
        json_str = output[json_start + len(_JSON_START):json_end].strip()
    return json.loads(json_str)


def _build_single_script(model_path, image_path, confidence, device_literal):
    """YOLO inference script for one image (JSON wrapped in markers)."""
    return f'''
import json
import sys
import os

# 禁用ultralytics的输出
os.environ['YOLO_VERBOSE'] = 'False'

from ultralytics import YOLO

model = YOLO(r"{model_path}")
results = model(r"{image_path}", conf={confidence}, device={device_literal}, verbose=False)

annotations = []
for result in results:
    if result.boxes is not None:
        for box in result.boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            cls_id = int(box.cls[0])
            cls_name = model.names[cls_id]
            conf = float(box.conf[0])

            annotations.append({{
                "class": cls_name,
                "confidence": conf,
                "points": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
                "type": "rectangle",
                "auto": True
            }})

# 使用特殊标记包裹JSON，便于解析
print("###JSON_START###")
print(json.dumps(annotations))
print("###JSON_END###")
'''


def _build_batch_script(model_path, image_paths_json, confidence, device_literal):
    """YOLO inference script for a list of images (JSON wrapped in markers)."""
    return f'''
import json
import sys
import os

# 禁用ultralytics的输出
os.environ['YOLO_VERBOSE'] = 'False'

from ultralytics import YOLO

model = YOLO(r"{model_path}")
image_paths = {image_paths_json}

all_results = {{}}

# 批量推理 - YOLO支持传入列表一次性处理多张图片
results = model(image_paths, conf={confidence}, device={device_literal}, verbose=False)

for i, result in enumerate(results):
    img_path = image_paths[i]
    annotations = []
    if result.boxes is not None:
        for box in result.boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            cls_id = int(box.cls[0])
            cls_name = model.names[cls_id]
            conf = float(box.conf[0])

            annotations.append({{
                "class": cls_name,
                "confidence": conf,
                "points": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
                "type": "rectangle",
                "auto": True
            }})
    all_results[img_path] = annotations

print("###JSON_START###")
print(json.dumps(all_results))
print("###JSON_END###")
'''
