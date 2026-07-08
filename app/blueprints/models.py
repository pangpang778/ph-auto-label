"""Models blueprint: model registry / install / activate routes."""
import json
import os
import shutil
import uuid

from flask import Blueprint, jsonify, request, send_from_directory

from app.common.config import PATHS
from app.common.json_store import read_json_file, write_json_file
from app.common.utils import color_for_index, now_iso
from app.services.models_service import (
    append_model_registry_record,
    get_active_model,
    get_models_dir,
    get_models_install_path,
    next_model_version,
    read_model_registry,
    set_active_model,
    write_model_registry,
)

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
    """Download YOLO models with SSE progress updates."""
    import os
    import subprocess
    import time
    from flask import Response

    models_str = request.args.get('models', '')
    models = [m.strip() for m in models_str.split(',') if m.strip()]
    install_path = request.args.get('install_path', 'plugins/yolo11')

    if not os.path.isabs(install_path):
        install_path = os.path.join(PATHS['root'], install_path)

    def sse(payload: dict) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def generate():
        yield sse({'status': 'started', 'message': 'Starting model download...', 'progress': 0})
        time.sleep(0.2)

        try:
            if not os.path.exists(install_path) or not os.path.isdir(install_path):
                yield sse({'status': 'error', 'message': 'YOLO11 is not installed', 'progress': 0})
                return

            if not models:
                yield sse({'status': 'error', 'message': 'No model selected', 'progress': 0})
                return

            # Prefer plugin venv; fallback to current Python runtime.
            if os.name == 'nt':
                plugin_python = os.path.join(install_path, 'venv', 'Scripts', 'python.exe')
            else:
                plugin_python = os.path.join(install_path, 'venv', 'bin', 'python')

            if os.path.exists(plugin_python):
                python_path = plugin_python
            else:
                python_path = sys.executable
                yield sse({'message': 'plugins/yolo11/venv not found, fallback to current Python runtime', 'progress': 5})

            models_dir = os.path.join(install_path, 'models')
            os.makedirs(models_dir, exist_ok=True)

            total_models = len(models)
            for i, model in enumerate(models):
                progress = int((i / total_models) * 80) + 10
                yield sse({'message': f'Downloading model: {model}...', 'progress': progress})

                result = subprocess.run(
                    [python_path, '-c', f'from ultralytics import YOLO; YOLO("{model}.pt")'],
                    capture_output=True,
                    text=True,
                    cwd=models_dir,
                    encoding='utf-8',
                    errors='ignore',
                    timeout=900,
                )

                if result.returncode != 0:
                    err = (result.stderr or '').strip()[:500]
                    yield sse({'status': 'error', 'message': f'Failed to download {model}: {err}', 'progress': 0})
                    return

                time.sleep(0.2)

            yield sse({'message': 'Model download completed', 'progress': 100, 'status': 'completed'})

        except FileNotFoundError as e:
            yield sse({'status': 'error', 'message': f'File not found: {e.filename or str(e)}', 'progress': 0})
        except (GeneratorExit, BrokenPipeError, ConnectionResetError):
            # EventSource client disconnected. This is normal.
            return
        except Exception as e:
            import traceback
            yield sse({'status': 'error', 'message': f'Download failed: {str(e)}', 'progress': 0, 'traceback': traceback.format_exc()})

    return Response(generate(), mimetype='text/event-stream')


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
    import os
    
    # 获取安装路径
    install_path = request.headers.get('X-Install-Path', 'plugins/yolo11')
    # 确保安装路径是相对于项目根目录的
    if not os.path.isabs(install_path):
        install_path = os.path.join(PATHS['root'], install_path)
    
    # 检查YOLO11是否安装
    if not os.path.exists(install_path) or not os.path.isdir(install_path):
        return jsonify({'success': False, 'error': 'YOLO11未安装'})
    
    # 检查是否有文件上传
    if 'files[]' not in request.files:
        return jsonify({'success': False, 'error': '未找到上传的文件'})
    
    # 创建models目录
    models_dir = os.path.join(install_path, 'models')
    os.makedirs(models_dir, exist_ok=True)
    
    # 保存上传的文件
    uploaded_files = []
    files = request.files.getlist('files[]')
    for file in files:
        if file.filename != '' and file.filename.endswith('.pt'):
            # 保存文件到models目录
            file_path = os.path.join(models_dir, file.filename)
            file.save(file_path)
            uploaded_files.append(file.filename)
    
    return jsonify({'success': True, 'uploaded_files': uploaded_files})


@bp.route('/api/delete-model', methods=['POST'])
def delete_model():
    """删除YOLO11模型文件"""
    import os
    
    # 获取安装路径
    install_path = request.headers.get('X-Install-Path', 'plugins/yolo11')
    # 确保安装路径是相对于项目根目录的
    if not os.path.isabs(install_path):
        install_path = os.path.join(PATHS['root'], install_path)
    
    # 获取模型名称
    data = request.json or {}
    model_name = data.get('model_name', '')
    
    # 检查YOLO11是否安装
    if not os.path.exists(install_path) or not os.path.isdir(install_path):
        return jsonify({'success': False, 'error': 'YOLO11未安装'})
    
    # 检查模型名称是否为空
    if not model_name:
        return jsonify({'success': False, 'error': '模型名称不能为空'})
    
    # 构建模型文件路径
    models_dir = os.path.join(install_path, 'models')
    model_path = os.path.join(models_dir, model_name)
    
    # 检查模型文件是否存在
    if not os.path.exists(model_path):
        return jsonify({'success': False, 'error': '模型文件不存在'})
    
    try:
        # 删除模型文件
        os.remove(model_path)
        return jsonify({'success': True, 'message': f'模型 {model_name} 删除成功'})
    except Exception as e:
        return jsonify({'success': False, 'error': f'删除模型失败: {str(e)}'})


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
    set_active_model(model_id=model["id"], model_name=model["name"], model_path=model["path"])
    for m in models:
        m["status"] = "candidate"
    model["status"] = "production"
    write_model_registry(models)
    return jsonify({"message": "model activated", "active": get_active_model()})
