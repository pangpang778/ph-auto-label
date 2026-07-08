"""Model-registry + active-model JSON stores + lock + model-path resolution."""
import os
import threading

from app.common.config import PATHS
from app.common.json_store import read_json_file, write_json_file
from app.common.utils import now_iso

MODEL_REGISTRY_LOCK = threading.Lock()


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
    with MODEL_REGISTRY_LOCK:
        return read_json_file(PATHS['model_registry'], [])


def write_model_registry(models: list[dict]) -> None:
    with MODEL_REGISTRY_LOCK:
        write_json_file(PATHS['model_registry'], models)


def append_model_registry_record(model_record: dict) -> None:
    with MODEL_REGISTRY_LOCK:
        models = read_json_file(PATHS['model_registry'], [])
        models.append(model_record)
        write_json_file(PATHS['model_registry'], models)


def get_active_model() -> dict:
    return read_json_file(PATHS['active_model'], {"model_id": "", "model_name": "", "model_path": ""})


def set_active_model(model_id: str, model_name: str, model_path: str) -> None:
    write_json_file(PATHS['active_model'], {"model_id": model_id, "model_name": model_name, "model_path": model_path, "updated_at": now_iso()})
