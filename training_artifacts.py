import csv
import os
from collections import deque
from typing import Any

SAFE_ARTIFACT_KINDS = {"model", "results_csv", "results_png", "log"}
ARTIFACT_CONTENT_TYPES = {
    "model": "application/octet-stream",
    "results_csv": "text/csv; charset=utf-8",
    "results_png": "image/png",
    "log": "text/plain; charset=utf-8",
}

NATIVE_IMAGE_TITLES = {
    "results.png": "训练总览图",
    "confusion_matrix.png": "混淆矩阵",
    "confusion_matrix_normalized.png": "归一化混淆矩阵",
    "BoxPR_curve.png": "PR 曲线",
    "BoxP_curve.png": "Precision 曲线",
    "BoxR_curve.png": "Recall 曲线",
    "BoxF1_curve.png": "F1 曲线",
    "labels.jpg": "标签分布",
}
NATIVE_IMAGE_ORDER = [
    "results.png",
    "confusion_matrix.png",
    "confusion_matrix_normalized.png",
    "BoxPR_curve.png",
    "BoxP_curve.png",
    "BoxR_curve.png",
    "BoxF1_curve.png",
    "labels.jpg",
]


def _recorded_artifact_path(record: dict[str, Any], kind: str) -> str:
    if kind == "model":
        return str(record.get("artifact_path") or record.get("weights_path") or record.get("path") or "")
    run_dir = str(record.get("run_dir") or "")
    if kind == "results_csv":
        return str(record.get("results_csv") or (os.path.join(run_dir, "results.csv") if run_dir else ""))
    if kind == "results_png":
        return str(record.get("results_png") or (os.path.join(run_dir, "results.png") if run_dir else ""))
    if kind == "log":
        return str(record.get("log_path") or "")
    return ""


def _is_under_root(root: str, path: str) -> bool:
    try:
        return os.path.commonpath([root, path]) == root
    except ValueError as exc:
        raise PermissionError("artifact outside allowed roots") from exc


def _has_symlink_component(path: str, allowed_roots: list[str]) -> bool:
    abs_path = os.path.abspath(path)
    roots = [os.path.abspath(root) for root in allowed_roots]
    base_root = next((root for root in roots if _is_under_root(root, abs_path)), "")
    if not base_root:
        return True

    rel_path = os.path.relpath(abs_path, base_root)
    current = base_root
    for part in rel_path.split(os.sep):
        current = os.path.join(current, part)
        if os.path.islink(current):
            return True
    return False


def resolve_artifact(record: dict[str, Any], kind: str, allowed_roots: list[str]) -> str:
    if kind not in SAFE_ARTIFACT_KINDS:
        raise ValueError("unsupported artifact kind")

    raw_path = _recorded_artifact_path(record, kind)
    if not raw_path:
        raise FileNotFoundError("artifact not recorded")

    real_path = os.path.realpath(raw_path)
    real_roots = [os.path.realpath(root) for root in allowed_roots]
    try:
        if not any(os.path.commonpath([root, real_path]) == root for root in real_roots):
            raise PermissionError("artifact outside allowed roots")
    except ValueError as exc:
        raise PermissionError("artifact outside allowed roots") from exc
    if _has_symlink_component(raw_path, allowed_roots):
        raise PermissionError("symlink artifacts are not allowed")
    if not os.path.isfile(real_path):
        raise FileNotFoundError("artifact missing")
    return real_path


def resolve_native_image(record: dict[str, Any], image_name: str, allowed_roots: list[str]) -> str:
    if os.path.basename(image_name) != image_name:
        raise PermissionError("native image outside run directory")
    run_dir = str(record.get("run_dir") or "")
    if not run_dir:
        raise FileNotFoundError("run directory not recorded")
    if not image_name.lower().endswith((".png", ".jpg", ".jpeg")):
        raise ValueError("unsupported native image")
    return resolve_path_under_roots(os.path.join(run_dir, image_name), allowed_roots, must_be_file=True)


def resolve_path_under_roots(path: str, allowed_roots: list[str], must_be_file: bool = False) -> str:
    real_path = os.path.realpath(path)
    real_roots = [os.path.realpath(root) for root in allowed_roots]
    try:
        if not any(os.path.commonpath([root, real_path]) == root for root in real_roots):
            raise PermissionError("artifact outside allowed roots")
    except ValueError as exc:
        raise PermissionError("artifact outside allowed roots") from exc
    if _has_symlink_component(path, allowed_roots):
        raise PermissionError("symlink artifacts are not allowed")
    if must_be_file and not os.path.isfile(real_path):
        raise FileNotFoundError("artifact missing")
    if not must_be_file and not os.path.exists(real_path):
        raise FileNotFoundError("artifact missing")
    return real_path


def list_native_images(record: dict[str, Any], allowed_roots: list[str]) -> list[dict[str, str]]:
    run_dir = str(record.get("run_dir") or "")
    if not run_dir:
        return []
    try:
        safe_run_dir = resolve_path_under_roots(run_dir, allowed_roots)
    except FileNotFoundError:
        return []
    if not os.path.isdir(safe_run_dir):
        return []

    names = [name for name in os.listdir(safe_run_dir) if name.lower().endswith((".png", ".jpg", ".jpeg"))]
    order = {name: index for index, name in enumerate(NATIVE_IMAGE_ORDER)}

    def sort_key(name: str) -> tuple[int, str]:
        if name.startswith("train_batch"):
            group = 100
        elif name.startswith("val_batch"):
            group = 110
        else:
            group = order.get(name, 90)
        return group, name

    images = []
    for name in sorted(names, key=sort_key):
        title = NATIVE_IMAGE_TITLES.get(name)
        if title is None:
            if name.startswith("train_batch"):
                title = "训练样本预览"
            elif name.startswith("val_batch") and name.endswith("_labels.jpg"):
                title = "验证集标注"
            elif name.startswith("val_batch") and name.endswith("_pred.jpg"):
                title = "验证集预测"
            else:
                title = os.path.splitext(name)[0]
        images.append({"name": name, "title": title})
    return images


def read_log_tail(log_path: str, max_lines: int = 200) -> str:
    if not log_path or not os.path.isfile(log_path):
        return ""
    lines: deque[str] = deque(maxlen=max(1, min(int(max_lines), 5000)))
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            lines.append(line.rstrip("\n"))
    return "\n".join(lines)


def read_training_metrics_series(results_csv: str) -> dict[str, Any]:
    if not results_csv or not os.path.isfile(results_csv):
        return {"available": False, "columns": [], "rows": []}

    rows: list[dict[str, Any]] = []
    columns: list[str] = []
    with open(results_csv, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames:
            columns = [name.strip() for name in reader.fieldnames if name and name.strip()]
        for raw_row in reader:
            row: dict[str, Any] = {}
            for raw_key, raw_value in raw_row.items():
                if raw_key is None:
                    continue
                key = raw_key.strip()
                if not key:
                    continue
                value = str(raw_value or "").strip()
                try:
                    row[key] = float(value)
                except ValueError:
                    row[key] = value
            if row:
                rows.append(row)

    return {"available": bool(rows), "columns": columns, "rows": rows}
