"""Video-timeline domain service: SOP scenario parsing + timeline segment normalization."""
import os


def normalize_timeline_segment(raw, video_name=''):
    segment = dict(raw or {})
    start = float(segment.get('start_sec') or 0)
    end = float(segment.get('end_sec') or start)
    if end < start:
        start, end = end, start
    step_id = str(segment.get('step_id') or '').strip()
    action_label = str(segment.get('action_label') or '').strip()
    target_id = str(segment.get('target_id') or '').strip()
    return {
        'id': segment.get('id') or f"seg_{abs(hash((video_name, start, end, step_id, action_label))) % 10000000000}",
        'video_name': segment.get('video_name') or video_name,
        'start_sec': round(start, 3),
        'end_sec': round(end, 3),
        'step_id': step_id,
        'action_label': action_label,
        'target_id': target_id,
        'part_id': str(segment.get('part_id') or '').strip(),
        'event_type': str(segment.get('event_type') or '').strip(),
        'is_complete': int(segment.get('is_complete', 1) or 0),
        'error_type': str(segment.get('error_type') or '').strip(),
        'remark': str(segment.get('remark') or '').strip(),
    }



def load_yaml_file(path):
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError('PyYAML is required for scenario import. Run: pip install PyYAML') from exc
    with open(path, 'r', encoding='utf-8-sig') as f:
        return yaml.safe_load(f) or {}



def parse_sop_scenario(scenario_dir):
    scenario_dir = os.path.abspath(scenario_dir)
    process_path = os.path.join(scenario_dir, 'process.yaml')
    if not os.path.exists(process_path):
        raise FileNotFoundError(f'process.yaml not found in {scenario_dir}')
    process_doc = load_yaml_file(process_path)
    process = process_doc.get('process', {})
    steps = []
    action_ids = []
    object_ids = []
    for step in process_doc.get('steps', []):
        completion = step.get('completion', {}) or {}
        evidence_ids = list(completion.get('all_of', []) or [])
        step_actions = [x.split(':', 1)[1] for x in evidence_ids if isinstance(x, str) and x.startswith('action:')]
        step_objects = [x.split(':', 1)[1] for x in evidence_ids if isinstance(x, str) and x.startswith('object:')]
        action_id = step_actions[0] if step_actions else step.get('action_label', step.get('id', ''))
        for aid in step_actions:
            if aid not in action_ids:
                action_ids.append(aid)
        for oid in step_objects:
            if oid not in object_ids:
                object_ids.append(oid)
        steps.append({
            'id': str(step.get('id', '')),
            'name': str(step.get('name', step.get('id', ''))),
            'action_label': action_id,
            'target_ids': step_objects,
            'event_type': f"{step.get('id', action_id)}_done",
        })

    labels_dir = os.path.join(scenario_dir, 'labels')
    yolo_path = os.path.join(labels_dir, 'yolo_classes.yaml')
    if os.path.exists(yolo_path):
        yolo_doc = load_yaml_file(yolo_path)
        object_ids = []
        for cls in yolo_doc.get('classes', []):
            if isinstance(cls, dict):
                object_ids.append({'id': str(cls.get('id') or cls.get('name')), 'name': str(cls.get('name') or cls.get('id'))})
            else:
                object_ids.append({'id': str(cls), 'name': str(cls)})
    else:
        object_ids = [{'id': oid, 'name': oid} for oid in object_ids]

    action_path = os.path.join(labels_dir, 'action_labels.yaml')
    action_labels = []
    if os.path.exists(action_path):
        action_doc = load_yaml_file(action_path)
        for act in action_doc.get('actions', []):
            if isinstance(act, dict):
                action_labels.append({'id': str(act.get('id') or act.get('name')), 'name': str(act.get('name') or act.get('id'))})
            else:
                action_labels.append({'id': str(act), 'name': str(act)})
    else:
        action_labels = [{'id': aid, 'name': aid} for aid in action_ids]

    return {
        'scenario_id': str(process.get('id') or os.path.basename(scenario_dir)),
        'name': str(process.get('name') or process.get('id') or os.path.basename(scenario_dir)),
        'version': str(process.get('version') or ''),
        'source_path': scenario_dir,
        'steps': steps,
        'object_classes': object_ids,
        'action_labels': action_labels,
    }
