"""Timelines + SOP scenario JSON stores."""
from app.common.config import PATHS
from app.common.json_store import read_json_file, write_json_file

_SCENARIO_DEFAULT = {'scenario_id': '', 'name': '', 'steps': [], 'object_classes': [], 'action_labels': []}


def read_timelines() -> dict:
    return read_json_file(PATHS['timelines'], {})


def write_timelines(data: dict) -> None:
    write_json_file(PATHS['timelines'], data)


def read_scenario() -> dict:
    return read_json_file(PATHS['scenario'], _SCENARIO_DEFAULT)


def write_scenario(data: dict) -> None:
    write_json_file(PATHS['scenario'], data)
