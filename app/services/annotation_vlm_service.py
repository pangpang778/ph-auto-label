"""VLM 大模型自动标注服务（镜像 annotation_sam3_service 的契约与持久化策略）。

单张/批量：文本提示 -> vlm_service.grounding -> 平台标注格式（auto=True），
进 annotations.json 后被现有训练闭环直接消费（VLM 打标喂训练）。
"""
import filelock
import logging
import os
from concurrent.futures import ThreadPoolExecutor

from plugins.vlm_service import vlm_service

from app.common.config import PATHS
from app.common.path_safety import PathSafetyError, resolve_contained_path
from app.repositories.annotation_repo import update_annotations
logger = logging.getLogger(__name__)

from app.services.annotation_service import (
    AnnotationError,
    assign_class_colors_and_ids,
    parse_target_classes,
    update_classes,
)

_MISSING = '请至少配置一个目标类别（逗号分隔）'


def _env_batch_workers():
    try:
        return int(os.environ.get("VLM_BATCH_WORKERS", "4"))
    except ValueError:
        logger.warning("invalid VLM_BATCH_WORKERS value; using default 4")
        return 4


_VLM_BATCH_WORKERS = _env_batch_workers()


def _resolve_upload_image(name):
    """uploads 图片名 -> 绝对路径；拒绝越界/绝对注入路径（防目录穿越）。"""
    try:
        return resolve_contained_path(PATHS['uploads'], name)
    except PathSafetyError:
        raise AnnotationError(400, f'非法图片名: {name}')


def run_vlm_single(data):
    image_name = data.get('image_name', '')
    confidence = float(data.get('confidence', 0.35))
    target_classes = parse_target_classes(data.get('target_classes') or data.get('world_classes'))

    if not image_name:
        raise AnnotationError(400, '未指定图片')
    if not target_classes:
        raise AnnotationError(400, _MISSING)
    # 按用户所选模型自动启动对应后端容器（显存腾挪），再检查就绪
    vlm_service.ensure_backend((data.get('model') or '').strip() or None)
    if not vlm_service.is_available():
        raise AnnotationError(503, 'VLM 服务未就绪')

    image_path = _resolve_upload_image(image_name)
    if not os.path.exists(image_path):
        raise AnnotationError(400, f'图片不存在: {image_name}')

    raw = vlm_service.detect_from_file(image_path, text=target_classes, conf=confidence,
                                       model=data.get('model') or None)
    annotations = _to_platform_annotations(raw)
    new_classes_added = _merge_classes_for(annotations)

    return {
        'success': True,
        'annotations': annotations,
        'new_classes_added': bool(new_classes_added),
        'engine': 'vlm',
        'target_classes': target_classes,
    }


def run_vlm_batch(data):
    image_names = data.get('image_names', [])
    confidence = float(data.get('confidence', 0.35))
    target_classes = parse_target_classes(
        data.get('target_classes') or data.get('world_classes'))

    if not image_names:
        raise AnnotationError(400, '未指定图片')
    if not target_classes:
        raise AnnotationError(400, _MISSING)
    # 按用户所选模型自动启动对应后端容器（显存腾挪），再检查就绪
    vlm_service.ensure_backend((data.get('model') or '').strip() or None)
    if not vlm_service.is_available():
        raise AnnotationError(503, 'VLM 服务未就绪（docker compose -f docker-compose.vlm.yml up -d）')
    image_paths, valid_image_names = _collect_valid_image_paths(image_names)
    if not image_paths:
        raise AnnotationError(400, '没有有效的图片')

    def _label_one(item):
        name, path = item
        err = ""
        for attempt in range(2):
            try:
                raw = vlm_service.detect_from_file(path, text=target_classes, conf=confidence,
                                                   model=data.get('model') or None)
                return name, _to_platform_annotations(raw), ""
            except Exception as exc:
                err = str(exc)
                logger.warning('VLM label failed(%s) %s: %s',
                               'retry' if attempt == 0 else 'final', name, exc)
        return name, [], err

    workers = min(_VLM_BATCH_WORKERS, len(image_paths))
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # map keeps input order so persisted results are deterministic
            per_image = list(pool.map(_label_one, zip(valid_image_names, image_paths)))
    else:
        per_image = [_label_one(t) for t in zip(valid_image_names, image_paths)]
    return _persist_batch(per_image, target_classes)


def _to_platform_annotations(raw_boxes):
    """[{class,conf,xyxy}] -> 平台标注 dict（rectangle 四点，auto=True）。"""
    out = []
    for b in raw_boxes:
        x1, y1, x2, y2 = b["xyxy"]
        out.append({
            "class": str(b["class"]),
            "confidence": float(b.get("conf", 0.8)),
            "points": [[float(x1), float(y1)], [float(x2), float(y1)],
                       [float(x2), float(y2)], [float(x1), float(y2)]],
            "type": "rectangle",
            "auto": True,
        })
    return out


def _merge_classes_for(annotations):
    def _mutate(current_classes):
        added = assign_class_colors_and_ids(annotations, current_classes, id_suffix="")
        return (current_classes if added else None, added)
    try:
        return update_classes(_mutate, timeout=10)
    except filelock.Timeout:
        raise AnnotationError(503, '文件正在被其他操作使用，请稍后重试')


def _collect_valid_image_paths(image_names):
    paths, names = [], []
    for n in image_names:
        try:
            p = _resolve_upload_image(n)
        except AnnotationError:
            continue
        if os.path.exists(p):
            paths.append(p)
            names.append(n)
    return paths, names


def _persist_batch(per_image, target_classes):
    """颜色/类别合并 + annotations RMW + 响应组装（与 sam3 批量同策略：
    替换 auto 标注、保留手工标注）。"""
    def _classes_mutate(current_classes):
        added_count = 0
        for _, anns, _err in per_image:
            before = len(current_classes)
            assign_class_colors_and_ids(anns, current_classes, id_suffix="")
            added_count += len(current_classes) - before
        return (current_classes if added_count else None, added_count)

    try:
        new_class_count = update_classes(_classes_mutate, timeout=10) or 0
    except filelock.Timeout:
        raise AnnotationError(503, '文件正在被其他操作使用，请稍后重试')

    total_detected = 0

    def _ann_mutate(current):
        nonlocal total_detected
        for image_name, image_annotations, _err in per_image:
            if not image_annotations:
                continue
            existing = current.get(image_name, [])
            current[image_name] = [a for a in existing if not a.get('auto')] + image_annotations
            total_detected += len(image_annotations)
        return current, None

    try:
        update_annotations(_ann_mutate, timeout=10)
    except filelock.Timeout:
        raise AnnotationError(503, '文件正在被其他操作使用，请稍后重试')

    return {
        'success': True,
        'results': [{'image_name': n, 'success': not err, 'error': err,
                     'count': len(a), 'annotations': a} for n, a, err in per_image],
        'total_processed': len(per_image),
        'total_failed': sum(1 for *_x, err in per_image if err),
        'new_classes_added': int(new_class_count),
        'engine': 'vlm',
        'target_classes': target_classes,
    }
