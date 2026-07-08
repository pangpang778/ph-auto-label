"""Video-timeline blueprint: videos, timelines, SOP scenario, export-timeline."""
import csv
import json
import os
import shutil

from flask import Blueprint, jsonify, request, send_file, send_from_directory

from app.common.config import PATHS, VIDEO_EXTENSIONS
from app.common.json_store import read_json_file, write_json_file
from app.common.utils import now_iso
from app.repositories.annotation_repo import read_classes
from app.repositories.timeline_repo import read_scenario, read_timelines, write_scenario, write_timelines
from app.services.annotation_service import sync_object_classes_to_labels
from app.services.video_timeline_service import load_yaml_file, normalize_timeline_segment, parse_sop_scenario
from plugins.video_inference import list_available_videos, resolve_video_path

bp = Blueprint("video_timeline", __name__)


@bp.route('/api/videos')
def list_videos():
    """List uploaded videos that can be used for SOP timeline annotation."""
    videos = []
    for filename in os.listdir(PATHS['uploads']):
        if filename.lower().endswith(VIDEO_EXTENSIONS):
            path = os.path.join(PATHS['uploads'], filename)
            videos.append({
                'name': filename,
                'size': os.path.getsize(path),
                'url': f'/api/video/{filename}',
            })
    videos.sort(key=lambda x: x['name'].lower())
    return jsonify({'videos': videos})


@bp.route('/api/video/<path:filename>')
def get_video(filename):
    return send_from_directory(PATHS['uploads'], filename)


@bp.route('/api/upload/timeline-video', methods=['POST'])
def upload_timeline_video():
    """Upload and keep a full video for SOP action timeline labeling."""
    if 'video' not in request.files:
        return jsonify({'error': 'No video file provided'}), 400
    video_file = request.files['video']
    if not video_file.filename:
        return jsonify({'error': 'No video file selected'}), 400
    original = video_file.filename
    safe_name = secure_filename(original) or 'timeline_video.mp4'
    base, ext = os.path.splitext(safe_name)
    if ext.lower() not in VIDEO_EXTENSIONS:
        return jsonify({'error': f'Unsupported video extension: {ext}'}), 400
    filename = safe_name
    index = 1
    while os.path.exists(os.path.join(PATHS['uploads'], filename)):
        filename = f"{base}_{index}{ext}"
        index += 1
    path = os.path.join(PATHS['uploads'], filename)
    video_file.save(path)
    return jsonify({'message': 'Timeline video uploaded', 'video_name': filename, 'url': f'/api/video/{filename}'})


@bp.route('/api/scenario')
def get_sop_scenario():
    return jsonify(read_scenario())


@bp.route('/api/scenario', methods=['POST'])
def save_sop_scenario():
    scenario = request.json or {}
    scenario.setdefault('steps', [])
    scenario.setdefault('object_classes', [])
    scenario.setdefault('action_labels', [])
    write_scenario(scenario)
    if scenario.get('object_classes'):
        sync_object_classes_to_labels(scenario.get('object_classes', []), replace=bool(scenario.get('replace_classes')))
    return jsonify({'message': 'Scenario saved', 'scenario': scenario})


@bp.route('/api/scenario/import', methods=['POST'])
def import_sop_scenario():
    """Import SOP steps/classes from universal_sop_platform scenario package."""
    data = request.json or {}
    scenario_path = data.get('scenario_path') or data.get('path') or ''
    if not scenario_path:
        return jsonify({'error': 'scenario_path is required'}), 400
    try:
        scenario = parse_sop_scenario(scenario_path)
        write_scenario(scenario)
        classes = sync_object_classes_to_labels(scenario.get('object_classes', []), replace=bool(data.get('replace_classes', True)))
        return jsonify({'message': 'Scenario imported', 'scenario': scenario, 'classes': classes})
    except Exception as exc:
        traceback.print_exc()
        return jsonify({'error': str(exc)}), 400


@bp.route('/api/timelines')
def list_timelines():
    return jsonify(read_timelines())


@bp.route('/api/timelines/<path:video_name>')
def get_timeline(video_name):
    timelines = read_timelines()
    return jsonify(timelines.get(video_name, []))


@bp.route('/api/timelines/<path:video_name>', methods=['POST'])
def save_timeline(video_name):
    payload = request.json or {}
    raw_segments = payload if isinstance(payload, list) else payload.get('segments', [])
    segments = [normalize_timeline_segment(seg, video_name) for seg in raw_segments]
    segments.sort(key=lambda x: (x['start_sec'], x['end_sec'], x['step_id']))
    timelines = read_timelines()
    timelines[video_name] = segments
    write_timelines(timelines)
    return jsonify({'message': 'Timeline saved', 'video_name': video_name, 'segments': segments, 'count': len(segments)})


@bp.route('/api/export-timeline')
def export_timeline_csv():
    """Export all SOP action segments as universal_sop_platform timeline CSV."""
    timelines = read_timelines()
    fieldnames = ['video_name', 'start_sec', 'end_sec', 'step_id', 'action_label', 'target_id', 'part_id', 'event_type', 'is_complete', 'error_type', 'remark']
    out = StringIO()
    out.write('\ufeff')
    writer = csv.DictWriter(out, fieldnames=fieldnames, lineterminator='\n')
    writer.writeheader()
    for video_name in sorted(timelines.keys()):
        for segment in sorted(timelines.get(video_name, []), key=lambda x: (float(x.get('start_sec', 0)), float(x.get('end_sec', 0)))):
            row = normalize_timeline_segment(segment, video_name)
            writer.writerow({k: row.get(k, '') for k in fieldnames})
    csv_text = out.getvalue()
    return Response(
        csv_text,
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': 'attachment; filename=timeline.csv'},
    )
