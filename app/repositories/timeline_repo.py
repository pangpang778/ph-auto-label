"""Timelines + SOP scenario JSON stores.

Both stores are now guarded by cross-process ``filelock`` instances.
:func:`update_timelines` / :func:`update_scenario` are the atomic
read-modify-write entries; the ``write_*`` helpers guard the overwrite only
(a caller that reads first still races). ``read_*`` are lock-free reads for
callers that only inspect.
"""
import filelock

from app.common.config import PATHS
from app.common.json_store import read_json_file, write_json_file

_SCENARIO_DEFAULT = {'scenario_id': '', 'name': '', 'steps': [], 'object_classes': [], 'action_labels': []}

_TIMELINES_LOCK_PATH = PATHS['timelines'] + '.lock'
_SCENARIO_LOCK_PATH = PATHS['scenario'] + '.lock'


def read_timelines() -> dict:
    return read_json_file(PATHS['timelines'], {})


def write_timelines(data: dict) -> None:
    """Overwrite timelines.json under the cross-process lock."""
    lock = filelock.FileLock(_TIMELINES_LOCK_PATH, timeout=10)
    with lock:
        write_json_file(PATHS['timelines'], data)


def update_timelines(mutator, *, timeout=10):
    """Atomically read-modify-write timelines.json under the file lock.

    ``mutator(current: dict) -> (new_data, result)`` where ``new_data`` is the
    dict to persist (or ``None`` to skip the write) and ``result`` is any
    value returned to the caller.
    """
    lock = filelock.FileLock(_TIMELINES_LOCK_PATH, timeout=timeout)
    with lock:
        current = read_json_file(PATHS['timelines'], {})
        new_data, result = mutator(current)
        if new_data is not None:
            write_json_file(PATHS['timelines'], new_data)
        return result


def read_scenario() -> dict:
    return read_json_file(PATHS['scenario'], _SCENARIO_DEFAULT)


def write_scenario(data: dict) -> None:
    """Overwrite sop_scenario.json under the cross-process lock."""
    lock = filelock.FileLock(_SCENARIO_LOCK_PATH, timeout=10)
    with lock:
        write_json_file(PATHS['scenario'], data)


def update_scenario(mutator, *, timeout=10):
    """Atomically read-modify-write sop_scenario.json under the file lock.

    ``mutator(current: dict) -> (new_data, result)`` where ``new_data`` is the
    dict to persist (or ``None`` to skip the write) and ``result`` is any
    value returned to the caller.
    """
    lock = filelock.FileLock(_SCENARIO_LOCK_PATH, timeout=timeout)
    with lock:
        current = read_json_file(PATHS['scenario'], _SCENARIO_DEFAULT)
        new_data, result = mutator(current)
        if new_data is not None:
            write_json_file(PATHS['scenario'], new_data)
        return result
