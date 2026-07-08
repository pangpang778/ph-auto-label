"""Models domain service.

Owns model-versioning logic AND re-exports the model-registry repository's
public API. Cross-domain callers (training_service, annotation_service,
video_test_service) MUST go through THIS module - never
app.repositories.model_registry_repo directly (closed-world contract,
Interpretation A - see .omc/plans/api-freeze.md §2).
"""
from app.repositories.model_registry_repo import (
    MODEL_REGISTRY_LOCK,
    append_model_registry_record,
    get_active_model,
    get_models_dir,
    get_models_install_path,
    read_model_registry,
    set_active_model,
    write_model_registry,
)


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
