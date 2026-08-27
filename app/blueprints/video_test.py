"""Video-test blueprint: video inference test routes."""
import json
import os

from flask import Blueprint, Response, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

from app.common.config import PATHS, VIDEO_EXTENSIONS
from app.services.models_service import list_installed_models as models_service_list_installed_models
from app.services.models_service import list_models_by_kind
from plugins.yolo_depth.depth_models import list_depth_models
from app.services.video_test_service import (
    _parse_classes,
    get_active_model,
    get_models_dir,
    parse_video_test_params,
    resolve_depth_model,
)
from plugins.sam3_service import sam3_service
from plugins.video_inference import UPLOAD_VIDEO_DIR, list_available_videos, resolve_video_path, video_inference_service

bp = Blueprint("video_test", __name__)


@bp.route('/video-test')
def video_test_page():
    """视频AI对比测试独立页面。"""
    return render_template('video_test.html')


@bp.route('/api/video-test/videos')
def video_test_videos():
    """列出可选视频（默认素材 + 上传）。"""
    return jsonify({'videos': list_available_videos()})


@bp.route('/api/video-test/video/<path:name>')
def video_test_serve(name):
    """服务原视频文件。"""
    path = resolve_video_path(name)
    if not path:
        return jsonify({'error': '视频不存在'}), 404
    return send_from_directory(os.path.dirname(path), os.path.basename(path))


@bp.route('/api/video-test/upload', methods=['POST'])
def video_test_upload():
    """上传视频到 uploads/video_compare。"""
    if 'video' not in request.files:
        return jsonify({'error': '未提供视频文件'}), 400
    f = request.files['video']
    if not f.filename:
        return jsonify({'error': '未选择文件'}), 400
    safe = secure_filename(f.filename) or 'video.mp4'
    base, ext = os.path.splitext(safe)
    if ext.lower() not in VIDEO_EXTENSIONS:
        return jsonify({'error': f'不支持的视频格式: {ext}'}), 400
    name = safe
    i = 1
    while os.path.exists(os.path.join(UPLOAD_VIDEO_DIR, name)):
        name = f"{base}_{i}{ext}"
        i += 1
    f.save(os.path.join(UPLOAD_VIDEO_DIR, name))
    return jsonify({'message': '上传成功', 'name': name, 'url': f'/api/video-test/video/{name}'})


@bp.route('/api/video-test/yolo-models')
def video_test_yolo_models():
    """YOLO 模型下拉：预训练 + 项目已训练模型。"""
    models = [{'name': 'yolo11n.pt (COCO 80类 预训练)', 'value': 'yolo11n.pt'}]
    try:
        md = get_models_dir()
        for fn in models_service_list_installed_models():
            models.append({'name': f'{fn} (已训练)', 'value': os.path.join(md, fn)})
    except Exception:
        pass
    active = get_active_model()
    preferred = active.get('model_path', '')
    return jsonify({'models': models, 'active': preferred})


@bp.route('/api/video-test/depth-models')
def video_test_depth_models():
    """深度模型下拉：内置 2 项 + 注册表 kind=depth 自训练条目（工单 06）。"""
    registry_entries = list_models_by_kind("depth")
    return jsonify({"models": list_depth_models(registry_entries)})


def _dispatch_video_test(data, launcher):
    """Shared validation + launch for video-test start endpoints.

    ``launcher`` is ``video_inference_service.start_job`` or
    ``start_stream_session`` (same signature). Returns the launcher's result
    dict on success, or a ``(jsonify_body, status)`` error tuple.
    """
    try:
        name, engine, mode, target_fps, conf = parse_video_test_params(data)
        depth = resolve_depth_model(data)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    path = resolve_video_path(name)
    if not path:
        return jsonify({'error': f'视频不存在: {name}'}), 400

    extra = {}
    if depth:
        extra = {"depth_model": depth["id"], "weights_path": depth["weights_path"],
                 "depth_metric": depth["metric"], "show_meters": depth["show_meters"]}

    if engine == 'sam3':
        if not sam3_service.is_loaded:
            return jsonify({'error': 'SAM3 模型未加载，请先确认模型已就绪'}), 503
        classes = _parse_classes(data.get('classes'))
        if not classes:
            return jsonify({'error': 'SAM3 需要填写目标类别(text)，如 person,car'}), 400
        return launcher(path, 'sam3', classes=classes, target_fps=target_fps, conf=conf, mode=mode,
                        **extra)

    if engine == 'vlm':
        from plugins.vlm_service import vlm_service as _vlm
        if not _vlm.is_available():
            return jsonify({'error': 'VLM 服务未就绪（docker compose -f docker-compose.vlm.yml up -d）'}), 503
        classes = _parse_classes(data.get('classes'))
        if not classes:
            return jsonify({'error': 'VLM 需要填写目标类别(text)，如 person,car'}), 400
        vlm_model = (data.get('vlm_model') or '').strip() or None
        return launcher(path, 'vlm', classes=classes, target_fps=target_fps, conf=conf, mode=mode,
                        vlm_model=vlm_model, **extra)

    model_path = data.get('model') or 'yolo11n.pt'
    return launcher(path, 'yolo', model_path=model_path, target_fps=target_fps, conf=conf, mode=mode,
                    **extra)


@bp.route('/api/video-test/start', methods=['POST'])
def video_test_start():
    """启动视频推理任务。"""
    data = request.json or {}
    result = _dispatch_video_test(data, video_inference_service.start_job)
    if isinstance(result, tuple):
        return result
    return jsonify({'job_id': result['id'], 'status': result['status']})


@bp.route('/api/video-test/stream/<job_id>')
def video_test_stream(job_id):
    """SSE 实时推送推理进度。"""
    def gen():
        try:
            for chunk in video_inference_service.stream_progress(job_id):
                yield chunk
        except GeneratorExit:
            return
        except Exception as exc:
            yield f"data: {json.dumps({'status': 'error', 'message': str(exc)}, ensure_ascii=False)}\n\n"
    return Response(gen(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@bp.route('/api/video-test/job/<job_id>')
def video_test_job(job_id):
    job = video_inference_service.get_job(job_id)
    if not job:
        return jsonify({'error': '任务不存在'}), 404
    return jsonify(job)


@bp.route('/api/video-test/job/<job_id>/stop', methods=['POST'])
def video_test_job_stop(job_id):
    result = video_inference_service.stop_job(job_id)
    if result is None:
        return jsonify({'error': '任务不存在'}), 404
    return jsonify(result)


@bp.route('/api/video-test/stream/start', methods=['POST'])
def video_test_stream_start():
    """启动流式 MJPEG 推理会话（边算边播）。"""
    data = request.json or {}
    result = _dispatch_video_test(data, video_inference_service.start_stream_session)
    if isinstance(result, tuple):
        return result
    return jsonify(result)


@bp.route('/api/video-test/stream/frames/<sid>')
def video_test_stream_frames(sid):
    """MJPEG 流：原帧+AI帧水平拼接，边算边播。"""
    return Response(
        video_inference_service.stream_mjpeg(sid),
        mimetype='multipart/x-mixed-replace; boundary=frame',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@bp.route('/api/video-test/stream/status/<sid>')
def video_test_stream_status(sid):
    s = video_inference_service.get_session(sid)
    if not s:
        return jsonify({'error': '会话不存在'}), 404
    return jsonify(s)


@bp.route('/api/video-test/stream/stop/<sid>', methods=['POST'])
def video_test_stream_stop(sid):
    return jsonify({'stopped': video_inference_service.stop_session(sid)})
