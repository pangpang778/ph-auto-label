"""Generic JSON file I/O helpers.

Callers pass the path (typically from ``app.common.config.PATHS``); these
helpers are path-agnostic. ``write_json_file`` is atomic (temp + os.replace).
"""
import json
import os


def read_json_file(path, default):
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8-sig') as f:
                content = f.read().strip()
                return json.loads(content) if content else default
    except Exception as exc:
        print(f"Failed to read JSON {path}: {exc}")
    return default


def write_json_file(path, data):
    temp_path = path + '.tmp'
    with open(temp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)
