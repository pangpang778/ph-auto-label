"""Video-test blueprint: video inference test routes."""
import json
import os

from flask import Blueprint, Response, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

from app.common.config import PATHS, VIDEO_EXTENSIONS
from app.services.models_service import get_active_model, get_models_dir
from app.services.models_service import list_installed_models as models_service_list_installed_models
from app.services.video_inference_service import VideoTestError, video_inference_service

bp = Blueprint("video_test", __name__)


@bp.route('/video-test')
def video_test_page():
    """视频AI对比测试独立页面。"""
    return render_template('video_test.html')


@bp.route('/api/video-test/videos')
def video_test_videos():
    """列出可选视频（默认素材 + 上传）。"""
    return jsonify({'videos': video_inference_service.list_videos()})


@bp.route('/api/video-test/video/<path:name>')
def video_test_serve(name):
    """服务原视频文件。"""
    path = video_inference_service.resolve_video(name)
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
    while os.path.exists(os.path.join(PATHS["video_uploads"], name)):
        name = f"{base}_{i}{ext}"
        i += 1
    f.save(os.path.join(PATHS["video_uploads"], name))
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


def _launch(fn, data):
    """运行 facade 方法，把 VideoTestError 映射为 HTTP 错误元组。"""
    try:
        return fn(data), None
    except VideoTestError as exc:
        return None, (jsonify({'error': exc.message}), exc.status)


@bp.route('/api/video-test/start', methods=['POST'])
def video_test_start():
    """启动视频推理任务。"""
    result, err = _launch(video_inference_service.start_job, request.json or {})
    if err:
        return err
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


@bp.route('/api/video-test/stream/start', methods=['POST'])
def video_test_stream_start():
    """启动流式 MJPEG 推理会话（边算边播）。"""
    result, err = _launch(video_inference_service.start_stream_session, request.json or {})
    if err:
        return err
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
