"""Model-registry + active-model JSON stores + lock + model-path resolution.

The model registry is guarded by a cross-process ``filelock``
(``model_registry.json.lock``) instead of a process-local ``threading.Lock``
so concurrent worker processes can no longer clobber each other's
read-modify-write cycles. :func:`update_model_registry` is the atomic RMW
entry; :func:`append_model_registry_record` is rewritten on top of it.
"""
import os

import filelock

from app.common.config import PATHS
from app.common.json_store import read_json_file, write_json_file
from app.common.utils import now_iso

_MODEL_REGISTRY_LOCK_PATH = PATHS['model_registry'] + '.lock'

# Kept as a process-local filelock alias for any caller that still imports the
# name. Prefer update_model_registry() for RMW; this instance is acquired
# non-reentrantly by the read/write helpers below.
MODEL_REGISTRY_LOCK = filelock.FileLock(_MODEL_REGISTRY_LOCK_PATH, timeout=10)


def get_models_install_path() -> str:
    install_path = PATHS['plugins_yolo11']
    if not os.path.exists(install_path):
        os.makedirs(install_path, exist_ok=True)
    return install_path


def get_models_dir() -> str:
    models_dir = os.path.join(get_models_install_path(), 'models')
    os.makedirs(models_dir, exist_ok=True)
    return models_dir


def read_model_registry() -> list[dict]:
    with filelock.FileLock(_MODEL_REGISTRY_LOCK_PATH, timeout=10):
        return read_json_file(PATHS['model_registry'], [])


def write_model_registry(models: list[dict]) -> None:
    """Overwrite model_registry.json under the cross-process lock."""
    with filelock.FileLock(_MODEL_REGISTRY_LOCK_PATH, timeout=10):
        write_json_file(PATHS['model_registry'], models)


def update_model_registry(mutator, *, timeout=10):
    """Atomically read-modify-write model_registry.json under the file lock.

    ``mutator(current: list) -> (new_data, result)`` where ``new_data`` is the
    list to persist (or ``None`` to skip the write) and ``result`` is any
    value returned to the caller.
    """
    lock = filelock.FileLock(_MODEL_REGISTRY_LOCK_PATH, timeout=timeout)
    with lock:
        current = read_json_file(PATHS['model_registry'], [])
        new_data, result = mutator(current)
        if new_data is not None:
            write_json_file(PATHS['model_registry'], new_data)
        return result


def append_model_registry_record(model_record: dict) -> None:
    """Append a single record atomically via :func:`update_model_registry`."""
    def _mutator(models):
        models.append(model_record)
        return models, None
    update_model_registry(_mutator)


def get_active_model() -> dict:
    return read_json_file(PATHS['active_model'], {"model_id": "", "model_name": "", "model_path": ""})


def set_active_model(model_id: str, model_name: str, model_path: str) -> None:
    write_json_file(PATHS['active_model'], {"model_id": model_id, "model_name": model_name, "model_path": model_path, "updated_at": now_iso()})
