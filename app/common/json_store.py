"""Generic JSON file I/O helpers.

Callers pass the path (typically from ``app.common.config.PATHS``); these
helpers are path-agnostic. ``write_json_file`` is atomic (temp + os.replace).
"""
import json
import logging
import os

_log = logging.getLogger(__name__)


def read_json_file(path, default):
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8-sig') as f:
                content = f.read().strip()
                return json.loads(content) if content else default
    except json.JSONDecodeError as exc:
        _log.warning("Corrupt JSON in %s, returning default: %s", path, exc)
    except OSError as exc:
        _log.warning("Failed to read JSON %s, returning default: %s", path, exc)
    return default


def write_json_file(path, data):
    temp_path = path + '.tmp'
    parent = os.path.dirname(os.path.abspath(path)) or '.'
    os.makedirs(parent, exist_ok=True)
    with open(temp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_path, path)
