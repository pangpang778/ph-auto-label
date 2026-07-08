"""Training-splits JSON store (training_splits.json) + lock."""
import threading

from app.common.config import PATHS
from app.common.json_store import read_json_file, write_json_file

TRAINING_SPLITS_LOCK = threading.Lock()


def read_training_splits() -> dict:
    with TRAINING_SPLITS_LOCK:
        return read_json_file(PATHS['training_splits'], {})


def write_training_splits(data: dict) -> None:
    with TRAINING_SPLITS_LOCK:
        write_json_file(PATHS['training_splits'], data)


def load_split_profile(profile_id: str = "default") -> dict | None:
    profiles = read_training_splits()
    profile = profiles.get(profile_id)
    return profile.get("split_config") if isinstance(profile, dict) else None
