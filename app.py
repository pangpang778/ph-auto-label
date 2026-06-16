import os
import json
import math
import csv
import time
import uuid
import random
import shutil
import threading
from datetime import datetime
import numpy as np
import base64
import traceback
import cv2
import sys
from urllib.parse import urlparse
from io import StringIO
from flask import Flask, render_template, request, jsonify, send_from_directory, Response
from werkzeug.utils import secure_filename
from flask_cors import CORS
from PIL import Image


from plugins.sam3_service import sam3_service
from plugins.video_inference import (
    video_inference_service,
    list_available_videos,
    resolve_video_path,
    UPLOAD_VIDEO_DIR as VC_UPLOAD_DIR,
)

app = Flask(__name__)
CORS(app)

# 配置
UPLOAD_FOLDER = 'uploads'
STATIC_FOLDER = 'static'
ANNOTATIONS_FOLDER = os.path.join(STATIC_FOLDER, 'annotations')

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['STATIC_FOLDER'] = STATIC_FOLDER
app.config['ANNOTATIONS_FOLDER'] = ANNOTATIONS_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024  # 最大上传2GB

# 创建必要的目录
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(STATIC_FOLDER, exist_ok=True)
os.makedirs(ANNOTATIONS_FOLDER, exist_ok=True)

# 模拟数据库存储标注信息
ANNOTATIONS_FILE = os.path.join(ANNOTATIONS_FOLDER, 'annotations.json')
CLASSES_FILE = os.path.join(ANNOTATIONS_FOLDER, 'classes.json')
TIMELINES_FILE = os.path.join(ANNOTATIONS_FOLDER, 'timelines.json')
SCENARIO_FILE = os.path.join(ANNOTATIONS_FOLDER, 'sop_scenario.json')
TRAIN_JOBS_FILE = os.path.join(ANNOTATIONS_FOLDER, 'train_jobs.json')
MODEL_REGISTRY_FILE = os.path.join(ANNOTATIONS_FOLDER, 'model_registry.json')
ACTIVE_MODEL_FILE = os.path.join(ANNOTATIONS_FOLDER, 'active_model.json')
VIDEO_EXTENSIONS = ('.mp4', '.avi', '.mov', '.mkv', '.webm', '.m4v')

# 初始化注释文件
if not os.path.exists(ANNOTATIONS_FILE):
    with open(ANNOTATIONS_FILE, 'w') as f:
        json.dump({}, f)
        
# 初始化类别文件
if not os.path.exists(CLASSES_FILE):
    # 默认类别
    default_classes = [
        {'name': 'person', 'color': '#3aa757'},
        {'name': 'car', 'color': '#4c9ffd'},
        {'name': 'animal', 'color': '#ff9d00'}
    ]
    with open(CLASSES_FILE, 'w') as f:
        json.dump(default_classes, f)

if not os.path.exists(TIMELINES_FILE):
    with open(TIMELINES_FILE, 'w', encoding='utf-8') as f:
        json.dump({}, f, ensure_ascii=False, indent=2)

if not os.path.exists(SCENARIO_FILE):
    with open(SCENARIO_FILE, 'w', encoding='utf-8') as f:
        json.dump({"scenario_id": "", "name": "", "steps": [], "object_classes": [], "action_labels": []}, f, ensure_ascii=False, indent=2)

if not os.path.exists(TRAIN_JOBS_FILE):
    with open(TRAIN_JOBS_FILE, 'w', encoding='utf-8') as f:
        json.dump([], f, ensure_ascii=False, indent=2)

if not os.path.exists(MODEL_REGISTRY_FILE):
    with open(MODEL_REGISTRY_FILE, 'w', encoding='utf-8') as f:
        json.dump([], f, ensure_ascii=False, indent=2)

if not os.path.exists(ACTIVE_MODEL_FILE):
    with open(ACTIVE_MODEL_FILE, 'w', encoding='utf-8') as f:
        json.dump({"model_id": "", "model_name": "", "model_path": ""}, f, ensure_ascii=False, indent=2)


def read_json_file(path, default):
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8-sig') as f:
                content = f.read().strip()
                return json.loads(content) if content else default
    except Exception as exc:
        print(f"Failed to read JSON {path}: {exc}")
    return default


def write_json_file(path, data):
    temp_path = path + '.tmp'
    with open(temp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)


def color_for_index(index):
    palette = ['#3aa757', '#4c9ffd', '#ff9d00', '#dc3545', '#6f42c1', '#20c997', '#fd7e14', '#17a2b8', '#e83e8c', '#6610f2']
    return palette[index % len(palette)]


def sync_object_classes_to_labels(object_classes, replace=False):
    classes = [] if replace else read_json_file(CLASSES_FILE, [])
    existing = {c.get('name') for c in classes}
    for index, obj in enumerate(object_classes):
        name = obj.get('id') or obj.get('name') if isinstance(obj, dict) else str(obj)
        display_name = obj.get('name') if isinstance(obj, dict) else name
        if name and name not in existing:
            classes.append({'name': name, 'display_name': display_name, 'color': color_for_index(len(classes))})
            existing.add(name)
    write_json_file(CLASSES_FILE, classes)
    return classes


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


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec='seconds') + "Z"


def get_models_install_path() -> str:
    install_path = os.path.join(app.root_path, 'plugins', 'yolo11')
    if not os.path.exists(install_path):
        os.makedirs(install_path, exist_ok=True)
    return install_path


def get_models_dir() -> str:
    models_dir = os.path.join(get_models_install_path(), 'models')
    os.makedirs(models_dir, exist_ok=True)
    return models_dir


def read_train_jobs() -> list[dict]:
    return read_json_file(TRAIN_JOBS_FILE, [])


def write_train_jobs(jobs: list[dict]) -> None:
    write_json_file(TRAIN_JOBS_FILE, jobs)


def upsert_train_job(job: dict) -> None:
    jobs = read_train_jobs()
    for i, old in enumerate(jobs):
        if old.get("id") == job.get("id"):
            jobs[i] = job
            write_train_jobs(jobs)
            return
    jobs.append(job)
    write_train_jobs(jobs)


def read_model_registry() -> list[dict]:
    return read_json_file(MODEL_REGISTRY_FILE, [])


def write_model_registry(models: list[dict]) -> None:
    write_json_file(MODEL_REGISTRY_FILE, models)


def get_active_model() -> dict:
    return read_json_file(ACTIVE_MODEL_FILE, {"model_id": "", "model_name": "", "model_path": ""})


def set_active_model(model_id: str, model_name: str, model_path: str) -> None:
    write_json_file(ACTIVE_MODEL_FILE, {"model_id": model_id, "model_name": model_name, "model_path": model_path, "updated_at": now_iso()})


def training_readiness() -> dict:
    annotations = read_json_file(ANNOTATIONS_FILE, {})
    total_images = 0
    annotated_images = 0
    valid_suffix = ('.png', '.jpg', '.jpeg', '.bmp')
    for name in os.listdir(app.config['UPLOAD_FOLDER']):
        if name.lower().endswith(valid_suffix):
            total_images += 1
            anns = annotations.get(name, [])
            if anns:
                annotated_images += 1
    return {
        "total_images": total_images,
        "annotated_images": annotated_images,
        "min_for_initial": 20,
        "ready_for_initial": annotated_images >= 20,
        "cuda": get_cuda_status(),
    }


def get_cuda_status() -> dict:
    """Return CUDA availability for the current Flask runtime environment."""
    status = {
        "available": False,
        "device_count": 0,
        "device_name": "",
        "torch_version": "",
        "error": "",
    }
    try:
        import torch

        status["torch_version"] = str(getattr(torch, "__version__", ""))
        status["available"] = bool(torch.cuda.is_available())
        status["device_count"] = int(torch.cuda.device_count() if status["available"] else 0)
        if status["available"] and status["device_count"] > 0:
            status["device_name"] = str(torch.cuda.get_device_name(0))
    except Exception as exc:
        status["error"] = str(exc)
    return status


def _annotation_to_bbox(ann: dict, width: int, height: int):
    points = ann.get('points', [])
    if not points:
        return None
    valid_points = []
    if isinstance(points, list):
        if points and isinstance(points[0], dict):
            for p in points:
                if p.get('x') is not None and p.get('y') is not None:
                    valid_points.append([float(p['x']), float(p['y'])])
        else:
            for p in points:
                if isinstance(p, (list, tuple)) and len(p) >= 2 and p[0] is not None and p[1] is not None:
                    valid_points.append([float(p[0]), float(p[1])])
    if not valid_points:
        return None
    arr = np.array(valid_points, dtype=np.float32)
    x_min, y_min = float(np.min(arr[:, 0])), float(np.min(arr[:, 1]))
    x_max, y_max = float(np.max(arr[:, 0])), float(np.max(arr[:, 1]))
    x_min = max(0.0, min(x_min, width))
    y_min = max(0.0, min(y_min, height))
    x_max = max(0.0, min(x_max, width))
    y_max = max(0.0, min(y_max, height))
    bw = x_max - x_min
    bh = y_max - y_min
    if bw <= 1e-6 or bh <= 1e-6:
        return None
    cx = (x_min + x_max) / 2.0 / max(width, 1)
    cy = (y_min + y_max) / 2.0 / max(height, 1)
    nw = bw / max(width, 1)
    nh = bh / max(height, 1)
    return cx, cy, nw, nh


def build_yolo_training_dataset(work_dir: str) -> dict:
    classes = read_json_file(CLASSES_FILE, [])
    class_names = [c.get('name') for c in classes if c.get('name')]
    if not class_names:
        raise RuntimeError("No classes found. Please create labels first.")
    class_to_id = {name: i for i, name in enumerate(class_names)}
    annotations = read_json_file(ANNOTATIONS_FILE, {})

    image_names = [name for name in os.listdir(app.config['UPLOAD_FOLDER']) if name.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp'))]
    annotated = [name for name in image_names if annotations.get(name)]
    if len(annotated) < 20:
        raise RuntimeError("Need at least 20 annotated images before training.")

    random.Random(42).shuffle(annotated)
    n = len(annotated)
    n_train = max(1, int(n * 0.8))
    n_val = max(1, int(n * 0.15))
    train_set = annotated[:n_train]
    val_set = annotated[n_train:n_train + n_val]
    test_set = annotated[n_train + n_val:] or annotated[:1]
    splits = {"train": train_set, "val": val_set, "test": test_set}

    dataset_root = os.path.join(work_dir, "dataset")
    for split in splits.keys():
        os.makedirs(os.path.join(dataset_root, split, "images"), exist_ok=True)
        os.makedirs(os.path.join(dataset_root, split, "labels"), exist_ok=True)

    for split, names in splits.items():
        for image_name in names:
            src = os.path.join(app.config['UPLOAD_FOLDER'], image_name)
            dst = os.path.join(dataset_root, split, "images", image_name)
            shutil.copy2(src, dst)

            label_path = os.path.join(dataset_root, split, "labels", os.path.splitext(image_name)[0] + ".txt")
            with Image.open(src) as img:
                width, height = img.size

            lines = []
            for ann in annotations.get(image_name, []):
                cls_name = ann.get("class")
                if cls_name not in class_to_id:
                    continue
                box = _annotation_to_bbox(ann, width, height)
                if not box:
                    continue
                cx, cy, nw, nh = box
                lines.append(f"{class_to_id[cls_name]} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}")
            with open(label_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))

    data_yaml = os.path.join(dataset_root, "data.yaml")
    with open(data_yaml, "w", encoding="utf-8") as f:
        # Use absolute dataset root to avoid Ultralytics resolving paths against project cwd.
        dataset_root_abs = os.path.abspath(dataset_root).replace("\\", "/")
        f.write(f"path: {dataset_root_abs}\n")
        f.write("train: train/images\n")
        f.write("val: val/images\n")
        f.write("test: test/images\n\n")
        f.write(f"nc: {len(class_names)}\n")
        f.write(f"names: {class_names}\n")

    return {
        "dataset_root": dataset_root,
        "data_yaml": data_yaml,
        "class_names": class_names,
        "total_images": len(image_names),
        "annotated_images": len(annotated),
    }


def _latest_version_tag(models: list[dict]) -> tuple[int, int]:
    major, minor = 0, 0
    for m in models:
        version = str(m.get("version", "")).lstrip("v")
        parts = version.split(".")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            ma, mi = int(parts[0]), int(parts[1])
            if (ma, mi) > (major, minor):
                major, minor = ma, mi
    return major, minor


def next_model_version(mode: str) -> str:
    models = read_model_registry()
    ma, mi = _latest_version_tag(models)
    if ma == 0 and mi == 0:
        return "v1.0"
    if mode == "initial":
        return f"v{ma + 1}.0"
    return f"v{ma}.{mi + 1}"


def _extract_metrics_from_results_csv(results_csv: str) -> dict:
    if not os.path.exists(results_csv):
        return {}
    metrics = {}
    try:
        with open(results_csv, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            return {}
        row = rows[-1]
        for key, value in row.items():
            try:
                metrics[key.strip()] = float(value)
            except Exception:
                continue
    except Exception:
        return {}
    return metrics


def resolve_training_device(requested_device):
    """Resolve train device from user input. Supports 'auto' -> CUDA if available."""
    value = str(requested_device or "auto").strip().lower()
    if value in {"", "auto", "default"}:
        try:
            import torch
            if torch.cuda.is_available():
                return 0
        except Exception:
            pass
        return "cpu"
    return requested_device


def run_training_job(job_id: str) -> None:
    jobs = read_train_jobs()
    job = next((x for x in jobs if x.get("id") == job_id), None)
    if not job:
        return
    job["status"] = "running"
    job["progress"] = 5
    job["message"] = "Preparing training dataset..."
    job["updated_at"] = now_iso()
    upsert_train_job(job)

    try:
        job_dir = os.path.join(app.root_path, "static", "train_work", job_id)
        os.makedirs(job_dir, exist_ok=True)
        dataset = build_yolo_training_dataset(job_dir)
        job["dataset_dir"] = dataset["dataset_root"]
        job["annotated_images"] = dataset["annotated_images"]
        job["total_images"] = dataset["total_images"]
        job["progress"] = 20
        job["message"] = "Dataset prepared. Starting model training..."
        job["updated_at"] = now_iso()
        upsert_train_job(job)

        from ultralytics import YOLO

        base_model = job.get("base_model") or "yolo11n.pt"
        job["resolved_base_model"] = base_model
        train_project = os.path.join(job_dir, "runs")
        model = YOLO(base_model)
        job["progress"] = 45
        job["message"] = "Training in progress..."
        job["updated_at"] = now_iso()
        upsert_train_job(job)

        model.train(
            data=dataset["data_yaml"],
            epochs=int(job.get("epochs", 50)),
            imgsz=int(job.get("imgsz", 640)),
            batch=int(job.get("batch", 8)),
            device=job.get("device", "cpu"),
            workers=0,
            project=train_project,
            name="detector",
            exist_ok=True,
            patience=0,
            pretrained=True,
            verbose=True,
            cache=False,
            amp=False,
        )
        run_dir = os.path.join(train_project, "detector")
        best_path = os.path.join(run_dir, "weights", "best.pt")
        last_path = os.path.join(run_dir, "weights", "last.pt")
        if os.path.exists(best_path):
            trained_model = best_path
        elif os.path.exists(last_path):
            trained_model = last_path
        else:
            raise FileNotFoundError("Training finished but no model artifact found in weights/")

        version = next_model_version(job.get("mode", "incremental"))
        model_filename = f"{version}.pt"
        model_dst = os.path.join(get_models_dir(), model_filename)
        shutil.copy2(trained_model, model_dst)

        results_csv = os.path.join(run_dir, "results.csv")
        metrics = _extract_metrics_from_results_csv(results_csv)
        model_id = f"model_{uuid.uuid4().hex[:10]}"
        model_record = {
            "id": model_id,
            "version": version,
            "name": model_filename,
            "path": model_dst,
            "parent_model": job.get("base_model", ""),
            "mode": job.get("mode", "incremental"),
            "metrics": metrics,
            "created_at": now_iso(),
            "job_id": job_id,
            "status": "candidate",
        }
        models = read_model_registry()
        models.append(model_record)
        write_model_registry(models)

        # auto activate newest successful model
        set_active_model(model_id=model_id, model_name=model_filename, model_path=model_dst)

        job["status"] = "completed"
        job["progress"] = 100
        job["message"] = f"Training completed. Model {model_filename} is ready."
        job["artifact_path"] = model_dst
        job["metrics"] = metrics
        job["model_id"] = model_id
        job["version"] = version
        job["run_dir"] = run_dir
        job["updated_at"] = now_iso()
        upsert_train_job(job)

    except Exception as exc:
        job["status"] = "failed"
        job["progress"] = 100
        job["message"] = f"Training failed: {str(exc)}"
        job["error"] = traceback.format_exc()
        job["updated_at"] = now_iso()
        upsert_train_job(job)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/classes')
def get_classes():
    """获取所有类别"""
    classes = []
    if os.path.exists(CLASSES_FILE):
        with open(CLASSES_FILE, 'r') as f:
            classes = json.load(f)
    return jsonify(classes)


@app.route('/api/classes', methods=['POST'])
def save_classes():
    """保存所有类别"""
    data = request.json
    
    with open(CLASSES_FILE, 'w') as f:
        json.dump(data, f, indent=2)
    
    return jsonify({'message': 'Classes saved successfully'})


@app.route('/api/images')
def get_images():
    """获取所有上传的图片"""
    images = []
    
    # 读取标注信息，用于获取每张图片的标注数量
    annotations = {}
    if os.path.exists(ANNOTATIONS_FILE):
        try:
            with open(ANNOTATIONS_FILE, 'r', encoding='utf-8') as f:
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
        print(f"[DEBUG] 标注文件不存在: {ANNOTATIONS_FILE}")
    
    for filename in os.listdir(app.config['UPLOAD_FOLDER']):
        if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp')):
            # 获取图片尺寸信息
            try:
                image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
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


@app.route('/api/images/delete', methods=['POST'])
def delete_images():
    """删除指定的图片"""
    data = request.json or {}
    image_names = data.get('images', [])
    
    deleted_count = 0
    errors = []
    
    for image_name in image_names:
        try:
            # 删除图片文件
            image_path = os.path.join(app.config['UPLOAD_FOLDER'], image_name)
            if os.path.exists(image_path):
                os.remove(image_path)
                deleted_count += 1
                
                # 同时删除对应的标注信息
                annotations = {}
                if os.path.exists(ANNOTATIONS_FILE):
                    with open(ANNOTATIONS_FILE, 'r') as f:
                        annotations = json.load(f)
                    
                    if image_name in annotations:
                        del annotations[image_name]
                        with open(ANNOTATIONS_FILE, 'w') as f:
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


@app.route('/api/image/<filename>')
def get_image(filename):
    """获取指定图片"""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/api/upload', methods=['POST'])
def upload_folder():
    """上传整个文件夹"""
    if 'files[]' not in request.files:
        return jsonify({'error': 'No files provided'}), 400
    
    files = request.files.getlist('files[]')
    uploaded_files = []
    
    for file in files:
        if file.filename != '':
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename or '')
            file.save(filepath)
            uploaded_files.append(file.filename or '')
    
    return jsonify({'message': 'Files uploaded successfully', 'files': uploaded_files})


@app.route('/api/upload-labelme', methods=['POST'])
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
        if os.path.exists(CLASSES_FILE):
            with open(CLASSES_FILE, 'r') as f:
                classes = json.load(f)
        
        annotations = {}
        if os.path.exists(ANNOTATIONS_FILE):
            with open(ANNOTATIONS_FILE, 'r') as f:
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
            image_path = os.path.join(app.config['UPLOAD_FOLDER'], image_filename)
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
        with open(CLASSES_FILE, 'w') as f:
            json.dump(classes, f, indent=2)
        
        with open(ANNOTATIONS_FILE, 'w') as f:
            json.dump(annotations, f, indent=2)
        
        return jsonify({
            'message': 'LabelMe dataset uploaded successfully', 
            'files': uploaded_files,
            'annotations_processed': processed_annotations
        })
        
    except Exception as e:
        return jsonify({'error': f'Failed to process LabelMe dataset: {str(e)}'}), 500


@app.route('/api/upload/video', methods=['POST'])
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
        temp_video_path = os.path.join(app.config['UPLOAD_FOLDER'], temp_filename)
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
            frame_path = os.path.join(app.config['UPLOAD_FOLDER'], frame_filename)
            
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


@app.route('/api/annotations/<image_name>')
def get_annotations(image_name):
    """获取特定图片的标注"""
    annotations = {}
    if os.path.exists(ANNOTATIONS_FILE):
        try:
            with open(ANNOTATIONS_FILE, 'r', encoding='utf-8') as f:
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


@app.route('/api/annotations/<image_name>', methods=['POST'])
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
    lock_file = ANNOTATIONS_FILE + '.lock'
    lock = filelock.FileLock(lock_file, timeout=10)
    
    try:
        lock_wait_started = time.perf_counter()
        with lock:
            metrics['lock_wait_ms'] = int((time.perf_counter() - lock_wait_started) * 1000)
            # 读取现有标注
            annotations = {}
            if os.path.exists(ANNOTATIONS_FILE):
                try:
                    read_started = time.perf_counter()
                    with open(ANNOTATIONS_FILE, 'r', encoding='utf-8') as f:
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
            if os.path.exists(ANNOTATIONS_FILE):
                backup_file = ANNOTATIONS_FILE + '.bak'
                try:
                    backup_started = time.perf_counter()
                    shutil.copy2(ANNOTATIONS_FILE, backup_file)
                    metrics['backup_ms'] = int((time.perf_counter() - backup_started) * 1000)
                except Exception as e:
                    print(f"备份失败: {e}")
            
            # 更新标注
            annotations[image_name] = data
            
            # 先写入临时文件，成功后再替换原文件
            temp_file = ANNOTATIONS_FILE + '.tmp'
            try:
                write_started = time.perf_counter()
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(annotations, f, indent=2, ensure_ascii=False)
                
                # 验证写入的JSON是否有效
                with open(temp_file, 'r', encoding='utf-8') as f:
                    json.load(f)  # 验证JSON格式
                
                # 替换原文件
                if os.path.exists(ANNOTATIONS_FILE):
                    os.replace(temp_file, ANNOTATIONS_FILE)
                else:
                    os.rename(temp_file, ANNOTATIONS_FILE)
                metrics['write_verify_replace_ms'] = int((time.perf_counter() - write_started) * 1000)
                    
            except Exception as e:
                # 写入失败，删除临时文件
                if os.path.exists(temp_file):
                    os.remove(temp_file)
                print(f"写入标注文件失败: {e}")
                return jsonify({'error': f'保存失败: {str(e)}'}), 500
            
            metrics['total_ms'] = int((time.perf_counter() - req_started) * 1000)
            app.logger.info(
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
        app.logger.warning(
            '[annotations.save.timeout] image=%s waited_ms=%d total_ms=%d',
            image_name,
            int((time.perf_counter() - lock_wait_started) * 1000),
            metrics['total_ms']
        )
        return jsonify({'error': '文件正在被其他操作使用，请稍后重试'}), 503


@app.route('/api/ai-annotate', methods=['POST'])
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
            active = get_active_model()
            model_name = active.get('model_name') or os.path.basename(active.get('model_path', ''))
        if not model_name:
            return jsonify({'error': 'Model not specified'}), 400
        # 构建路径
        if not os.path.isabs(install_path):
            install_path = os.path.join(app.root_path, install_path)
        
        # 构建绝对路径
        image_path = os.path.join(app.root_path, app.config['UPLOAD_FOLDER'], image_name)
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
        if os.path.exists(CLASSES_FILE):
            with open(CLASSES_FILE, 'r') as f:
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
            with open(CLASSES_FILE, 'w') as f:
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


@app.route('/api/ai-annotate-batch', methods=['POST'])
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
            active = get_active_model()
            model_name = active.get('model_name') or os.path.basename(active.get('model_path', ''))
        if not model_name:
            return jsonify({'error': 'Model not specified'}), 400
        
        # 构建路径
        if not os.path.isabs(install_path):
            install_path = os.path.join(app.root_path, install_path)
        
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
            img_path = os.path.join(app.root_path, app.config['UPLOAD_FOLDER'], img_name)
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
        if os.path.exists(CLASSES_FILE):
            with open(CLASSES_FILE, 'r') as f:
                existing_classes = json.load(f)
        
        existing_class_names = {cls['name'] for cls in existing_classes}
        new_classes_added = False
        
        # 读取现有标注
        all_saved_annotations = {}
        if os.path.exists(ANNOTATIONS_FILE):
            with open(ANNOTATIONS_FILE, 'r') as f:
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
        with open(ANNOTATIONS_FILE, 'w') as f:
            json.dump(all_saved_annotations, f, indent=2)
        
        # 保存新增的类别
        if new_classes_added:
            with open(CLASSES_FILE, 'w') as f:
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

    local_classes = read_json_file(CLASSES_FILE, [])
    fallback = []
    for cls in local_classes:
        name = str(cls.get('name') or '').strip()
        if name and name not in fallback:
            fallback.append(name)
    return fallback


@app.route('/api/sam3/status')
def sam3_status():
    """Check SAM3 model availability."""
    model_path = os.environ.get("SAM3_MODEL_PATH", os.path.join(app.root_path, 'plugins', 'sam3', 'models', 'model.pt'))
    return jsonify({
        'loaded': sam3_service.is_loaded,
        'model_path': model_path,
        'model_exists': os.path.isfile(model_path),
    })


@app.route('/api/ai-annotate-sam3', methods=['POST'])
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

        image_path = os.path.abspath(os.path.join(app.root_path, app.config['UPLOAD_FOLDER'], image_name))
        if not os.path.exists(image_path):
            return jsonify({'error': f'图片不存在: {image_name}'}), 400

        annotations = sam3_service.detect_from_file(image_path, text=target_classes, conf=confidence)

        existing_classes = read_json_file(CLASSES_FILE, [])
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
            write_json_file(CLASSES_FILE, existing_classes)

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


@app.route('/api/ai-annotate-sam3-batch', methods=['POST'])
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
            img_path = os.path.abspath(os.path.join(app.root_path, app.config['UPLOAD_FOLDER'], img_name))
            if os.path.exists(img_path):
                image_paths.append(img_path)
                valid_image_names.append(img_name)
        if not image_paths:
            return jsonify({'error': '没有有效的图片'}), 400

        all_results = sam3_service.detect_batch_from_files(image_paths, text=target_classes, conf=confidence)

        existing_classes = read_json_file(CLASSES_FILE, [])
        annotations = read_json_file(ANNOTATIONS_FILE, {})

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

        write_json_file(ANNOTATIONS_FILE, annotations)
        write_json_file(CLASSES_FILE, existing_classes)

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


@app.route('/api/check-yolo11-install')
def check_yolo11_install():
    """检查YOLO11安装状态"""
    import os
    # 检查YOLO11安装路径是否存在
    yolo11_path = os.path.join(app.root_path, 'plugins', 'yolo11')
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


@app.route('/api/download-models')
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
        install_path = os.path.join(app.root_path, install_path)

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
@app.route('/api/list-models')
def list_models():
    """获取已安装的YOLO11模型列表"""
    import os
    
    # 获取安装路径
    install_path = request.args.get('install_path', 'plugins/yolo11')
    # 确保安装路径是相对于项目根目录的
    if not os.path.isabs(install_path):
        install_path = os.path.join(app.root_path, install_path)
    
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


@app.route('/api/upload-model', methods=['POST'])
def upload_model():
    """上传YOLO11模型文件"""
    import os
    
    # 获取安装路径
    install_path = request.headers.get('X-Install-Path', 'plugins/yolo11')
    # 确保安装路径是相对于项目根目录的
    if not os.path.isabs(install_path):
        install_path = os.path.join(app.root_path, install_path)
    
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


@app.route('/api/delete-model', methods=['POST'])
def delete_model():
    """删除YOLO11模型文件"""
    import os
    
    # 获取安装路径
    install_path = request.headers.get('X-Install-Path', 'plugins/yolo11')
    # 确保安装路径是相对于项目根目录的
    if not os.path.isabs(install_path):
        install_path = os.path.join(app.root_path, install_path)
    
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


@app.route('/api/export', methods=['POST'])
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
        if os.path.exists(CLASSES_FILE):
            with open(CLASSES_FILE, 'r') as f:
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
        for filename in os.listdir(app.config['UPLOAD_FOLDER']):
            if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp')):
                images.append(filename)
        
        # 根据样本选择参数过滤图片
        annotations = {}
        if os.path.exists(ANNOTATIONS_FILE):
            with open(ANNOTATIONS_FILE, 'r') as f:
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
                src_img_path = os.path.join(app.config['UPLOAD_FOLDER'], image_name)
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


@app.route('/api/videos')
def list_videos():
    """List uploaded videos that can be used for SOP timeline annotation."""
    videos = []
    for filename in os.listdir(app.config['UPLOAD_FOLDER']):
        if filename.lower().endswith(VIDEO_EXTENSIONS):
            path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            videos.append({
                'name': filename,
                'size': os.path.getsize(path),
                'url': f'/api/video/{filename}',
            })
    videos.sort(key=lambda x: x['name'].lower())
    return jsonify({'videos': videos})


@app.route('/api/video/<path:filename>')
def get_video(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/api/upload/timeline-video', methods=['POST'])
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
    while os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], filename)):
        filename = f"{base}_{index}{ext}"
        index += 1
    path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    video_file.save(path)
    return jsonify({'message': 'Timeline video uploaded', 'video_name': filename, 'url': f'/api/video/{filename}'})


@app.route('/api/scenario')
def get_sop_scenario():
    return jsonify(read_json_file(SCENARIO_FILE, {'scenario_id': '', 'name': '', 'steps': [], 'object_classes': [], 'action_labels': []}))


@app.route('/api/scenario', methods=['POST'])
def save_sop_scenario():
    scenario = request.json or {}
    scenario.setdefault('steps', [])
    scenario.setdefault('object_classes', [])
    scenario.setdefault('action_labels', [])
    write_json_file(SCENARIO_FILE, scenario)
    if scenario.get('object_classes'):
        sync_object_classes_to_labels(scenario.get('object_classes', []), replace=bool(scenario.get('replace_classes')))
    return jsonify({'message': 'Scenario saved', 'scenario': scenario})


@app.route('/api/scenario/import', methods=['POST'])
def import_sop_scenario():
    """Import SOP steps/classes from universal_sop_platform scenario package."""
    data = request.json or {}
    scenario_path = data.get('scenario_path') or data.get('path') or ''
    if not scenario_path:
        return jsonify({'error': 'scenario_path is required'}), 400
    try:
        scenario = parse_sop_scenario(scenario_path)
        write_json_file(SCENARIO_FILE, scenario)
        classes = sync_object_classes_to_labels(scenario.get('object_classes', []), replace=bool(data.get('replace_classes', True)))
        return jsonify({'message': 'Scenario imported', 'scenario': scenario, 'classes': classes})
    except Exception as exc:
        traceback.print_exc()
        return jsonify({'error': str(exc)}), 400


@app.route('/api/timelines')
def list_timelines():
    return jsonify(read_json_file(TIMELINES_FILE, {}))


@app.route('/api/timelines/<path:video_name>')
def get_timeline(video_name):
    timelines = read_json_file(TIMELINES_FILE, {})
    return jsonify(timelines.get(video_name, []))


@app.route('/api/timelines/<path:video_name>', methods=['POST'])
def save_timeline(video_name):
    payload = request.json or {}
    raw_segments = payload if isinstance(payload, list) else payload.get('segments', [])
    segments = [normalize_timeline_segment(seg, video_name) for seg in raw_segments]
    segments.sort(key=lambda x: (x['start_sec'], x['end_sec'], x['step_id']))
    timelines = read_json_file(TIMELINES_FILE, {})
    timelines[video_name] = segments
    write_json_file(TIMELINES_FILE, timelines)
    return jsonify({'message': 'Timeline saved', 'video_name': video_name, 'segments': segments, 'count': len(segments)})


@app.route('/api/export-timeline')
def export_timeline_csv():
    """Export all SOP action segments as universal_sop_platform timeline CSV."""
    timelines = read_json_file(TIMELINES_FILE, {})
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


@app.route('/api/train/readiness')
def train_readiness():
    return jsonify(training_readiness())


@app.route('/api/train/jobs')
def train_jobs():
    jobs = read_train_jobs()
    jobs.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return jsonify({"jobs": jobs})


@app.route('/api/train/jobs/<job_id>')
def train_job_detail(job_id):
    jobs = read_train_jobs()
    job = next((x for x in jobs if x.get("id") == job_id), None)
    if not job:
        return jsonify({"error": "job not found"}), 404
    return jsonify(job)


@app.route('/api/train/start', methods=['POST'])
def train_start():
    payload = request.json or {}
    mode = str(payload.get("mode", "initial")).strip().lower()
    if mode not in {"initial", "incremental"}:
        mode = "incremental"

    readiness = training_readiness()
    if mode == "initial" and not readiness.get("ready_for_initial"):
        return jsonify({"error": f"Need at least {readiness['min_for_initial']} annotated images for initial training", "readiness": readiness}), 400

    active = get_active_model()
    base_model = payload.get("base_model")
    if not base_model:
        if mode == "incremental" and active.get("model_path") and os.path.exists(active.get("model_path")):
            base_model = active.get("model_path")
        else:
            base_model = "yolo11n.pt"

    job_id = f"train_{uuid.uuid4().hex[:10]}"
    job = {
        "id": job_id,
        "mode": mode,
        "status": "queued",
        "progress": 0,
        "message": "Queued",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "base_model": base_model,
        "epochs": int(payload.get("epochs", 30)),
        "imgsz": int(payload.get("imgsz", 640)),
        "batch": int(payload.get("batch", 8)),
        "device": resolve_training_device(payload.get("device", "auto")),
        "annotated_images": readiness["annotated_images"],
        "total_images": readiness["total_images"],
    }
    upsert_train_job(job)

    t = threading.Thread(target=run_training_job, args=(job_id,), daemon=True)
    t.start()
    return jsonify({"message": "training started", "job": job})


@app.route('/api/models/registry')
def models_registry():
    models = read_model_registry()
    models.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return jsonify({"models": models})


@app.route('/api/models/active')
def models_active():
    return jsonify(get_active_model())


@app.route('/api/models/<model_id>/activate', methods=['POST'])
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


# =================== 视频AI对比测试 ===================


def _parse_classes(raw) -> list:
    """把 SAM3 目标类别从 list/str 解析成干净的字符串列表。"""
    if isinstance(raw, list):
        return [str(c).strip() for c in raw if str(c).strip()]
    if isinstance(raw, str):
        s = raw.replace('，', ',').replace('\n', ',').replace(';', ',')
        return [c.strip() for c in s.split(',') if c.strip()]
    return []


@app.route('/video-test')
def video_test_page():
    """视频AI对比测试独立页面。"""
    return render_template('video_test.html')


@app.route('/api/video-test/videos')
def video_test_videos():
    """列出可选视频（默认素材 + 上传）。"""
    return jsonify({'videos': list_available_videos()})


@app.route('/api/video-test/video/<path:name>')
def video_test_serve(name):
    """服务原视频文件。"""
    path = resolve_video_path(name)
    if not path:
        return jsonify({'error': '视频不存在'}), 404
    return send_from_directory(os.path.dirname(path), os.path.basename(path))


@app.route('/api/video-test/upload', methods=['POST'])
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
    while os.path.exists(os.path.join(VC_UPLOAD_DIR, name)):
        name = f"{base}_{i}{ext}"
        i += 1
    f.save(os.path.join(VC_UPLOAD_DIR, name))
    return jsonify({'message': '上传成功', 'name': name, 'url': f'/api/video-test/video/{name}'})


@app.route('/api/video-test/yolo-models')
def video_test_yolo_models():
    """YOLO 模型下拉：预训练 + 项目已训练模型。"""
    models = [{'name': 'yolo11n.pt (COCO 80类 预训练)', 'value': 'yolo11n.pt'}]
    try:
        md = get_models_dir()
        for fn in sorted(os.listdir(md)):
            if fn.endswith('.pt'):
                models.append({'name': f'{fn} (已训练)', 'value': os.path.join(md, fn)})
    except Exception:
        pass
    active = get_active_model()
    preferred = active.get('model_path', '')
    return jsonify({'models': models, 'active': preferred})


@app.route('/api/video-test/start', methods=['POST'])
def video_test_start():
    """启动视频推理任务。"""
    data = request.json or {}
    name = (data.get('video_name') or '').strip()
    engine = (data.get('engine') or 'yolo').strip().lower()
    try:
        target_fps = int(data.get('target_fps', 2))
    except (TypeError, ValueError):
        target_fps = 2
    try:
        conf = float(data.get('confidence', 0.35))
    except (TypeError, ValueError):
        conf = 0.35

    if engine not in ('yolo', 'sam3'):
        return jsonify({'error': '引擎必须是 yolo 或 sam3'}), 400
    if target_fps not in (1, 2, 5):
        return jsonify({'error': '帧率仅支持 1/2/5'}), 400

    path = resolve_video_path(name)
    if not path:
        return jsonify({'error': f'视频不存在: {name}'}), 400

    if engine == 'sam3':
        if not sam3_service.is_loaded:
            return jsonify({'error': 'SAM3 模型未加载，请先确认模型已就绪'}), 503
        classes = _parse_classes(data.get('classes'))
        if not classes:
            return jsonify({'error': 'SAM3 需要填写目标类别(text)，如 person,car'}), 400
        job = video_inference_service.start_job(
            path, 'sam3', classes=classes, target_fps=target_fps, conf=conf)
    else:
        model_path = data.get('model') or 'yolo11n.pt'
        job = video_inference_service.start_job(
            path, 'yolo', model_path=model_path, target_fps=target_fps, conf=conf)

    return jsonify({'job_id': job['id'], 'status': job['status']})


@app.route('/api/video-test/stream/<job_id>')
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


@app.route('/api/video-test/job/<job_id>')
def video_test_job(job_id):
    job = video_inference_service.get_job(job_id)
    if not job:
        return jsonify({'error': '任务不存在'}), 404
    return jsonify(job)


@app.route('/api/video-test/stream/start', methods=['POST'])
def video_test_stream_start():
    """启动流式 MJPEG 推理会话（边算边播）。"""
    data = request.json or {}
    name = (data.get('video_name') or '').strip()
    engine = (data.get('engine') or 'yolo').strip().lower()
    try:
        target_fps = int(data.get('target_fps', 2))
    except (TypeError, ValueError):
        target_fps = 2
    try:
        conf = float(data.get('confidence', 0.35))
    except (TypeError, ValueError):
        conf = 0.35

    if engine not in ('yolo', 'sam3'):
        return jsonify({'error': '引擎必须是 yolo 或 sam3'}), 400
    if target_fps not in (1, 2, 5):
        return jsonify({'error': '帧率仅支持 1/2/5'}), 400

    path = resolve_video_path(name)
    if not path:
        return jsonify({'error': f'视频不存在: {name}'}), 400

    if engine == 'sam3':
        if not sam3_service.is_loaded:
            return jsonify({'error': 'SAM3 模型未加载，请先确认模型已就绪'}), 503
        classes = _parse_classes(data.get('classes'))
        if not classes:
            return jsonify({'error': 'SAM3 需要填写目标类别(text)，如 person,car'}), 400
        res = video_inference_service.start_stream_session(
            path, 'sam3', classes=classes, target_fps=target_fps, conf=conf)
    else:
        model_path = data.get('model') or 'yolo11n.pt'
        res = video_inference_service.start_stream_session(
            path, 'yolo', model_path=model_path, target_fps=target_fps, conf=conf)

    return jsonify(res)


@app.route('/api/video-test/stream/frames/<sid>')
def video_test_stream_frames(sid):
    """MJPEG 流：原帧+AI帧水平拼接，边算边播。"""
    return Response(
        video_inference_service.stream_mjpeg(sid),
        mimetype='multipart/x-mixed-replace; boundary=frame',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@app.route('/api/video-test/stream/status/<sid>')
def video_test_stream_status(sid):
    s = video_inference_service.get_session(sid)
    if not s:
        return jsonify({'error': '会话不存在'}), 404
    return jsonify(s)


@app.route('/api/video-test/stream/stop/<sid>', methods=['POST'])
def video_test_stream_stop(sid):
    return jsonify({'stopped': video_inference_service.stop_session(sid)})


if __name__ == '__main__':
    try:
        sam3_service.load_model()
        sam3_service.warmup()
    except Exception as e:
        print(f"[WARNING] SAM3 model failed to load: {e}")
        print("SAM3 auto-annotation will not be available. Set SAM3_MODEL_PATH env var or place model at plugins/sam3/models/model.pt")
    app.run(debug=True, host='0.0.0.0', port=5000)
