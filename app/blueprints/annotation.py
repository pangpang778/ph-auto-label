"""Annotation blueprint: classes, images, annotations, upload, ai-annotate, export.

Thin HTTP adapters over the ``annotation_*_service`` modules. Handlers keep
only Flask-bound concerns (request parsing, jsonify, send_from_directory,
response headers); business logic lives in Flask-context-free services.
"""
import json
import logging
import os
import subprocess

from flask import Blueprint, jsonify, render_template, request, send_from_directory

from app.common.config import PATHS
from app.common.path_safety import PathSafetyError, secure_save_path
from app.services import (
    annotation_export_service,
    annotation_import_service,
    annotation_inference_service,
    annotation_sam3_service,
    annotation_video_service,
)
from app.services.annotation_service import (
    AnnotationError,
    delete_images as delete_images_service,
    list_images,
    read_annotations,
    read_classes,
    save_image_annotations,
    write_classes,
)
from plugins.sam3_service import sam3_service

bp = Blueprint("annotation", __name__)

logger = logging.getLogger(__name__)

_ANNOTATION_METRIC_HEADERS = (
    ('lock_wait_ms', 'X-Annotations-Lock-Wait-Ms'),
    ('read_json_ms', 'X-Annotations-Read-Ms'),
    ('backup_ms', 'X-Annotations-Backup-Ms'),
    ('write_verify_replace_ms', 'X-Annotations-Write-Ms'),
    ('total_ms', 'X-Annotations-Total-Ms'),
)

_IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.bmp')


def _annotation_error_response(exc):
    """Map an AnnotationError to its HTTP response (preserves extra body fields)."""
    body = {'error': exc.message}
    if exc.body:
        body.update(exc.body)
    return jsonify(body), exc.status


@bp.route('/')
def index():
    return render_template('index.html')


@bp.route('/api/classes')
def get_classes():
    """获取所有类别"""
    return jsonify(read_classes())


@bp.route('/api/classes', methods=['POST'])
def save_classes():
    """保存所有类别"""
    data = request.json
    if not isinstance(data, list):
        return jsonify({'error': 'classes 必须是列表'}), 400
    write_classes(data)
    return jsonify({'message': 'Classes saved successfully'})


@bp.route('/api/images')
def get_images():
    """获取所有上传的图片"""
    return jsonify(list_images())


@bp.route('/api/images/delete', methods=['POST'])
def delete_images():
    """删除指定的图片"""
    data = request.json or {}
    deleted_count, errors = delete_images_service(data.get('images', []))
    if errors:
        return jsonify({'success': False, 'deleted_count': deleted_count, 'error': '; '.join(errors)}), 400
    return jsonify({'success': True, 'deleted_count': deleted_count})


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
        if file.filename == '':
            continue
        try:
            save_path = secure_save_path(
                PATHS['uploads'], file.filename, extensions=_IMAGE_EXTENSIONS,
            )
        except PathSafetyError as e:
            return jsonify({'error': f'非法文件: {file.filename} ({str(e)})'}), 400
        if os.path.exists(save_path):
            base, ext = os.path.splitext(save_path)
            i = 1
            while os.path.exists(f"{base}_{i}{ext}"):
                i += 1
            save_path = f"{base}_{i}{ext}"
        file.save(save_path)
        uploaded_files.append(os.path.basename(save_path))
    return jsonify({'message': 'Files uploaded successfully', 'files': uploaded_files})


@bp.route('/api/upload-labelme', methods=['POST'])
def upload_labelme_dataset():
    """上传LabelMe格式数据集"""
    if 'files' not in request.files:
        return jsonify({'error': 'No files provided'}), 400
    try:
        files = [(f.filename or '', f.read()) for f in request.files.getlist('files')]
        return jsonify(annotation_import_service.import_labelme_dataset(files))
    except PathSafetyError as e:
        return jsonify({'error': str(e)}), 400
    except Exception:
        logger.exception("Failed to process LabelMe dataset")
        return jsonify({'error': 'Failed to process LabelMe dataset'}), 500


@bp.route('/api/upload/video', methods=['POST'])
def upload_video():
    """上传视频文件并抽帧"""
    if 'video' not in request.files:
        return jsonify({'error': 'No video file provided'}), 400
    video_file = request.files['video']
    if video_file.filename == '':
        return jsonify({'error': 'No video file selected'}), 400
    try:
        frame_interval = int(request.form.get('frame_interval', 30))
        if frame_interval < 1:
            return jsonify({'error': 'frame_interval 必须 >= 1'}), 400
        frames = annotation_video_service.extract_video_frames(
            video_file.read(), video_file.filename or 'video', frame_interval,
        )
        return jsonify({'message': 'Video frames extracted successfully', 'frames': frames, 'count': len(frames)})
    except ValueError:
        return jsonify({'error': 'frame_interval 必须是整数'}), 400
    except Exception:
        logger.exception("Failed to process video")
        return jsonify({'error': 'Failed to process video'}), 500


@bp.route('/api/annotations/<image_name>')
def get_annotations(image_name):
    """获取特定图片的标注"""
    return jsonify(read_annotations().get(image_name, []))


@bp.route('/api/annotations/<image_name>', methods=['POST'])
def save_annotations(image_name):
    """保存特定图片的标注"""
    data = request.json
    try:
        metrics = save_image_annotations(image_name, data)
    except AnnotationError as exc:
        return _annotation_error_response(exc)
    response = jsonify({'message': 'Annotations saved successfully', 'metrics': metrics})
    for key, header in _ANNOTATION_METRIC_HEADERS:
        response.headers[header] = str(metrics[key])
    return response


@bp.route('/api/ai-annotate', methods=['POST'])
def ai_annotate():
    """执行AI自动标注"""
    try:
        return jsonify(annotation_inference_service.run_yolo_single(request.json or {}))
    except AnnotationError as exc:
        return _annotation_error_response(exc)
    except subprocess.TimeoutExpired:
        return jsonify({'error': '模型推理超时'}), 500
    except json.JSONDecodeError as e:
        return jsonify({'error': f'解析模型输出失败: {str(e)}'}), 500
    except Exception:
        logger.exception("AI标注错误")
        return jsonify({'error': 'AI标注失败'}), 500


@bp.route('/api/ai-annotate-batch', methods=['POST'])
def ai_annotate_batch():
    """批量执行AI自动标注 - 一次性处理多张图片，速度更快"""
    try:
        return jsonify(annotation_inference_service.run_yolo_batch(request.json or {}))
    except AnnotationError as exc:
        return _annotation_error_response(exc)
    except subprocess.TimeoutExpired:
        return jsonify({'error': '批量推理超时'}), 500
    except json.JSONDecodeError as e:
        return jsonify({'error': f'解析模型输出失败: {str(e)}'}), 500
    except Exception:
        logger.exception("批量AI标注错误")
        return jsonify({'error': '批量AI标注失败'}), 500


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
        return jsonify(annotation_sam3_service.run_sam3_single(request.json or {}))
    except AnnotationError as exc:
        return _annotation_error_response(exc)
    except Exception:
        logger.exception("SAM3标注错误")
        return jsonify({'error': 'SAM3标注失败'}), 500


@bp.route('/api/ai-annotate-sam3-batch', methods=['POST'])
def ai_annotate_sam3_batch():
    """Batch auto annotation by SAM3 with text prompts."""
    try:
        return jsonify(annotation_sam3_service.run_sam3_batch(request.json or {}))
    except AnnotationError as exc:
        return _annotation_error_response(exc)
    except Exception:
        logger.exception("批量SAM3标注错误")
        return jsonify({'error': '批量SAM3标注失败'}), 500


@bp.route('/api/export', methods=['POST'])
def export_dataset():
    """导出数据集"""
    try:
        result = annotation_export_service.export_yolo_dataset(request.json or {})
        return send_from_directory(
            result['temp_dir'], result['zip_filename'],
            as_attachment=True, download_name=result['zip_filename'],
        )
    except AnnotationError as exc:
        return _annotation_error_response(exc)
    except Exception:
        logger.exception("Export error")
        return jsonify({'error': '导出数据集失败'}), 500
