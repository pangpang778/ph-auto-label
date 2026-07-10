"""YOLO subprocess auto-annotation service.

Runs the plugin YOLO model in a child process (plugin venv or current
interpreter) for single-image and batch auto-annotation. Extracted from
the ``ai_annotate`` / ``ai_annotate_batch`` route handlers.

Fixes two latent bugs from the monolith: ``sys.executable`` and
``resolve_training_device`` were both used but never imported, so both
routes always raised ``NameError`` -> 500. They are now imported properly.
No characterization test covered these routes (they could not run), so the
fix is non-breaking; the success response shapes match the original design.

Security/correctness hardening:
* Paths (model / image) are passed to the child process via ENVIRONMENT
  VARIABLES, never f-string-interpolated into the ``python -c`` script -
  a ``"`` in a path could previously break out of the ``r"..."`` literal
  and achieve RCE (C1).
* Image/model lookups use :func:`resolve_child_path` so ``..`` / absolute
  paths cannot escape their base directory (H4).
* The batch read-modify-write now happens atomically under
  :func:`update_annotations` (C2).
* Re-running YOLO batch no longer double-appends detections: existing
  AUTO annotations for an image are replaced while manual ones are
  preserved, making the batch path idempotent (MEDIUM).
"""
import filelock
import json
import os
import subprocess
import sys

from app.common.config import PATHS
from app.common.path_safety import PathSafetyError, resolve_child_path, resolve_contained_path
from app.repositories.annotation_repo import update_annotations
from app.services import models_service
from app.services.annotation_service import (
    AnnotationError,
    assign_class_colors_and_ids,
    update_classes,
)
from app.services.training_service import resolve_training_device

_JSON_START = "###JSON_START###"
_JSON_END = "###JSON_END###"

# Environment variables consumed by the child-process inference scripts.
# Paths are never interpolated into the ``python -c`` source (C1).
_ENV_MODEL_PATH = 'PH_MODEL_PATH'
_ENV_IMAGE_PATH = 'PH_IMAGE_PATH'        # single-image path
_ENV_IMAGE_PATHS_JSON = 'PH_IMAGE_PATHS_JSON'  # batch: json.dumps(list[str])

_IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.bmp')


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

    script = _build_single_script(confidence, device_literal)
    env = _build_env({
        _ENV_MODEL_PATH: model_path,
        _ENV_IMAGE_PATH: image_path,
    })
    output = _run_inference(python_path, script, install_path, env, timeout=60)
    annotations = _extract_json(output, include_output_fallback=True)

    # ponytail: classes RMW under one lock; assign mutates the locked-in
    # current list so a concurrent single-image run cannot lose a class.
    new_classes_added = _merge_classes_for(annotations)

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

    script = _build_batch_script(confidence, device_literal)
    env = _build_env({
        _ENV_MODEL_PATH: model_path,
        _ENV_IMAGE_PATHS_JSON: json.dumps(image_paths),
    })
    output = _run_inference(python_path, script, install_path, env, timeout=300)
    all_annotations = _extract_json(output, include_output_fallback=False)

    return _merge_batch_results(all_annotations, image_paths, valid_image_names)


def _merge_batch_results(all_annotations, image_paths, valid_image_names):
    """Assign colors/ids, merge into persisted annotations, build summary.

    Classes RMW (read-assign-write) happens atomically under one
    ``update_classes`` lock acquisition (H5/H10); annotations RMW under one
    ``update_annotations`` lock (C2). Existing AUTO annotations for an image
    are replaced (preserving manual ones) so a re-run does not double-append
    detections (MEDIUM). The two JSON stores cannot share a cross-file
    transaction; the window between the two locks is accepted (single
    inference run, not a hot concurrent path for classes).
    """
    # Pre-collect per-image annotations (no store access yet).
    per_image = [
        (valid_image_names[i], all_annotations.get(img_path, []))
        for i, img_path in enumerate(image_paths)
    ]

    # Assign colors/ids + merge new classes under the classes lock so a
    # concurrent single-image run cannot lose a class (H5/H10).
    new_classes_added = _merge_classes_for(
        [ann for _name, anns in per_image for ann in anns])

    def _mutate(current):
        results_summary = []
        for img_name, annotations in per_image:
            existing_anns = current.get(img_name, [])
            # Preserve manual annotations; replace prior AUTO detections.
            current[img_name] = [a for a in existing_anns if not a.get('auto')] + annotations
            results_summary.append({'image_name': img_name, 'count': len(annotations), 'success': True})
        return current, results_summary

    try:
        results_summary = update_annotations(_mutate, timeout=10)
    except filelock.Timeout:
        raise AnnotationError(503, '文件正在被其他操作使用，请稍后重试')

    return {
        'success': True,
        'results': results_summary,
        'total_processed': len(results_summary),
        'new_classes_added': new_classes_added,
    }


def _merge_classes_for(annotations):
    """Run assign_class_colors_and_ids against the locked-in current classes.

    Returns ``new_classes_added`` (bool). The classes read-assign-write is
    atomic under ``update_classes`` (H5/H10): the mutator receives the
    current classes list under the filelock, runs assign (which mutates it
    in place, setting color/id on each annotation and appending any new
    class), and persists the result. Only writes when a new class was added.
    """
    def _mutate(current_classes):
        added = assign_class_colors_and_ids(annotations, current_classes)
        return (current_classes if added else None, added)

    try:
        return update_classes(_mutate, timeout=10)
    except filelock.Timeout:
        raise AnnotationError(503, '文件正在被其他操作使用，请稍后重试')


def _resolve_model_name(model_name):
    """Active model name fallback; raises AnnotationError(400) if none."""
    if not model_name:
        active = models_service.get_active_model()
        model_name = active.get('model_name') or os.path.basename(active.get('model_path', ''))
    if not model_name:
        raise AnnotationError(400, 'Model not specified')
    return model_name


def _resolve_install_path(install_path):
    """Resolve install_path and verify it stays under PATHS['root'].

    Previously this only joined relative paths and passed absolute paths
    through unchecked - an attacker-chosen absolute ``install_path`` could
    load a ``.pt`` from anywhere (RCE via ``torch.load`` inside ``YOLO()``).
    Now delegates to the shared containment helper (C1 fix). Raises
    ``AnnotationError(400)`` on escape so the inference routes return a
    clean 400 instead of propagating ``PathSafetyError``.
    """
    try:
        return resolve_contained_path(PATHS['root'], install_path)
    except PathSafetyError as e:
        raise AnnotationError(400, f'非法安装路径: {install_path}') from e


def _validate_image_path(image_name):
    """Absolute image path under uploads; raises AnnotationError(400) if invalid/missing."""
    try:
        image_path = resolve_child_path(PATHS['uploads'], image_name, extensions=_IMAGE_EXTENSIONS)
    except PathSafetyError as e:
        raise AnnotationError(400, f'非法图片路径: {image_name}') from e
    if not os.path.exists(image_path):
        raise AnnotationError(400, f'图片不存在: {image_name}')
    return image_path


def _validate_model_path(model_name, install_path):
    """Absolute model path under <install>/models; raises AnnotationError(400) if invalid/missing."""
    models_dir = os.path.join(install_path, 'models')
    try:
        model_path = resolve_child_path(models_dir, model_name)
    except PathSafetyError as e:
        raise AnnotationError(400, f'非法模型路径: {model_name}') from e
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
    """Filter image_names to those existing in uploads. Returns (paths, names).

    Each surviving name is containment-checked via resolve_child_path so a
    traversal attempt cannot surface a filesystem path here.
    """
    image_paths = []
    valid_image_names = []
    for img_name in image_names:
        try:
            img_path = resolve_child_path(PATHS['uploads'], img_name, extensions=_IMAGE_EXTENSIONS)
        except PathSafetyError:
            continue
        if os.path.exists(img_path):
            image_paths.append(img_path)
            valid_image_names.append(img_name)
    return image_paths, valid_image_names


def _build_env(extra):
    """Base child-process environment (copy of os.environ) plus YOLO-specific vars."""
    env = dict(os.environ)
    env.update(extra)
    return env


def _run_inference(python_path, script, install_path, env, timeout):
    """Run the YOLO script in a child process; return stdout.

    Paths are supplied to the child via ``env`` (never interpolated into
    ``script``). Raises ``AnnotationError(500)`` on non-zero returncode;
    lets ``subprocess.TimeoutExpired`` propagate to the handler.
    """
    result = subprocess.run(
        [python_path, '-c', script],
        capture_output=True, text=True, cwd=install_path,
        timeout=timeout, encoding='utf-8', errors='ignore', env=env,
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


def _build_single_script(confidence, device_literal):
    """YOLO inference script for one image (JSON wrapped in markers).

    Model/image paths are read from environment variables at runtime - they
    are NOT interpolated into this source string (C1). ``confidence`` and
    ``device_literal`` are validated numeric/enum literals, safe to embed.
    """
    return f'''
import json
import sys
import os

# 禁用ultralytics的输出
os.environ['YOLO_VERBOSE'] = 'False'

model_path = os.environ['{_ENV_MODEL_PATH}']
image_path = os.environ['{_ENV_IMAGE_PATH}']

from ultralytics import YOLO

model = YOLO(model_path)
results = model(image_path, conf={confidence}, device={device_literal}, verbose=False)

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


def _build_batch_script(confidence, device_literal):
    """YOLO inference script for a list of images (JSON wrapped in markers).

    The model path and image-path list are read from environment variables
    (``PH_MODEL_PATH`` / ``PH_IMAGE_PATHS_JSON``) at runtime - they are NOT
    interpolated into this source string (C1). ``confidence`` and
    ``device_literal`` are validated numeric/enum literals, safe to embed.
    """
    return f'''
import json
import sys
import os

# 禁用ultralytics的输出
os.environ['YOLO_VERBOSE'] = 'False'

model_path = os.environ['{_ENV_MODEL_PATH}']
image_paths = json.loads(os.environ['{_ENV_IMAGE_PATHS_JSON}'])

from ultralytics import YOLO

model = YOLO(model_path)

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
