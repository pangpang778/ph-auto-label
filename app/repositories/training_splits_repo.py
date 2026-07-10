"""Training-splits JSON store (training_splits.json) + lock.

Guarded by a cross-process ``filelock`` (``training_splits.json.lock``)
instead of a process-local ``threading.Lock``. All read-modify-write
paths now go through :func:`update_split_profile` /
:func:`delete_split_profile_atomic` (``training_service`` migrated off its
own direct ``read_json_file``/``write_json_file`` pair), so no caller
acquires the lock directly.

The ``TRAINING_SPLITS_LOCK`` name is kept (as a ``filelock.FileLock``
instance) purely for import compatibility; it currently has no in-repo
acquirer. Prefer the locked helpers below for any new RMW.
"""
import filelock

from app.common.config import PATHS
from app.common.json_store import read_json_file, write_json_file

_TRAINING_SPLITS_LOCK_PATH = PATHS['training_splits'] + '.lock'

# Kept as a filelock alias for import compatibility. No in-repo caller
# acquires it directly anymore; all RMW goes through the locked helpers
# below (update_split_profile / delete_split_profile_atomic).
TRAINING_SPLITS_LOCK = filelock.FileLock(_TRAINING_SPLITS_LOCK_PATH, timeout=10)


def read_training_splits() -> dict:
    with filelock.FileLock(_TRAINING_SPLITS_LOCK_PATH, timeout=10):
        return read_json_file(PATHS['training_splits'], {})


def write_training_splits(data: dict) -> None:
    """Overwrite training_splits.json under the cross-process lock."""
    with filelock.FileLock(_TRAINING_SPLITS_LOCK_PATH, timeout=10):
        write_json_file(PATHS['training_splits'], data)


def update_split_profile(profile_id, mutator, *, timeout=10):
    """Atomically read-modify-write a single split profile under the file lock.

    Operates on the whole profiles dict but the mutator receives it and is
    expected to touch only ``profiles[profile_id]``. Returns ``result`` from
    the mutator.

    ``mutator(profiles: dict) -> (new_profiles, result)`` where ``new_profiles``
    is the dict to persist (or ``None`` to skip the write) and ``result`` is
    any value returned to the caller.
    """
    lock = filelock.FileLock(_TRAINING_SPLITS_LOCK_PATH, timeout=timeout)
    with lock:
        current = read_json_file(PATHS['training_splits'], {})
        new_data, result = mutator(current)
        if new_data is not None:
            write_json_file(PATHS['training_splits'], new_data)
        return result


def delete_split_profile_atomic(profile_id, *, timeout=10):
    """Remove a split profile under the cross-process lock (idempotent).

    Returns the removed profile dict, or ``None`` if it was absent.
    """
    removed = {}

    def _mutator(profiles):
        nonlocal removed
        if profile_id in profiles:
            removed = profiles[profile_id]
            del profiles[profile_id]
            return profiles, None
        # absent -> no write
        return None, None

    update_split_profile(profile_id, _mutator, timeout=timeout)
    return removed or None


def load_split_profile(profile_id: str = "default") -> dict | None:
    profiles = read_training_splits()
    profile = profiles.get(profile_id)
    return profile.get("split_config") if isinstance(profile, dict) else None
