"""Evaluation blueprint: model evaluation + comparison API + /evaluation page."""
from flask import Blueprint, Response, jsonify, render_template, request

from app.services import evaluation_service
from app.services.evaluation_service import EvaluationBusyError

bp = Blueprint("evaluation", __name__)


@bp.route('/api/models/<model_id>/evaluate', methods=['POST'])
def model_evaluate(model_id):
    try:
        record = evaluation_service.start_evaluation(model_id)
    except EvaluationBusyError as exc:
        return jsonify({"error": str(exc), "status": "busy"}), 409
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify(record)


@bp.route('/api/evaluations/<record_id>')
def evaluation_detail(record_id):
    record = evaluation_service.get_evaluation(record_id)
    if record is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(record)


@bp.route('/api/evaluations')
def evaluations_list():
    model_id = request.args.get("model_id")
    return jsonify({"records": evaluation_service.list_evaluations(model_id)})


@bp.route('/api/evaluations/compare')
def evaluations_compare():
    ids_str = request.args.get("ids", "")
    ids = [s for s in ids_str.split(",") if s.strip()]
    return jsonify(evaluation_service.build_comparison(ids))


@bp.route('/api/evaluations/<record_id>/export')
def evaluation_export(record_id):
    fmt = request.args.get("format", "json")
    data, mimetype, filename = evaluation_service.export_evaluation([record_id], fmt)
    return Response(data, mimetype=mimetype, headers={"Content-Disposition": f"attachment; filename={filename}"})


@bp.route('/evaluation')
def evaluation_page():
    return render_template("evaluation.html")
