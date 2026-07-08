"""Annotation blueprint: classes, images, annotations, upload, ai-annotate, export."""
import base64
import json
import math
import os
import shutil
import time
import traceback
import uuid
from datetime import datetime
from io import StringIO
from urllib.parse import urlparse

import cv2
import numpy as np
from flask import Blueprint, Response, current_app, jsonify, render_template, request, send_file, send_from_directory
from PIL import Image

from app.common.config import PATHS, VIDEO_EXTENSIONS
from app.common.json_store import read_json_file, write_json_file
from app.common.utils import color_for_index, now_iso
from app.repositories.annotation_repo import read_annotations, read_classes, write_annotations, write_classes
from app.services import models_service
from app.services.annotation_service import parse_target_classes, sync_object_classes_to_labels
from plugins.sam3_service import sam3_service
from training_artifacts import resolve_artifact

bp = Blueprint("annotation", __name__)


@bp.route('/')
def index():
    return render_template('index.html')


@bp.route('/api/classes')
def get_classes():
    """获取所有类别"""
    classes = []
    if os.path.exists(PATHS['classes']):
        with open(PATHS['classes'], 'r') as f:
            classes = json.load(f)
    return jsonify(classes)


@bp.route('/api/classes', methods=['POST'])
def save_classes():
    """保存所有类别"""
    data = request.json
    
    with open(PATHS['classes'], 'w') as f:
        json.dump(data, f, indent=2)
    
    return jsonify({'message': 'Classes saved successfully'})


@bp.route('/api/images')
def get_images():
    """获取所有上传的图片"""
    images = []
    
    # 读取标注信息，用于获取每张图片的标注数量
    annotations = {}
    if os.path.exists(PATHS['annotations']):
        try:
            with open(PATHS['annotations'], 'r', encoding='utf-8') as f:
                annotations = json.load(f)
            print(f"[DEBUG] 成功读取标注文件，共有 {len(annotations)} 张图片有标注")
        except json.JSONDecodeError as e:
            # 如果JSON文件无效或为空，使用空字典
            print(f"[ERROR] JSON解析失败: {e}")
            annotations = {}
        except Exception as e:
            # 处理其他可能的错误
            print(f"[ERROR] 读取标注文件失败: {e}")
            annotations = {}
    else:
        print(f"[DEBUG] 标注文件不存在: {PATHS['annotations']}")
    
    for filename in os.listdir(PATHS['uploads']):
        if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp')):
            # 获取图片尺寸信息
            try:
                image_path = os.path.join(PATHS['uploads'], filename)
                with Image.open(image_path) as img:
                    width, height = img.size
            except Exception:
                width, height = 0, 0
            
            # 获取标注数量
            annotation_count = len(annotations.get(filename, []))
            
            images.append({
                'name': filename,
                'width': width,
                'height': height,
                'annotation_count': annotation_count
            })
    
    # 统计有标注的图片数量
    annotated_count = sum(1 for img in images if img['annotation_count'] > 0)
    print(f"[DEBUG] 返回 {len(images)} 张图片，其中 {annotated_count} 张有标注")
    
    return jsonify({'images': images})


@bp.route('/api/images/delete', methods=['POST'])
def delete_images():
    """删除指定的图片"""
    data = request.json or {}
    image_names = data.get('images', [])
    
    deleted_count = 0
    errors = []
    
    for image_name in image_names:
        try:
            # 删除图片文件
            image_path = os.path.join(PATHS['uploads'], image_name)
            if os.path.exists(image_path):
                os.remove(image_path)
                deleted_count += 1
                
                # 同时删除对应的标注信息
                annotations = {}
                if os.path.exists(PATHS['annotations']):
                    with open(PATHS['annotations'], 'r') as f:
                        annotations = json.load(f)
                    
                    if image_name in annotations:
                        del annotations[image_name]
                        with open(PATHS['annotations'], 'w') as f:
                            json.dump(annotations, f, indent=2)
            else:
                errors.append(f"图片 '{image_name}' 不存在")
        except Exception as e:
            errors.append(f"删除图片 '{image_name}' 失败: {str(e)}")
    
    if errors:
        return jsonify({
            'success': False,
            'deleted_count': deleted_count,
            'error': '; '.join(errors)
        }), 400
    
    return jsonify({
        'success': True,
        'deleted_count': deleted_count
    })


@bp.route('/api/image/<filename>')
def get_image(filename):
    """获取指定图片"""
    return send_from_directory(PATHS['uploads'], filename)


@bp.route('/api/upload', methods=['POST'])
def upload_folder():
    """上传整个文件夹"""
    if 'files[]' not in request.files:
        return jsonify({'error': 'No files provided'}), 400
    
    files = request.files.getlist('files[]')
    uploaded_files = []
    
    for file in files:
        if file.filename != '':
            filepath = os.path.join(PATHS['uploads'], file.filename or '')
            file.save(filepath)
            uploaded_files.append(file.filename or '')
    
    return jsonify({'message': 'Files uploaded successfully', 'files': uploaded_files})


@bp.route('/api/upload-labelme', methods=['POST'])
def upload_labelme_dataset():
    """上传LabelMe格式数据集"""
    try:
        if 'files' not in request.files:
            return jsonify({'error': 'No files provided'}), 400
        
        files = request.files.getlist('files')
        uploaded_files = []
        processed_annotations = 0
        
        # 读取现有的类别和标注信息
        classes = []
        if os.path.exists(PATHS['classes']):
            with open(PATHS['classes'], 'r') as f:
                classes = json.load(f)
        
        annotations = {}
        if os.path.exists(PATHS['annotations']):
            with open(PATHS['annotations'], 'r') as f:
                annotations = json.load(f)
        
        # 获取已有类别名称集合，便于快速查找
        existing_class_names = {cls['name'] for cls in classes}
        
        # 处理上传的文件
        image_files = {}
        json_files = {}
        
        for file in files:
            if file.filename != '':
                filename = file.filename or ''
                if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif')):
                    image_files[filename] = file
                elif filename.lower().endswith('.json'):
                    json_files[filename] = file
        
        # 处理图像文件
        for image_filename, image_file in image_files.items():
            # 保存图像文件
            image_path = os.path.join(PATHS['uploads'], image_filename)
            image_file.save(image_path)
            uploaded_files.append(image_filename)
            
            # 查找对应的JSON文件
            json_filename = os.path.splitext(image_filename)[0] + '.json'
            if json_filename in json_files:
                # 读取并解析JSON文件
                json_file = json_files[json_filename]
                json_content = json.loads(json_file.read().decode('utf-8'))
                
                # 解析LabelMe标注格式
                image_annotations = []
                if 'shapes' in json_content:
                    for shape in json_content['shapes']:
                        label = shape.get('label', '')
                        points = shape.get('points', [])
                        
                        # 如果标签不存在于现有类别中，添加它
                        if label and label not in existing_class_names:
                            # 为新类别分配一个默认颜色
                            new_color = '#{:06x}'.format(hash(label) % 0x1000000)
                            classes.append({'name': label, 'color': new_color})
                            existing_class_names.add(label)
                        
                        # 将points转换为我们的内部格式
                        if points and label:
                            # 查找标签的颜色
                            color = '#000000'  # 默认颜色
                            for cls in classes:
                                if cls['name'] == label:
                                    color = cls['color']
                                    break
                            
                            # 确定形状类型
                            shape_type = shape.get('shape_type', 'polygon')
                            
                            # 转换为我们的内部格式
                            internal_points = points
                            internal_type = shape_type
                            
                            # 处理矩形：LabelMe矩形只有2个点，我们需要转换为4个点的矩形
                            if shape_type == 'rectangle' and len(points) == 2:
                                x1, y1 = points[0]
                                x2, y2 = points[1]
                                internal_points = [
                                    [x1, y1],
                                    [x2, y1],
                                    [x2, y2],
                                    [x1, y2]
                                ]
                                internal_type = 'rectangle'
                            elif shape_type == 'circle' and len(points) == 2:
                                # 处理圆形，转换为多边形（简化处理）
                                cx, cy = points[0]
                                radius = ((points[1][0] - cx) ** 2 + (points[1][1] - cy) ** 2) ** 0.5
                                # 转换为16边形近似圆形
                                internal_points = []
                                for i in range(16):
                                    angle = (i / 16) * 2 * 3.14159
                                    x = cx + radius * math.cos(angle)
                                    y = cy + radius * math.sin(angle)
                                    internal_points.append([x, y])
                                internal_type = 'polygon'
                            elif shape_type == 'line' and len(points) >= 2:
                                internal_type = 'line'
                            else:
                                internal_type = 'polygon'
                            
                            # 创建标注对象
                            annotation = {
                                'class': label,
                                'color': color,
                                'points': internal_points,
                                'type': internal_type
                            }
                            image_annotations.append(annotation)
                
                # 保存此图像的标注
                annotations[image_filename] = image_annotations
                processed_annotations += 1
        
        # 保存更新后的类别和标注信息
        with open(PATHS['classes'], 'w') as f:
            json.dump(classes, f, indent=2)
        
        with open(PATHS['annotations'], 'w') as f:
            json.dump(annotations, f, indent=2)
        
        return jsonify({
            'message': 'LabelMe dataset uploaded successfully', 
            'files': uploaded_files,
            'annotations_processed': processed_annotations
        })
        
    except Exception as e:
        return jsonify({'error': f'Failed to process LabelMe dataset: {str(e)}'}), 500


@bp.route('/api/upload/video', methods=['POST'])
def upload_video():
    """上传视频文件并抽帧"""
    import uuid
    
    if 'video' not in request.files:
        return jsonify({'error': 'No video file provided'}), 400
    
    video_file = request.files['video']
    frame_interval = int(request.form.get('frame_interval', 30))  # 默认每隔30帧保存一帧
    
    if video_file.filename == '':
        return jsonify({'error': 'No video file selected'}), 400
    
    try:
        # 保存原始文件名（用于生成帧图片名）
        original_filename = video_file.filename or 'video'
        original_name = os.path.splitext(original_filename)[0]
        video_ext = os.path.splitext(original_filename)[1] or '.mp4'
        
        # 使用UUID作为临时文件名（避免中文路径问题）
        temp_filename = f"temp_{uuid.uuid4().hex}{video_ext}"
        temp_video_path = os.path.join(PATHS['uploads'], temp_filename)
        video_file.save(temp_video_path)
        
        # 抽帧处理，传入原始文件名用于命名帧图片
        extracted_frames = extract_frames(temp_video_path, frame_interval, original_name)
        
        # 删除临时视频文件
        if os.path.exists(temp_video_path):
            os.remove(temp_video_path)
        
        return jsonify({
            'message': 'Video frames extracted successfully', 
            'frames': extracted_frames,
            'count': len(extracted_frames)
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Failed to process video: {str(e)}'}), 500


def extract_frames(video_path, frame_interval, original_name=None):
    """从视频中抽帧并保存为图片"""
    
    # 打开视频文件
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        # 尝试使用绝对路径
        abs_path = os.path.abspath(video_path)
        cap = cv2.VideoCapture(abs_path)
        if not cap.isOpened():
            raise Exception(f"无法打开视频文件: {video_path}")
    
    frame_count = 0
    saved_frame_count = 0
    extracted_frames = []
    
    # 使用传入的原始文件名，如果没有则从路径提取
    if original_name is None:
        video_basename = os.path.basename(video_path)
        if video_basename.startswith('temp_'):
            video_basename = video_basename[5:]
        original_name = os.path.splitext(video_basename)[0]
    
    while True:
        ret, frame = cap.read()
        if not ret:
            break
            
        # 每隔frame_interval帧保存一帧
        if frame_count % frame_interval == 0:
            # 生成文件名
            frame_filename = f"{original_name}_frame_{saved_frame_count:06d}.jpg"
            frame_path = os.path.join(PATHS['uploads'], frame_filename)
            
            # Windows中文路径兼容：使用cv2.imencode + 文件写入
            success, encoded_img = cv2.imencode('.jpg', frame)
            if success:
                with open(frame_path, 'wb') as f:
                    f.write(encoded_img.tobytes())
                extracted_frames.append(frame_filename)
                saved_frame_count += 1
            
        frame_count += 1
    
    cap.release()
    return extracted_frames


@bp.route('/api/annotations/<image_name>')
def get_annotations(image_name):
    """获取特定图片的标注"""
    annotations = {}
    if os.path.exists(PATHS['annotations']):
        try:
            with open(PATHS['annotations'], 'r', encoding='utf-8') as f:
                annotations = json.load(f)
        except json.JSONDecodeError:
            # 如果JSON文件无效或为空，使用空字典
            annotations = {}
        except Exception as e:
            # 处理其他可能的错误
            print(f"Error reading annotations file: {e}")
            annotations = {}
    
    image_annotations = annotations.get(image_name, [])
    return jsonify(image_annotations)


@bp.route('/api/annotations/<image_name>', methods=['POST'])
def save_annotations(image_name):
    """保存特定图片的标注"""
    import shutil
    import filelock
    
    data = request.json
    req_started = time.perf_counter()
    metrics = {
        'lock_wait_ms': 0,
        'read_json_ms': 0,
        'backup_ms': 0,
        'write_verify_replace_ms': 0,
        'total_ms': 0
    }
    
    # 使用文件锁防止并发写入
    lock_file = PATHS['annotations'] + '.lock'
    lock = filelock.FileLock(lock_file, timeout=10)
    
    try:
        lock_wait_started = time.perf_counter()
        with lock:
            metrics['lock_wait_ms'] = int((time.perf_counter() - lock_wait_started) * 1000)
            # 读取现有标注
            annotations = {}
            if os.path.exists(PATHS['annotations']):
                try:
                    read_started = time.perf_counter()
                    with open(PATHS['annotations'], 'r', encoding='utf-8') as f:
                        content = f.read()
                        if content.strip():  # 确保文件不为空
                            annotations = json.loads(content)
                    metrics['read_json_ms'] = int((time.perf_counter() - read_started) * 1000)
                except json.JSONDecodeError as e:
                    # JSON解析失败，不覆盖原文件，返回错误
                    print(f"JSON解析失败: {e}")
                    return jsonify({'error': f'标注文件格式错误，无法保存: {str(e)}'}), 500
                except Exception as e:
                    print(f"读取标注文件失败: {e}")
                    return jsonify({'error': f'读取标注文件失败: {str(e)}'}), 500
            
            # 保存前先备份（每次保存都备份，保留最近一次）
            if os.path.exists(PATHS['annotations']):
                backup_file = PATHS['annotations'] + '.bak'
                try:
                    backup_started = time.perf_counter()
                    shutil.copy2(PATHS['annotations'], backup_file)
                    metrics['backup_ms'] = int((time.perf_counter() - backup_started) * 1000)
                except Exception as e:
                    print(f"备份失败: {e}")
            
            # 更新标注
            annotations[image_name] = data
            
            # 先写入临时文件，成功后再替换原文件
            temp_file = PATHS['annotations'] + '.tmp'
            try:
                write_started = time.perf_counter()
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(annotations, f, indent=2, ensure_ascii=False)
                
                # 验证写入的JSON是否有效
                with open(temp_file, 'r', encoding='utf-8') as f:
                    json.load(f)  # 验证JSON格式
                
                # 替换原文件
                if os.path.exists(PATHS['annotations']):
                    os.replace(temp_file, PATHS['annotations'])
                else:
                    os.rename(temp_file, PATHS['annotations'])
                metrics['write_verify_replace_ms'] = int((time.perf_counter() - write_started) * 1000)
                    
            except Exception as e:
                # 写入失败，删除临时文件
                if os.path.exists(temp_file):
                    os.remove(temp_file)
                print(f"写入标注文件失败: {e}")
                return jsonify({'error': f'保存失败: {str(e)}'}), 500
            
            metrics['total_ms'] = int((time.perf_counter() - req_started) * 1000)
            current_app.logger.info(
                '[annotations.save] image=%s lock_wait_ms=%d read_json_ms=%d backup_ms=%d '
                'write_verify_replace_ms=%d total_ms=%d payload_len=%d',
                image_name,
                metrics['lock_wait_ms'],
                metrics['read_json_ms'],
                metrics['backup_ms'],
                metrics['write_verify_replace_ms'],
                metrics['total_ms'],
                len(data) if isinstance(data, list) else -1
            )
            response = jsonify({'message': 'Annotations saved successfully', 'metrics': metrics})
            response.headers['X-Annotations-Lock-Wait-Ms'] = str(metrics['lock_wait_ms'])
            response.headers['X-Annotations-Read-Ms'] = str(metrics['read_json_ms'])
            response.headers['X-Annotations-Backup-Ms'] = str(metrics['backup_ms'])
            response.headers['X-Annotations-Write-Ms'] = str(metrics['write_verify_replace_ms'])
            response.headers['X-Annotations-Total-Ms'] = str(metrics['total_ms'])
            return response
            
    except filelock.Timeout:
        metrics['total_ms'] = int((time.perf_counter() - req_started) * 1000)
        current_app.logger.warning(
            '[annotations.save.timeout] image=%s waited_ms=%d total_ms=%d',
            image_name,
            int((time.perf_counter() - lock_wait_started) * 1000),
            metrics['total_ms']
        )
        return jsonify({'error': '文件正在被其他操作使用，请稍后重试'}), 503


@bp.route('/api/ai-annotate', methods=['POST'])
def ai_annotate():
    """执行AI自动标注"""
    try:
        data = request.json or {}
        image_name = data.get('image_name', '')
        model_name = data.get('model_name', '')
        confidence = float(data.get('confidence', 0.5))
        install_path = data.get('install_path', 'plugins/yolo11')
        inference_device = resolve_training_device(data.get('device', 'auto'))
        device_literal = repr(inference_device)
        
        if not image_name:
            return jsonify({'error': '未指定图片'}), 400
        if not model_name:
            active = models_service.get_active_model()
            model_name = active.get('model_name') or os.path.basename(active.get('model_path', ''))
        if not model_name:
            return jsonify({'error': 'Model not specified'}), 400
        # 构建路径
        if not os.path.isabs(install_path):
            install_path = os.path.join(PATHS['root'], install_path)
        
        # 构建绝对路径
        image_path = os.path.join(PATHS['uploads'], image_name)
        model_path = os.path.join(install_path, 'models', model_name)
        
        # 确保路径是绝对路径
        image_path = os.path.abspath(image_path)
        model_path = os.path.abspath(model_path)
        
        # 检查文件是否存在
        if not os.path.exists(image_path):
            return jsonify({'error': f'图片不存在: {image_name}'}), 400
        if not os.path.exists(model_path):
            return jsonify({'error': f'模型不存在: {model_name}'}), 400
        
        # 获取Python路径 - 优先使用插件虚拟环境，否则使用系统Python
        if os.name == 'nt':  # Windows
            venv_python = os.path.join(install_path, 'venv', 'Scripts', 'python.exe')
        else:  # Linux/macOS
            venv_python = os.path.join(install_path, 'venv', 'bin', 'python')
        
        # 如果插件虚拟环境存在则使用，否则使用当前Python环境
        if os.path.exists(venv_python):
            python_path = venv_python
        else:
            python_path = sys.executable  # 使用当前运行的Python
        
        # 构建推理脚本 - 使用特殊标记包裹JSON输出
        inference_script = f'''
import json
import sys
import os

# 禁用ultralytics的输出
os.environ['YOLO_VERBOSE'] = 'False'

from ultralytics import YOLO

model = YOLO(r"{model_path}")
results = model(r"{image_path}", conf={confidence}, device={device_literal}, verbose=False)

annotations = []
for result in results:
    if result.boxes is not None:
        for box in result.boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            cls_id = int(box.cls[0])
            cls_name = model.names[cls_id]
            conf = float(box.conf[0])
            
            annotations.append({{
                "class": cls_name,
                "confidence": conf,
                "points": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
                "type": "rectangle",
                "auto": True
            }})

# 使用特殊标记包裹JSON，便于解析
print("###JSON_START###")
print(json.dumps(annotations))
print("###JSON_END###")
'''
        
        # 执行推理
        import subprocess
        result = subprocess.run(
            [python_path, '-c', inference_script],
            capture_output=True,
            text=True,
            cwd=install_path,
            timeout=60,
            encoding='utf-8',
            errors='ignore'
        )
        if result.returncode != 0:
            error_msg = result.stderr.strip() if result.stderr else '推理失败'
            return jsonify({'error': f'模型推理失败: {error_msg}'}), 500
        
        # 解析输出 - 使用特殊标记提取JSON
        output = result.stdout
        
        # 查找JSON标记
        start_marker = "###JSON_START###"
        end_marker = "###JSON_END###"
        
        json_start = output.find(start_marker)
        json_end = output.find(end_marker)
        
        if json_start == -1 or json_end == -1:
            # 回退到旧方法
            json_start = output.rfind('[')
            json_end = output.rfind(']')
            if json_start == -1 or json_end == -1:
                return jsonify({'error': '无法解析模型输出', 'output': output[:500]}), 500
            json_str = output[json_start:json_end+1]
        else:
            json_str = output[json_start + len(start_marker):json_end].strip()
        
        annotations = json.loads(json_str)
        
        # 读取现有类别
        existing_classes = []
        if os.path.exists(PATHS['classes']):
            with open(PATHS['classes'], 'r') as f:
                existing_classes = json.load(f)
        
        existing_class_names = {cls['name'] for cls in existing_classes}
        
        # 为标注添加颜色，并自动创建不存在的类别
        new_classes_added = False
        for ann in annotations:
            cls_name = ann['class']
            # 查找类别颜色
            color = None
            for cls in existing_classes:
                if cls['name'] == cls_name:
                    color = cls['color']
                    break
            
            if color is None:
                # 类别不存在，创建新类别
                new_color = '#{:06x}'.format(hash(cls_name) % 0x1000000)
                existing_classes.append({'name': cls_name, 'color': new_color})
                existing_class_names.add(cls_name)
                color = new_color
                new_classes_added = True
            
            ann['color'] = color
            ann['id'] = int(hash(f"{cls_name}_{ann['points'][0][0]}_{ann['points'][0][1]}") % 1000000000)
        
        # 保存新增的类别
        if new_classes_added:
            with open(PATHS['classes'], 'w') as f:
                json.dump(existing_classes, f, indent=2)
        
        return jsonify({
            'success': True,
            'annotations': annotations,
            'new_classes_added': new_classes_added
        })
        
    except subprocess.TimeoutExpired:
        return jsonify({'error': '模型推理超时'}), 500
    except json.JSONDecodeError as e:
        return jsonify({'error': f'解析模型输出失败: {str(e)}'}), 500
    except Exception as e:
        import traceback
        print(f"AI标注错误: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': f'AI标注失败: {str(e)}'}), 500


@bp.route('/api/ai-annotate-batch', methods=['POST'])
def ai_annotate_batch():
    """批量执行AI自动标注 - 一次性处理多张图片，速度更快"""
    try:
        import subprocess
        data = request.json or {}
        image_names = data.get('image_names', [])
        model_name = data.get('model_name', '')
        confidence = float(data.get('confidence', 0.5))
        install_path = data.get('install_path', 'plugins/yolo11')
        inference_device = resolve_training_device(data.get('device', 'auto'))
        device_literal = repr(inference_device)
        
        if not image_names:
            return jsonify({'error': '未指定图片'}), 400
        if not model_name:
            active = models_service.get_active_model()
            model_name = active.get('model_name') or os.path.basename(active.get('model_path', ''))
        if not model_name:
            return jsonify({'error': 'Model not specified'}), 400
        
        # 构建路径
        if not os.path.isabs(install_path):
            install_path = os.path.join(PATHS['root'], install_path)
        
        model_path = os.path.join(install_path, 'models', model_name)
        model_path = os.path.abspath(model_path)
        
        if not os.path.exists(model_path):
            return jsonify({'error': f'模型不存在: {model_name}'}), 400
        
        # 获取Python路径 - 优先使用插件虚拟环境，否则使用系统Python
        if os.name == 'nt':  # Windows
            venv_python = os.path.join(install_path, 'venv', 'Scripts', 'python.exe')
        else:  # Linux/macOS
            venv_python = os.path.join(install_path, 'venv', 'bin', 'python')
        
        # 如果插件虚拟环境存在则使用，否则使用当前Python环境
        if os.path.exists(venv_python):
            python_path = venv_python
        else:
            python_path = sys.executable  # 使用当前运行的Python
        
        # 构建图片路径列表
        image_paths = []
        valid_image_names = []
        for img_name in image_names:
            img_path = os.path.join(PATHS['uploads'], img_name)
            img_path = os.path.abspath(img_path)
            if os.path.exists(img_path):
                image_paths.append(img_path)
                valid_image_names.append(img_name)
        
        if not image_paths:
            return jsonify({'error': '没有有效的图片'}), 400
        # 将图片路径列表转为JSON字符串
        image_paths_json = json.dumps(image_paths)
        
        # 构建批量推理脚本
        inference_script = f'''
import json
import sys
import os

# 禁用ultralytics的输出
os.environ['YOLO_VERBOSE'] = 'False'

from ultralytics import YOLO

model = YOLO(r"{model_path}")
image_paths = {image_paths_json}

all_results = {{}}

# 批量推理 - YOLO支持传入列表一次性处理多张图片
results = model(image_paths, conf={confidence}, device={device_literal}, verbose=False)

for i, result in enumerate(results):
    img_path = image_paths[i]
    annotations = []
    if result.boxes is not None:
        for box in result.boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            cls_id = int(box.cls[0])
            cls_name = model.names[cls_id]
            conf = float(box.conf[0])
            
            annotations.append({{
                "class": cls_name,
                "confidence": conf,
                "points": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
                "type": "rectangle",
                "auto": True
            }})
    all_results[img_path] = annotations

print("###JSON_START###")
print(json.dumps(all_results))
print("###JSON_END###")
'''
        
        # 执行批量推理
        result = subprocess.run(
            [python_path, '-c', inference_script],
            capture_output=True,
            text=True,
            cwd=install_path,
            timeout=300,  # 批量处理给更长的超时时间
            encoding='utf-8',
            errors='ignore'
        )
        if result.returncode != 0:
            error_msg = result.stderr.strip() if result.stderr else '推理失败'
            return jsonify({'error': f'模型推理失败: {error_msg}'}), 500
        
        # 解析输出
        output = result.stdout
        start_marker = "###JSON_START###"
        end_marker = "###JSON_END###"
        
        json_start = output.find(start_marker)
        json_end = output.find(end_marker)
        
        if json_start == -1 or json_end == -1:
            return jsonify({'error': '无法解析模型输出'}), 500
        
        json_str = output[json_start + len(start_marker):json_end].strip()
        all_annotations = json.loads(json_str)
        
        # 读取现有类别
        existing_classes = []
        if os.path.exists(PATHS['classes']):
            with open(PATHS['classes'], 'r') as f:
                existing_classes = json.load(f)
        
        existing_class_names = {cls['name'] for cls in existing_classes}
        new_classes_added = False
        
        # 读取现有标注
        all_saved_annotations = {}
        if os.path.exists(PATHS['annotations']):
            with open(PATHS['annotations'], 'r') as f:
                all_saved_annotations = json.load(f)
        
        # 处理每张图片的标注结果
        results_summary = []
        for i, img_path in enumerate(image_paths):
            img_name = valid_image_names[i]
            annotations = all_annotations.get(img_path, [])
            
            # 为标注添加颜色和ID
            for ann in annotations:
                cls_name = ann['class']
                color = None
                for cls in existing_classes:
                    if cls['name'] == cls_name:
                        color = cls['color']
                        break
                
                if color is None:
                    new_color = '#{:06x}'.format(hash(cls_name) % 0x1000000)
                    existing_classes.append({'name': cls_name, 'color': new_color})
                    existing_class_names.add(cls_name)
                    color = new_color
                    new_classes_added = True
                
                ann['color'] = color
                ann['id'] = int(hash(f"{cls_name}_{ann['points'][0][0]}_{ann['points'][0][1]}") % 1000000000)
            
            # 合并到现有标注
            existing_anns = all_saved_annotations.get(img_name, [])
            merged_anns = existing_anns + annotations
            all_saved_annotations[img_name] = merged_anns
            
            results_summary.append({
                'image_name': img_name,
                'count': len(annotations),
                'success': True
            })
        
        # 保存所有标注
        with open(PATHS['annotations'], 'w') as f:
            json.dump(all_saved_annotations, f, indent=2)
        
        # 保存新增的类别
        if new_classes_added:
            with open(PATHS['classes'], 'w') as f:
                json.dump(existing_classes, f, indent=2)
        
        return jsonify({
            'success': True,
            'results': results_summary,
            'total_processed': len(results_summary),
            'new_classes_added': new_classes_added
        })
        
    except subprocess.TimeoutExpired:
        return jsonify({'error': '批量推理超时'}), 500
    except json.JSONDecodeError as e:
        return jsonify({'error': f'解析模型输出失败: {str(e)}'}), 500
    except Exception as e:
        import traceback
        print(f"批量AI标注错误: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': f'批量AI标注失败: {str(e)}'}), 500


@bp.route('/api/sam3/status')
def sam3_status():
    """Check SAM3 model availability."""
    model_path = os.environ.get("SAM3_MODEL_PATH", PATHS['plugins_sam3_models'])
    return jsonify({
        'loaded': sam3_service.is_loaded,
        'model_path': model_path,
        'model_exists': os.path.isfile(model_path),
    })


@bp.route('/api/ai-annotate-sam3', methods=['POST'])
def ai_annotate_sam3():
    """Single-image auto annotation by SAM3 with text prompts."""
    try:
        data = request.json or {}
        image_name = data.get('image_name', '')
        confidence = float(data.get('confidence', 0.5))
        target_classes = parse_target_classes(data.get('target_classes') or data.get('world_classes'))

        if not image_name:
            return jsonify({'error': '未指定图片'}), 400
        if not target_classes:
            return jsonify({'error': '请至少配置一个目标类别（例如 base,frame,mirror,screw）'}), 400

        if not sam3_service.is_loaded:
            return jsonify({'error': 'SAM3模型未加载，请检查模型文件'}), 503

        image_path = os.path.abspath(os.path.join(PATHS['uploads'], image_name))
        if not os.path.exists(image_path):
            return jsonify({'error': f'图片不存在: {image_name}'}), 400

        annotations = sam3_service.detect_from_file(image_path, text=target_classes, conf=confidence)

        existing_classes = read_classes()
        new_classes_added = False
        for ann in annotations:
            cls_name = ann.get('class')
            if not cls_name:
                continue
            color = None
            for cls in existing_classes:
                if cls.get('name') == cls_name:
                    color = cls.get('color')
                    break
            if color is None:
                color = '#{:06x}'.format(hash(cls_name) % 0x1000000)
                existing_classes.append({'name': cls_name, 'color': color})
                new_classes_added = True
            ann['color'] = color
            ann['id'] = int(hash(f"{cls_name}_{ann['points'][0][0]}_{ann['points'][0][1]}") % 1000000000)

        if new_classes_added:
            write_classes(existing_classes)

        return jsonify({
            'success': True,
            'annotations': annotations,
            'new_classes_added': new_classes_added,
            'engine': 'sam3',
            'target_classes': target_classes,
        })

    except Exception as e:
        print(f"SAM3标注错误: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': f'SAM3标注失败: {str(e)}'}), 500


@bp.route('/api/ai-annotate-sam3-batch', methods=['POST'])
def ai_annotate_sam3_batch():
    """Batch auto annotation by SAM3 with text prompts."""
    try:
        data = request.json or {}
        image_names = data.get('image_names', [])
        confidence = float(data.get('confidence', 0.5))
        target_classes = parse_target_classes(data.get('target_classes') or data.get('world_classes'))

        if not image_names:
            return jsonify({'error': '未指定图片'}), 400
        if not target_classes:
            return jsonify({'error': '请至少配置一个目标类别（例如 base,frame,mirror,screw）'}), 400

        if not sam3_service.is_loaded:
            return jsonify({'error': 'SAM3模型未加载，请检查模型文件'}), 503

        image_paths = []
        valid_image_names = []
        for img_name in image_names:
            img_path = os.path.abspath(os.path.join(PATHS['uploads'], img_name))
            if os.path.exists(img_path):
                image_paths.append(img_path)
                valid_image_names.append(img_name)
        if not image_paths:
            return jsonify({'error': '没有有效的图片'}), 400

        all_results = sam3_service.detect_batch_from_files(image_paths, text=target_classes, conf=confidence)

        existing_classes = read_classes()
        annotations = read_annotations()

        response_results = []
        total_detected = 0
        new_class_count = 0

        for i, image_name in enumerate(valid_image_names):
            image_path = image_paths[i]
            image_annotations = all_results.get(image_path, [])

            for ann in image_annotations:
                cls_name = ann.get('class')
                color = None
                for cls in existing_classes:
                    if cls.get('name') == cls_name:
                        color = cls.get('color')
                        break
                if color is None:
                    color = '#{:06x}'.format(hash(cls_name) % 0x1000000)
                    existing_classes.append({'name': cls_name, 'color': color})
                    new_class_count += 1
                ann['color'] = color
                ann['id'] = int(hash(f"{cls_name}_{ann['points'][0][0]}_{ann['points'][0][1]}_{image_name}") % 1000000000)

            if image_annotations:
                annotations[image_name] = image_annotations
                total_detected += len(image_annotations)

            response_results.append({
                'image_name': image_name,
                'success': True,
                'count': len(image_annotations),
                'annotations': image_annotations,
            })

        write_annotations(annotations)
        write_classes(existing_classes)

        return jsonify({
            'success': True,
            'results': response_results,
            'total_processed': len(valid_image_names),
            'total_detected': total_detected,
            'new_classes_added': new_class_count,
            'engine': 'sam3',
            'target_classes': target_classes,
        })

    except Exception as e:
        print(f"批量SAM3标注错误: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': f'批量SAM3标注失败: {str(e)}'}), 500


@bp.route('/api/export', methods=['POST'])
def export_dataset():
    """导出数据集"""
    try:
        import datetime
        
        data = request.json or {}
        # 确保比例值是有效的数字，处理前端可能发送的null或undefined
        train_ratio = float(data.get('train_ratio', 0.7)) if data.get('train_ratio') is not None else 0.7
        val_ratio = float(data.get('val_ratio', 0.2)) if data.get('val_ratio') is not None else 0.2
        test_ratio = float(data.get('test_ratio', 0.1)) if data.get('test_ratio') is not None else 0.1
        selected_classes = data.get('selected_classes', [])
        sample_selection = data.get('sample_selection', 'all')  # 获取样本选择参数，默认为'all'
        export_data_type = data.get('export_data_type', 'yolo')  # 获取导出数据类型参数，默认为'yolo'
        export_prefix = data.get('export_prefix', '')  # 获取导出文件前缀，默认为空字符串
        
        # 检查导出数据类型是否受支持
        if export_data_type not in ['yolo']:
            return jsonify({'error': '不支持的导出数据类型'}), 400
        
        # 前端已经检查了比例总和必须等于1，所以这里不需要再归一化
        # 直接使用前端传递的比例值
        
        # 获取全局类别列表
        classes = []
        if os.path.exists(PATHS['classes']):
            with open(PATHS['classes'], 'r') as f:
                classes = json.load(f)
        
        # 创建临时目录用于生成数据集
        import tempfile
        import zipfile
        temp_dir = tempfile.mkdtemp()
        
        # 生成带时间戳的基础名称，格式：datasets_年月日时分秒
        timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        base_name = f"datasets_{timestamp}"
        
        # 不管有没有前缀，zip文件名和内部文件夹名称都使用datasets_年月日时分秒格式
        yolo_base = os.path.join(temp_dir, base_name)
        
        # 创建符合YOLOv11格式的目录结构
        for split in ['train', 'val', 'test']:
            os.makedirs(os.path.join(yolo_base, split, 'images'), exist_ok=True)
            os.makedirs(os.path.join(yolo_base, split, 'labels'), exist_ok=True)
        
        # 获取所有图片
        images = []
        for filename in os.listdir(PATHS['uploads']):
            if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp')):
                images.append(filename)
        
        # 根据样本选择参数过滤图片
        annotations = {}
        if os.path.exists(PATHS['annotations']):
            with open(PATHS['annotations'], 'r') as f:
                annotations = json.load(f)
        
        # 根据用户选择过滤图片
        if sample_selection == 'annotated':
            # 只选择有标注的图片
            images = [img for img in images if img in annotations and annotations[img]]
        elif sample_selection == 'unannotated':
            # 只选择没有标注的图片
            images = [img for img in images if img not in annotations or not annotations[img]]
        # 如果是'all'则不进行过滤，使用所有图片
        
        # 分割数据集
        np.random.shuffle(images)
        
        total_images = len(images)
        
        # 彻底重写数据集分割逻辑，确保严格按照比例分割
        # 0比例的数据集绝对为空，多余的数据直接扔掉
        train_images = []
        val_images = []
        test_images = []
        
        # 只处理比例大于0的数据集
        if train_ratio > 0:
            # 计算训练集数量
            train_count = int(total_images * train_ratio)
            # 只分配计算出的数量的图片
            train_images = images[:train_count]
        
        # 验证集只在train_ratio > 0时才处理，否则从0开始
        val_start = len(train_images) if train_ratio > 0 else 0
        if val_ratio > 0:
            # 计算验证集数量
            val_count = int(total_images * val_ratio)
            # 只分配计算出的数量的图片
            val_images = images[val_start:val_start + val_count]
        
        # 测试集只在train_ratio > 0或val_ratio > 0时才处理，否则从0开始
        test_start = (len(train_images) + len(val_images)) if (train_ratio > 0 or val_ratio > 0) else 0
        if test_ratio > 0:
            # 计算测试集数量
            test_count = int(total_images * test_ratio)
            # 只分配计算出的数量的图片
            test_images = images[test_start:test_start + test_count]
        
        # 确保0比例的数据集绝对为空
        if train_ratio == 0:
            train_images = []
        if val_ratio == 0:
            val_images = []
        if test_ratio == 0:
            test_images = []
        
        # 处理每个分割的数据集
        splits = [
            ('train', train_images),
            ('val', val_images),
            ('test', test_images)
        ]
        
        # 创建数据集配置文件 (YOLOv11格式)
        data_yaml = f"""path: .
train: train/images
val: val/images
test: test/images

nc: {len(selected_classes)}
names: {selected_classes}
"""
        
        with open(os.path.join(yolo_base, 'data.yaml'), 'w') as f:
            f.write(data_yaml)
        
        # 复制图片和生成标签文件
        for split_name, split_images in splits:
            for image_name in split_images:
                # 复制图片，添加前缀
                src_img_path = os.path.join(PATHS['uploads'], image_name)
                if export_prefix:
                    dst_img_name = f"{export_prefix}_{image_name}"
                else:
                    dst_img_name = image_name
                dst_img_path = os.path.join(yolo_base, split_name, 'images', dst_img_name)
                
                # 使用PIL读取图片尺寸
                try:
                    img = Image.open(src_img_path)
                    width, height = img.size
                except Exception as e:
                    print(f"无法读取图片 {src_img_path}: {str(e)}")
                    continue
                
                # 复制图片文件
                from shutil import copyfile
                copyfile(src_img_path, dst_img_path)
                
                # 生成YOLO格式的标签文件，添加前缀
                base_name = os.path.splitext(image_name)[0]
                if export_prefix:
                    label_name = f"{export_prefix}_{base_name}.txt"
                else:
                    label_name = f"{base_name}.txt"
                label_path = os.path.join(yolo_base, split_name, 'labels', label_name)
                
                image_annotations = annotations.get(image_name, [])
                
                # 对于未标注的图片，创建空的标签文件；对于标注的图片，写入标注信息
                with open(label_path, 'w') as f:
                    # 只有当是标注图片并且选择了相关类别时才写入标注信息
                    if image_annotations and sample_selection != 'unannotated':
                        for ann in image_annotations:
                            # 只导出选中的类别
                            if ann['class'] in selected_classes:
                                # YOLO label format: class_id center_x center_y width height.
                                # data.yaml is generated from selected_classes, so class_id must use that local order.
                                # This keeps partial exports valid.
                                class_id = selected_classes.index(ann['class'])
                                
                                # Write the selected class annotation.
                                if class_id is not None:
                                    points = ann.get('points', [])
                                    
                                    # 处理不同格式的points数据
                                    if isinstance(points, list) and len(points) > 0:
                                        # 检查points是坐标对的数组还是对象数组
                                        valid_points = []
                                        if isinstance(points[0], dict):
                                            # 对象数组格式 [{x: ..., y: ...}, ...]
                                            for point in points:
                                                if 'x' in point and 'y' in point and point['x'] is not None and point['y'] is not None:
                                                    valid_points.append([point['x'], point['y']])
                                        else:
                                            # 坐标对数组格式 [[x, y], ...]
                                            for point in points:
                                                if isinstance(point, (list, tuple)) and len(point) >= 2 and point[0] is not None and point[1] is not None:
                                                    valid_points.append([point[0], point[1]])
                                            
                                        if len(valid_points) > 0:
                                            points = np.array(valid_points)
                                            
                                            x_min = np.min(points[:, 0])
                                            y_min = np.min(points[:, 1])
                                            x_max = np.max(points[:, 0])
                                            y_max = np.max(points[:, 1])
                                            
                                            # 确保坐标值有效
                                            if x_min is not None and y_min is not None and x_max is not None and y_max is not None:
                                                # 转换为YOLO格式
                                                center_x = ((x_min + x_max) / 2) / width
                                                center_y = ((y_min + y_max) / 2) / height
                                                bbox_width = (x_max - x_min) / width
                                                bbox_height = (y_max - y_min) / height
                                                
                                                f.write(f"{class_id} {center_x:.6f} {center_y:.6f} {bbox_width:.6f} {bbox_height:.6f}\n")
                                    elif 'x' in ann and 'y' in ann and 'width' in ann and 'height' in ann:
                                        # 处理矩形格式的标注数据
                                        x = ann['x']
                                        y = ann['y']
                                        w = ann['width']
                                        h = ann['height']
                                        
                                        # 确保所有值都是有效的数字
                                        if x is not None and y is not None and w is not None and h is not None:
                                            x_min = x
                                            y_min = y
                                            x_max = x + w
                                            y_max = y + h
                                            
                                            # 转换为YOLO格式
                                            center_x = ((x_min + x_max) / 2) / width
                                            center_y = ((y_min + y_max) / 2) / height
                                            bbox_width = (x_max - x_min) / width
                                            bbox_height = (y_max - y_min) / height
                                            
                                            f.write(f"{class_id} {center_x:.6f} {center_y:.6f} {bbox_width:.6f} {bbox_height:.6f}\n")
                                    else:
                                        # points数据格式无效，跳过该标注
                                        print(f"Invalid points data for annotation: {ann}")
                    # 对于未标注的图片，文件将保持为空（只需创建文件）
        
        # 创建zip文件，使用带时间戳的名称
        zip_filename = f"{base_name}.zip"
        zip_path = os.path.join(temp_dir, zip_filename)
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for root, dirs, files in os.walk(yolo_base):
                for file in files:
                    file_path = os.path.join(root, file)
                    # 使用yolo_base作为基准路径，这样zip文件中的目录结构就是直接的train/images/xxx.jpg
                    arc_name = os.path.relpath(file_path, yolo_base)
                    zipf.write(file_path, arc_name)
        
        # 返回zip文件
        return send_from_directory(temp_dir, zip_filename, as_attachment=True, download_name=zip_filename)
        
    except Exception as e:
        import traceback
        print(f"Export error: {str(e)}")
        print(f"Traceback: {traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500
