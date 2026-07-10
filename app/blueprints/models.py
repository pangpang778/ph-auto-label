"""Models blueprint: model registry / install / activate routes."""
import json
import os
import shutil
import uuid

from flask import Blueprint, Response, jsonify, request, send_from_directory

from app.common.config import PATHS
from app.common.json_store import read_json_file, write_json_file
from app.common.utils import color_for_index, now_iso
from app.services.models_service import (
    append_record,
    delete_model_file,
    get_active_model,
    get_models_dir,
    get_models_install_path,
    next_model_version,
    read_model_registry,
    resolve_install_path,
    save_uploaded_models,
    set_active,
    stream_model_download,
    write_model_registry,
)
from app.common.path_safety import PathSafetyError

bp = Blueprint("models", __name__)


@bp.route('/api/check-yolo11-install')
def check_yolo11_install():
    """检查YOLO11安装状态"""
    import os
    # 检查YOLO11安装路径是否存在
    yolo11_path = PATHS['plugins_yolo11']
    is_installed = os.path.exists(yolo11_path) and os.path.isdir(yolo11_path)
    
    # 初始化安装信息
    install_info = {
        'is_installed': is_installed,
        'install_time': '',
        'has_cuda': False,
        'hardware': 'CPU'
    }
    
    # 如果已安装，读取详细的安装信息
    if is_installed:
        install_info_path = os.path.join(yolo11_path, 'install_info.json')
        if os.path.exists(install_info_path):
            try:
                with open(install_info_path, 'r', encoding='utf-8') as f:
                    saved_info = json.load(f)
                    # 更新安装信息
                    install_info.update(saved_info)
            except Exception as e:
                print(f"读取安装信息失败: {e}")
    
    return jsonify(install_info)


@bp.route('/api/download-models')
def download_models():
    """Download YOLO models with SSE progress updates.

    NOTE: intentionally left as GET. The frontend (static/js/models.js) opens
    this stream via ``EventSource``, which only supports GET requests; making
    it POST would break the SSE download flow. The model name is validated
    server-side (``[A-Za-z0-9._-]+``) before reaching the child process, so
    the GET surface carries no injection risk (C1 fix applied in
    ``stream_model_download``).
    """
    models_str = request.args.get('models', '')
    models = [m.strip() for m in models_str.split(',') if m.strip()]
    try:
        install_path = resolve_install_path(request.args.get('install_path', 'plugins/yolo11'))
    except PathSafetyError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    return Response(stream_model_download(models, install_path), mimetype='text/event-stream')


@bp.route('/api/list-models')
def list_models():
    """获取已安装的YOLO11模型列表"""
    import os
    
    # 获取安装路径
    install_path = request.args.get('install_path', 'plugins/yolo11')
    # 确保安装路径是相对于项目根目录的
    if not os.path.isabs(install_path):
        install_path = os.path.join(PATHS['root'], install_path)
    
    # 初始化模型列表
    models = []
    
    # 检查YOLO11是否安装
    if os.path.exists(install_path) and os.path.isdir(install_path):
        # 检查models目录是否存在
        models_dir = os.path.join(install_path, 'models')
        if os.path.exists(models_dir) and os.path.isdir(models_dir):
            # 列出models目录下的所有.pt文件
            for file in os.listdir(models_dir):
                if file.endswith('.pt'):
                    models.append(file)
    
    return jsonify({'models': models})


@bp.route('/api/upload-model', methods=['POST'])
def upload_model():
    """上传YOLO11模型文件"""
    try:
        install_path = resolve_install_path(request.headers.get('X-Install-Path', 'plugins/yolo11'))
    except PathSafetyError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    if not os.path.exists(install_path) or not os.path.isdir(install_path):
        return jsonify({'success': False, 'error': 'YOLO11未安装'})
    if 'files[]' not in request.files:
        return jsonify({'success': False, 'error': '未找到上传的文件'})
    files = [(f.filename or '', f.read()) for f in request.files.getlist('files[]')]
    try:
        uploaded = save_uploaded_models(install_path, files)
    except PathSafetyError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    return jsonify({'success': True, 'uploaded_files': uploaded})


@bp.route('/api/delete-model', methods=['POST'])
def delete_model():
    """删除YOLO11模型文件"""
    try:
        install_path = resolve_install_path(request.headers.get('X-Install-Path', 'plugins/yolo11'))
    except PathSafetyError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    if not os.path.exists(install_path) or not os.path.isdir(install_path):
        return jsonify({'success': False, 'error': 'YOLO11未安装'})
    data = request.json or {}
    try:
        success, message = delete_model_file(install_path, data.get('model_name', ''))
    except PathSafetyError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    if success:
        return jsonify({'success': True, 'message': message})
    return jsonify({'success': False, 'error': message})


@bp.route('/api/models/registry')
def models_registry():
    models = read_model_registry()
    models.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return jsonify({"models": models})


@bp.route('/api/models/active')
def models_active():
    return jsonify(get_active_model())


@bp.route('/api/models/<model_id>/activate', methods=['POST'])
def model_activate(model_id):
    models = read_model_registry()
    model = next((m for m in models if m.get("id") == model_id), None)
    if not model:
        return jsonify({"error": "model not found"}), 404
    if not os.path.exists(model.get("path", "")):
        return jsonify({"error": "model file does not exist"}), 400
    set_active(model_id=model["id"], model_name=model["name"], model_path=model["path"])
    for m in models:
        m["status"] = "candidate"
    model["status"] = "production"
    write_model_registry(models)
    return jsonify({"message": "model activated", "active": get_active_model()})
