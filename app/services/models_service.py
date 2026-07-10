"""Models domain service.

Owns model-versioning logic AND re-exports the model-registry repository's
public API. Cross-domain callers (training_service, annotation_service,
video_test_service) MUST go through THIS module - never
app.repositories.model_registry_repo directly (closed-world contract,
Interpretation A - see .omc/plans/api-freeze.md §2).
"""
import json
import os
import re
import subprocess
import sys
import time

from app.common.config import PATHS
from app.common.path_safety import PathSafetyError, resolve_child_path
from app.repositories.model_registry_repo import (
    MODEL_REGISTRY_LOCK,
    append_model_registry_record as append_record,
    get_active_model,
    get_models_dir,
    get_models_install_path,
    read_model_registry,
    set_active_model as set_active,
    write_model_registry,
)

# Model names are passed to a child ``python -c`` script and used as the
# YOLO model stem. Allow only a conservative identifier set so a quote or
# shell metacharacter can never break out of the generated literal.
_MODEL_NAME_RE = re.compile(r'^[A-Za-z0-9._-]+$')

# Env var carrying the requested model name into the download subprocess,
# so the model stem is never interpolated into a code string (C1 fix).
_MODEL_NAME_ENV = 'PH_MODEL_NAME'


class ModelDownloadError(ValueError):
    """Raised when a model name is invalid or download setup fails."""


def _resolve_install_path(install_path):
    """Make install_path absolute and verify it stays under PATHS['root'].

    ``install_path`` originates from the ``X-Install-Path`` header / query
    param (user-controlled). An absolute value previously escaped the
    project root; we now reject any resolved path that is not contained
    under ``PATHS['root']`` (H2b fix).
    """
    if not install_path:
        raise PathSafetyError('安装路径不能为空')
    if not os.path.isabs(install_path):
        install_path = os.path.join(PATHS['root'], install_path)
    install_path = os.path.realpath(install_path)
    root_real = os.path.realpath(PATHS['root'])
    try:
        common = os.path.commonpath([root_real, install_path])
    except ValueError:
        raise PathSafetyError(f'非法安装路径: {install_path}')
    if common != root_real:
        raise PathSafetyError(f'安装路径越界: {install_path}')
    return install_path


# Backward-compat public alias; callers (blueprint) import ``resolve_install_path``.
def resolve_install_path(install_path):
    """Public wrapper around :func:`_resolve_install_path`."""
    return _resolve_install_path(install_path)


# Backward-compat aliases (deprecated; prefer append_record / set_active).
# Kept so legacy callers keep resolving during migration to the
# api-freeze.md section 2 contract names.
append_model_registry_record = append_record
set_active_model = set_active


def _latest_version_tag(models: list[dict]) -> tuple[int, int]:
    major, minor = 0, 0
    for m in models:
        version = str(m.get("version", "")).lstrip("v")
        parts = version.split(".")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            ma, mi = int(parts[0]), int(parts[1])
            if (ma, mi) > (major, minor):
                major, minor = ma, mi
    return major, minor


def next_model_version(mode: str) -> str:
    models = read_model_registry()
    ma, mi = _latest_version_tag(models)
    if ma == 0 and mi == 0:
        return "v1.0"
    if mode == "initial":
        return f"v{ma + 1}.0"
    return f"v{ma}.{mi + 1}"


def stream_model_download(models, install_path):
    """Yield SSE event strings for downloading YOLO models via the plugin venv.

    The handler wraps this generator in a Flask ``Response`` with
    ``mimetype='text/event-stream'``. Fixes the monolith's latent
    ``sys.executable`` NameError (sys was not imported).
    """
    yield _sse({'status': 'started', 'message': 'Starting model download...', 'progress': 0})
    time.sleep(0.2)
    try:
        if not os.path.exists(install_path) or not os.path.isdir(install_path):
            yield _sse({'status': 'error', 'message': 'YOLO11 is not installed', 'progress': 0})
            return
        if not models:
            yield _sse({'status': 'error', 'message': 'No model selected', 'progress': 0})
            return
        if os.name == 'nt':
            plugin_python = os.path.join(install_path, 'venv', 'Scripts', 'python.exe')
        else:
            plugin_python = os.path.join(install_path, 'venv', 'bin', 'python')
        if os.path.exists(plugin_python):
            python_path = plugin_python
        else:
            python_path = sys.executable
            yield _sse({'message': 'plugins/yolo11/venv not found, fallback to current Python runtime', 'progress': 5})
        models_dir = os.path.join(install_path, 'models')
        os.makedirs(models_dir, exist_ok=True)
        # Validate every model name up front: it is used as the YOLO model
        # stem and must match a conservative identifier set so it can never
        # break out of the child-process code literal (C1 fix).
        validated_models = []
        for model in models:
            if not _MODEL_NAME_RE.match(str(model)):
                yield _sse({
                    'status': 'error',
                    'message': f'非法模型名称: {model}',
                    'progress': 0,
                })
                return
            validated_models.append(model)
        total_models = len(validated_models)
        # The model stem is read from an env var inside the child script
        # instead of being interpolated into the code string (C1 fix).
        download_script = (
            "import os;from ultralytics import YOLO;"
            f"name=os.environ['{_MODEL_NAME_ENV}'];YOLO(name+'.pt')"
        )
        for i, model in enumerate(validated_models):
            progress = int((i / total_models) * 80) + 10
            yield _sse({'message': f'Downloading model: {model}...', 'progress': progress})
            env = {**os.environ, _MODEL_NAME_ENV: str(model)}
            result = subprocess.run(
                [python_path, '-c', download_script],
                capture_output=True, text=True, cwd=models_dir,
                env=env, encoding='utf-8', errors='ignore', timeout=900,
            )
            if result.returncode != 0:
                err = (result.stderr or '').strip()[:500]
                yield _sse({'status': 'error', 'message': f'Failed to download {model}: {err}', 'progress': 0})
                return
            time.sleep(0.2)
        yield _sse({'message': 'Model download completed', 'progress': 100, 'status': 'completed'})
    except FileNotFoundError as e:
        yield _sse({'status': 'error', 'message': f'File not found: {e.filename or str(e)}', 'progress': 0})
    except (GeneratorExit, BrokenPipeError, ConnectionResetError):
        return
    except Exception as e:
        import traceback
        yield _sse({'status': 'error', 'message': f'Download failed: {str(e)}', 'progress': 0, 'traceback': traceback.format_exc()})


def _sse(payload):
    """Format a dict as an SSE data event string."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def save_uploaded_models(install_path, files):
    """Save uploaded .pt files into the install's models dir.

    ``files`` is a list of ``(filename, bytes)``. Returns the saved filenames.
    Raises :class:`PathSafetyError` if a filename escapes the models dir or
    is not a ``.pt`` file (H2 fix).
    """
    models_dir = os.path.join(install_path, 'models')
    os.makedirs(models_dir, exist_ok=True)
    uploaded_files = []
    for filename, content in files:
        if not filename:
            continue
        safe_path = resolve_child_path(models_dir, filename, extensions=['.pt'])
        with open(safe_path, 'wb') as f:
            f.write(content)
        uploaded_files.append(os.path.basename(safe_path))
    return uploaded_files


def delete_model_file(install_path, model_name):
    """Delete a model file. Returns ``(success, message)``.

    Raises :class:`PathSafetyError` if ``model_name`` escapes the models dir
    or is not a ``.pt`` file (H2 fix); the blueprint maps this to HTTP 400.
    """
    if not model_name:
        return False, '模型名称不能为空'
    models_dir = os.path.join(install_path, 'models')
    model_path = resolve_child_path(models_dir, model_name, extensions=['.pt'])
    if not os.path.exists(model_path):
        return False, '模型文件不存在'
    try:
        os.remove(model_path)
        return True, f'模型 {model_name} 删除成功'
    except Exception as e:
        return False, f'删除模型失败: {str(e)}'
