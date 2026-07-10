"""H18 + Worker A strict mode: json_store corruption handling.

Direct unit tests for ``app.common.json_store.read_json_file`` - no Flask, no
app. Locks the two-mode contract:

- ``strict=False`` (default): corrupt JSON -> log + return ``default`` (backward
  compatible, the call never raises for ``JSONDecodeError``/``OSError``).
- ``strict=True``: corrupt JSON -> raise ``CorruptJSONError`` so callers can
  distinguish "file is broken" from "file is missing/empty".
- Missing file: both modes return ``default``.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.common.json_store import CorruptJSONError, read_json_file, write_json_file  # noqa: E402


@pytest.mark.unit
def test_read_json_file_returns_default_for_missing_file_in_both_modes(tmp_path):
    missing = str(tmp_path / "does_not_exist.json")

    assert read_json_file(missing, {"a": 1}) == {"a": 1}
    assert read_json_file(missing, {"a": 1}, strict=True) == {"a": 1}


@pytest.mark.unit
def test_read_json_file_lenient_mode_returns_default_for_corrupt_json(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")

    # No raise - backward compatible.
    result = read_json_file(str(path), {"fallback": True})

    assert result == {"fallback": True}


@pytest.mark.unit
def test_read_json_file_strict_mode_raises_corrupt_json_error(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(CorruptJSONError):
        read_json_file(str(path), {"fallback": True}, strict=True)


@pytest.mark.unit
def test_read_json_file_strict_mode_returns_default_for_missing_file(tmp_path):
    # Missing file is NOT corruption - strict mode still returns default.
    missing = str(tmp_path / "absent.json")

    assert read_json_file(missing, [], strict=True) == []


@pytest.mark.unit
def test_read_json_file_empty_file_returns_default_in_both_modes(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text("   \n  ", encoding="utf-8")

    assert read_json_file(str(path), {"d": 1}) == {"d": 1}
    assert read_json_file(str(path), {"d": 1}, strict=True) == {"d": 1}


@pytest.mark.unit
def test_read_json_file_round_trips_valid_data(tmp_path):
    path = tmp_path / "ok.json"
    write_json_file(str(path), {"k": [1, 2, 3]})

    assert read_json_file(str(path), {}) == {"k": [1, 2, 3]}
    assert read_json_file(str(path), {}, strict=True) == {"k": [1, 2, 3]}


@pytest.mark.unit
def test_read_json_file_handles_utf8_bom(tmp_path):
    # utf-8-sig should strip a leading BOM and still parse.
    path = tmp_path / "bom.json"
    path.write_bytes(b'\xef\xbb\xbf{"x": 42}')

    assert read_json_file(str(path), {}) == {"x": 42}
