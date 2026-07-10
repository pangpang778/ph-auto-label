"""Generic JSON file I/O helpers.

Callers pass the path (typically from ``app.common.config.PATHS``); these
helpers are path-agnostic. ``write_json_file`` is atomic (temp + os.replace).
"""
import json
import logging
import os

_log = logging.getLogger(__name__)


class CorruptJSONError(Exception):
    """Raised by ``read_json_file(..., strict=True)`` when a file exists but
    cannot be parsed as JSON (truncated/empty-after-strip/corrupt).

    Distinguished from a missing file (which returns ``default`` in both
    modes) and from a generic ``OSError`` (which still returns ``default``
    in non-strict mode but is re-raised in strict mode).
    """


def read_json_file(path, default, *, strict=False):
    """Read and JSON-decode ``path``.

    Missing file -> always returns ``default`` (both modes).

    ``strict=False`` (default, backward-compatible): a corrupt file
    (``JSONDecodeError``) or an ``OSError`` is logged and ``default`` is
    returned - the call never raises for these cases.

    ``strict=True``: a ``JSONDecodeError`` raises :class:`CorruptJSONError`
    (so callers can distinguish "file is broken" from "file is empty /
    missing"). An ``OSError`` is re-raised as-is. A missing file still
    returns ``default``.
    """
    if not os.path.exists(path):
        return default
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            content = f.read().strip()
            return json.loads(content) if content else default
    except json.JSONDecodeError as exc:
        if strict:
            raise CorruptJSONError(f"Corrupt JSON in {path}: {exc}") from exc
        _log.warning("Corrupt JSON in %s, returning default: %s", path, exc)
    except OSError as exc:
        if strict:
            raise
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
