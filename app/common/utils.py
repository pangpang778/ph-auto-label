"""Small stateless helpers shared across domains."""
from datetime import datetime


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec='seconds') + "Z"


def color_for_index(index):
    palette = ['#3aa757', '#4c9ffd', '#ff9d00', '#dc3545', '#6f42c1', '#20c997', '#fd7e14', '#17a2b8', '#e83e8c', '#6610f2']
    return palette[index % len(palette)]
