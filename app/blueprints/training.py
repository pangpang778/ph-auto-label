"""Training blueprint: /api/train/* routes."""
import os
import threading

from flask import Blueprint, current_app, jsonify, request, send_file

from app.services import models_service
from app.services.training_service import (
    _artifact_allowed_roots,
    build_split_summary,
    build_train_job,
    delete_split_profile,
    get_train_job,
    insert_train_job_if_idle,
    list_train_jobs,
    load_split_profile,
    resolve_training_device,
    run_training_job,
    save_split_profile,
    training_readiness,
)
from training_artifacts import (
    ARTIFACT_CONTENT_TYPES,
    list_native_images,
    read_log_tail,
    read_training_metrics_series,
    resolve_artifact,
    resolve_native_image,
)

bp = Blueprint("training", __name__)


@bp.route('/api/train/readiness')
def train_readiness():
    return jsonify(training_readiness())


@bp.route('/api/train/split')
def train_split_get():
    profile_id = request.args.get("profile_id", "default")
    split_config = load_split_profile(profile_id) or {"profile_id": profile_id}
    return jsonify(build_split_summary(split_config))


@bp.route('/api/train/split', methods=['POST'])
def train_split_save():
    payload = request.json or {}
    try:
        return jsonify(save_split_profile(payload.get("split_config") or payload))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.route('/api/train/split/reset', methods=['POST'])
def train_split_reset():
    payload = request.json or {}
    profile_id = str(payload.get("profile_id") or "default")
    delete_split_profile(profile_id)
    return jsonify(build_split_summary({"profile_id": profile_id}))


@bp.route('/api/train/jobs')
def train_jobs():
    jobs = list_train_jobs()
    jobs.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return jsonify({"jobs": jobs})


@bp.route('/api/train/jobs/<job_id>')
def train_job_detail(job_id):
    job = get_train_job(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    return jsonify(job)


def _get_train_job_or_404(job_id: str):
    job = get_train_job(job_id)
    if not job:
        return None, (jsonify({"error": "job not found"}), 404)
    return job, None


@bp.route('/api/train/jobs/<job_id>/logs')
def train_job_logs(job_id):
    job, error = _get_train_job_or_404(job_id)
    if error:
        return error
    try:
        max_lines = int(request.args.get("tail", 200))
        path = resolve_artifact(job, "log", _artifact_allowed_roots())
    except FileNotFoundError:
        return jsonify({"error": "artifact not found"}), 404
    except PermissionError:
        return jsonify({"error": "artifact is not allowed"}), 403
    return jsonify({"job_id": job_id, "log": read_log_tail(path, max_lines), "tail": max_lines})


@bp.route('/api/train/jobs/<job_id>/metrics')
def train_job_metrics(job_id):
    job, error = _get_train_job_or_404(job_id)
    if error:
        return error
    try:
        path = resolve_artifact(job, "results_csv", _artifact_allowed_roots())
    except FileNotFoundError:
        return jsonify({"error": "artifact not found"}), 404
    except PermissionError:
        return jsonify({"error": "artifact is not allowed"}), 403
    return jsonify(read_training_metrics_series(path))


@bp.route('/api/train/jobs/<job_id>/results-image')
def train_job_results_image(job_id):
    job, error = _get_train_job_or_404(job_id)
    if error:
        return error
    try:
        path = resolve_artifact(job, "results_png", _artifact_allowed_roots())
    except FileNotFoundError:
        return jsonify({"error": "artifact not found"}), 404
    except (PermissionError, ValueError):
        return jsonify({"error": "artifact is not allowed"}), 403
    return send_file(path, mimetype="image/png")


@bp.route('/api/train/jobs/<job_id>/native-images')
def train_job_native_images(job_id):
    job, error = _get_train_job_or_404(job_id)
    if error:
        return error
    return jsonify({"job_id": job_id, "images": list_native_images(job, _artifact_allowed_roots())})


@bp.route('/api/train/jobs/<job_id>/native-images/<image_name>')
def train_job_native_image(job_id, image_name):
    job, error = _get_train_job_or_404(job_id)
    if error:
        return error
    try:
        path = resolve_native_image(job, image_name, _artifact_allowed_roots())
    except ValueError:
        return jsonify({"error": "unsupported image"}), 400
    except FileNotFoundError:
        return jsonify({"error": "image not found"}), 404
    except PermissionError:
        return jsonify({"error": "image is not allowed"}), 403
    mimetype = "image/jpeg" if image_name.lower().endswith((".jpg", ".jpeg")) else "image/png"
    return send_file(path, mimetype=mimetype)


@bp.route('/api/train/jobs/<job_id>/download/<artifact>')
def train_job_download(job_id, artifact):
    job, error = _get_train_job_or_404(job_id)
    if error:
        return error
    try:
        path = resolve_artifact(job, artifact, _artifact_allowed_roots())
    except ValueError:
        return jsonify({"error": "unsupported artifact"}), 400
    except FileNotFoundError:
        return jsonify({"error": "artifact not found"}), 404
    except PermissionError:
        return jsonify({"error": "artifact is not allowed"}), 403
    return send_file(
        path,
        mimetype=ARTIFACT_CONTENT_TYPES.get(artifact, "application/octet-stream"),
        as_attachment=True,
        download_name=os.path.basename(path),
    )


@bp.route('/api/train/start', methods=['POST'])
def train_start():
    payload = request.json or {}
    mode = str(payload.get("mode", "initial")).strip().lower()
    if mode not in {"initial", "incremental"}:
        mode = "incremental"

    # 深度蒸馏/伪标签不走检测标注就绪门槛（工单 05：同一队列，不同前置条件）
    task_type = str(payload.get("task_type") or "detect").strip().lower()
    if task_type not in {"detect", "pseudo", "depth"}:
        return jsonify({"error": "task_type 必须是 detect/pseudo/depth"}), 400

    readiness = training_readiness()
    if task_type == "detect" and mode == "initial" and not readiness.get("ready_for_initial"):
        return jsonify({"error": f"Need at least {readiness['min_for_initial']} annotated images for initial training", "readiness": readiness}), 400

    active = models_service.get_active_model()
    try:
        job = build_train_job(payload, mode, readiness, active)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    # H5: atomic check-and-insert closes the TOCTOU where two concurrent
    # POST /api/train/start both observed "no running job" and each launched a
    # training thread (GPU OOM / artifact interleaving). Only "running" is
    # blocked (not "queued") so the back-to-back test flow with a no-op worker
    # still works.
    if not insert_train_job_if_idle(job):
        return jsonify({"error": "已有训练任务在运行或排队中", "status": "busy"}), 409

    t = threading.Thread(target=run_training_job, args=(job["id"], current_app.root_path), daemon=True)
    t.start()
    return jsonify({"message": "training started", "job": job})
