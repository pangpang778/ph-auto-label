"""Annotations + classes JSON stores."""
from app.common.config import PATHS
from app.common.json_store import read_json_file, write_json_file


def read_annotations() -> dict:
    return read_json_file(PATHS['annotations'], {})


def write_annotations(data: dict) -> None:
    write_json_file(PATHS['annotations'], data)


def read_classes() -> list:
    return read_json_file(PATHS['classes'], [])


def write_classes(data: list) -> None:
    write_json_file(PATHS['classes'], data)
