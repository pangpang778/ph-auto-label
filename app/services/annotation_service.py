"""Annotation domain service.

Class management + AI-annotate helpers. The long ai-annotate / export route
handlers are decomposed into service calls in a later step; for now the factored
helpers live here.
"""
import json
import os

from app.common.utils import color_for_index
from app.repositories.annotation_repo import read_classes, write_classes


def sync_object_classes_to_labels(object_classes, replace=False):
    classes = [] if replace else read_classes()
    existing = {c.get('name') for c in classes}
    for index, obj in enumerate(object_classes):
        name = obj.get('id') or obj.get('name') if isinstance(obj, dict) else str(obj)
        display_name = obj.get('name') if isinstance(obj, dict) else name
        if name and name not in existing:
            classes.append({'name': name, 'display_name': display_name, 'color': color_for_index(len(classes))})
            existing.add(name)
    write_classes(classes)
    return classes



def parse_target_classes(raw_value):
    """Parse target classes for SAM3 from list/string; fallback to local class config."""
    parsed = []
    if isinstance(raw_value, list):
        for item in raw_value:
            name = str(item or '').strip()
            if name and name not in parsed:
                parsed.append(name)
    elif isinstance(raw_value, str):
        normalized = raw_value.replace('\n', ',').replace(';', ',').replace('，', ',')
        for item in normalized.split(','):
            name = item.strip()
            if name and name not in parsed:
                parsed.append(name)

    if parsed:
        return parsed

    local_classes = read_classes()
    fallback = []
    for cls in local_classes:
        name = str(cls.get('name') or '').strip()
        if name and name not in fallback:
            fallback.append(name)
    return fallback
