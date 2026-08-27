"""深度训练闭环验收测试（工单 07 自动化轨，API 级风格仿 test_char_video_test_flow）。

覆盖：伪标签任务生命周期、深度蒸馏任务生命周期（重计算 mock）、
depth 注册表 kind、视频页深度模型下拉与 400 校验。
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import depth_train_service  # noqa: E402
from app.services.video_test_service import resolve_depth_model  # noqa: E402
from plugins.yolo_depth.depth_estimator import DepthAnythingDepthEstimator  # noqa: E402
from plugins.yolo_depth.depth_models import list_depth_models  # noqa: E402
from plugins.yolo_depth.depth_student import gradmatch_loss, silog_loss  # noqa: E402
from plugins.yolo_depth.estimator import build_vd_label  # noqa: E402


# ---------------------------------------------------------------------------
# 单元：标签 / 损失 / 切分 / 下拉
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_build_vd_label_hides_distance_for_relative_depth():
    label = build_vd_label(8, "car", 4.5, -18.0, "靠近", show_dist=False)
    assert label == "ID8 car 18km/h 靠近"
    assert build_vd_label(1, "car", None, None, "", show_dist=False) == "ID1 car"


@pytest.mark.unit
def test_silog_loss_zero_for_identical():
    p = torch_tensor_fixture()
    assert float(silog_loss(p, p.clone())) < 0.01


def torch_tensor_fixture():
    import torch
    return torch.randn(2, 1, 32, 32)


@pytest.mark.unit
def test_gradmatch_loss_zero_for_identical():
    import torch
    p = torch.randn(2, 1, 16, 16)
    assert float(gradmatch_loss(p, p.clone())) == pytest.approx(0.0, abs=1e-6)


@pytest.mark.unit
def test_split_items_by_video_groups_and_is_deterministic():
    from app.services.depth_train_service import split_items_by_video
    items = ([{"frame": f"frames/vA/{i:05d}.jpg"} for i in range(10)]
             + [{"frame": f"frames/vB/{i:05d}.jpg"} for i in range(10)])
    s1 = split_items_by_video(items)
    s2 = split_items_by_video(items)
    assert s1 == s2  # 确定性
    # 分组完整：同一视频的帧不跨集合
    def stems(part):
        return {it["frame"].split("/")[1] for it in part}
    assert not (stems(s1["train"]) & stems(s1["val"]))
    assert len(s1["train"]) == 20 or len(s1["val"]) > 0


@pytest.mark.unit
def test_list_depth_models_builtins_and_trained(tmp_path):
    entries = list_depth_models()
    ids = [e["id"] for e in entries]
    assert "depth_anything_v2_small" in ids and "moge2_vitl" in ids
    rel = next(e for e in entries if e["id"] == "depth_anything_v2_small")
    assert rel["metric"] is False
    weights = tmp_path / "v9.9.pt"
    weights.write_bytes(b"x")
    trained = list_depth_models([{"id": "model_abc", "version": "v9.9", "path": str(weights)}])
    t = [e for e in trained if e["source"] == "trained"]
    assert len(t) == 1 and t[0]["metric"] is True
    # 权重文件不存在的注册项被跳过（内置项仍在，只看 trained）
    assert not [e for e in list_depth_models([{"id": "m", "path": str(tmp_path / "gone.pt")}])
                if e["source"] == "trained"]


@pytest.mark.unit
def test_depth_dataset_disables_pickle_loading(monkeypatch):
    observed = {}
    monkeypatch.setattr(
        depth_train_service.cv2,
        "imread",
        lambda _path: np.zeros((4, 4, 3), dtype=np.uint8),
    )

    def fake_load(_path, **kwargs):
        observed.update(kwargs)
        return np.ones((4, 4), dtype=np.float32)

    monkeypatch.setattr(depth_train_service.np, "load", fake_load)
    dataset = depth_train_service._DepthDataset(
        ".", [{"frame": "frame.jpg", "depth": "depth.npy"}], input_size=4
    )

    dataset[0]

    assert observed["allow_pickle"] is False


@pytest.mark.unit
def test_depth_anything_moves_tensor_output_to_cpu(monkeypatch):
    class FakeTensor:
        def __init__(self):
            self.cpu_called = False

        def detach(self):
            return self

        def cpu(self):
            self.cpu_called = True
            return self

        def numpy(self):
            assert self.cpu_called
            return np.ones((1, 3, 4), dtype=np.float32)

    value = FakeTensor()

    class FakePipe:
        def __call__(self, _image):
            return {"predicted_depth": value}

    estimator = DepthAnythingDepthEstimator()
    monkeypatch.setattr(estimator, "_load", lambda: FakePipe())

    depth = estimator.estimate(np.zeros((6, 8, 3), dtype=np.uint8))

    assert value.cpu_called
    assert depth.shape == (6, 8)


# ---------------------------------------------------------------------------
# resolve_depth_model：工单 06 API 校验契约
# ---------------------------------------------------------------------------

def _patch_no_trained(monkeypatch):
    from app.services import models_service
    monkeypatch.setattr(models_service, "list_models_by_kind", lambda kind: [])


@pytest.mark.unit
def test_resolve_depth_model_none_for_detect(monkeypatch):
    _patch_no_trained(monkeypatch)
    assert resolve_depth_model({"mode": "detect"}) is None


@pytest.mark.unit
def test_resolve_depth_model_defaults_and_metric_flag(monkeypatch):
    _patch_no_trained(monkeypatch)
    r = resolve_depth_model({"mode": "depth_track"})
    assert r["id"] == "depth_anything_v2_small" and r["show_meters"] is False
    m = resolve_depth_model({"mode": "depth_track", "depth_model": "moge2_vitl"})
    assert m["id"] == "moge2_vitl" and m["metric"] is True and m["show_meters"] is True


@pytest.mark.unit
def test_resolve_depth_model_unknown_id_raises(monkeypatch):
    _patch_no_trained(monkeypatch)
    with pytest.raises(ValueError):
        resolve_depth_model({"mode": "depth_track", "depth_model": "bogus"})


@pytest.mark.unit
def test_resolve_depth_model_trained_entry_requires_weights_file(monkeypatch, tmp_path):
    from app.services import models_service
    missing = tmp_path / "nope.pt"
    monkeypatch.setattr(models_service, "list_models_by_kind",
                        lambda kind: [{"id": "model_x", "path": str(missing)}])
    with pytest.raises(ValueError):
        resolve_depth_model({"mode": "depth_track", "depth_model": "model_x"})


# ---------------------------------------------------------------------------
# API 集成：/api/train/start 任务类型分流（重计算 mock，仿 char 测试风格）
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_train_start_pseudo_requires_videos(isolated_app, monkeypatch):
    monkeypatch.setattr("app.blueprints.training.run_training_job", lambda job_id, root_path=None: None)
    client = isolated_app.test_client()
    r = client.post("/api/train/start", json={"task_type": "pseudo", "videos": []})
    assert r.status_code == 400
    r = client.post("/api/train/start", json={"task_type": "pseudo"})
    assert r.status_code == 400


@pytest.mark.integration
def test_train_start_pseudo_rejects_unknown_video(isolated_app, monkeypatch):
    monkeypatch.setattr("app.blueprints.training.run_training_job", lambda job_id, root_path=None: None)
    client = isolated_app.test_client()
    r = client.post("/api/train/start", json={"task_type": "pseudo", "videos": ["ghost.mp4"]})
    assert r.status_code == 400
    assert "视频不存在" in r.get_json()["error"]


@pytest.mark.integration
def test_train_start_depth_requires_manifest(isolated_app, monkeypatch, tmp_path):
    monkeypatch.setattr("app.blueprints.training.run_training_job", lambda job_id, root_path=None: None)
    client = isolated_app.test_client()
    from app.common.config import PATHS
    monkeypatch.setitem(PATHS, "train_work", str(tmp_path))
    ds = tmp_path / "ds_no_manifest"
    ds.mkdir(parents=True)
    r = client.post("/api/train/start", json={
        "task_type": "depth", "dataset_dir": str(ds)})
    assert r.status_code == 400
    assert "manifest" in r.get_json()["error"]


@pytest.mark.integration
def test_pseudo_job_runs_to_completed(isolated_app, monkeypatch):
    """伪标签任务端到端：run_pseudo_labeling mock 写 manifest → 任务 completed。"""
    monkeypatch.setattr("app.blueprints.training.run_training_job", lambda job_id, root_path=None: None)
    monkeypatch.setattr("plugins.video_inference.resolve_video_path", lambda n: "/tmp/fake.mp4")
    client = isolated_app.test_client()
    r = client.post("/api/train/start", json={
        "task_type": "pseudo", "videos": ["fake.mp4"], "interval_s": 0.2})
    assert r.status_code == 200
    job = r.get_json()["job"]
    assert job["task_type"] == "pseudo"

    def fake_labeling(videos, interval_s, out_dir, progress_cb=None):
        os.makedirs(out_dir, exist_ok=True)
        manifest = {"teacher": "moge2_vitl", "frames_total": 2,
                    "videos": {"fake": 2},
                    "items": [{"frame": "frames/fake/00000.jpg",
                               "depth": "depth/fake/00000.npy"},
                              {"frame": "frames/fake/00001.jpg",
                               "depth": "depth/fake/00001.npy"}]}
        with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f)
        return manifest

    monkeypatch.setattr("app.services.depth_label_service.run_pseudo_labeling", fake_labeling)
    from app.services.training_job_runner import run_training_job
    run_training_job(job["id"])
    detail = client.get("/api/train/jobs/" + job["id"]).get_json()
    assert detail["status"] == "completed"
    assert detail["progress"] == 100
    assert detail["artifact_path"]
    assert os.path.isfile(os.path.join(detail["artifact_path"], "manifest.json"))


@pytest.mark.integration
def test_depth_job_registers_kind_depth_without_touching_active(isolated_app, monkeypatch, tmp_path):
    """深度蒸馏端到端：mock 训练 → kind=depth 注册、depth 子目录、active 不变。"""
    import torch
    import app as training_app
    monkeypatch.setattr("app.blueprints.training.run_training_job", lambda job_id, root_path=None: None)
    client = isolated_app.test_client()
    dataset = os.path.join(training_app.PATHS["train_work"], "ds1")
    os.makedirs(os.path.join(dataset, "frames", "v1"), exist_ok=True)
    manifest = {"teacher": "moge2_vitl", "frames_total": 12, "videos": {"v1": 12},
                "items": [{"frame": "frames/v1/%05d.jpg" % i,
                           "depth": "depth/v1/%05d.npy" % i} for i in range(12)]}
    with open(os.path.join(dataset, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f)

    r = client.post("/api/train/start", json={
        "task_type": "depth", "dataset_dir": dataset, "epochs": 2})
    assert r.status_code == 200
    job = r.get_json()["job"]
    assert job["task_type"] == "depth"

    ckpt = tmp_path / "depth_best.pt"
    torch.save({"arch": "mbv3_unet_384", "state_dict": {}, "meta": {}}, ckpt)

    def fake_train(job_dict, job_dir, progress_cb=None):
        return {"run_dir": os.path.join(job_dir, "runs", "depth"),
                "checkpoint_path": str(ckpt),
                "metrics": {"val_silog": 0.12, "box_median_dev_pct": 7.5},
                "splits": {"train": 10, "val": 2}}

    monkeypatch.setattr("app.services.depth_train_service.train_depth_student", fake_train)
    from app.services.training_job_runner import run_training_job
    run_training_job(job["id"])
    detail = client.get("/api/train/jobs/" + job["id"]).get_json()
    assert detail["status"] == "completed"

    registry = client.get("/api/models/registry").get_json()["models"]
    depth_models = [m for m in registry if m.get("kind") == "depth"]
    assert len(depth_models) == 1
    rec = depth_models[0]
    assert os.path.isfile(rec["path"])
    assert "/depth/" in rec["path"].replace("\\\\", "/").replace("\\", "/")
    assert rec["metrics"]["box_median_dev_pct"] == 7.5
    active = client.get("/api/models/active").get_json()
    assert not active.get("model_id")
    dm = client.get("/api/video-test/depth-models").get_json()["models"]
    assert any(e["source"] == "trained" for e in dm)


@pytest.mark.integration
def test_depth_job_failure_preserves_external_dataset(isolated_app, monkeypatch):
    """失败清理守卫：depth 任务失败不得删除共享伪标签数据集。"""
    import app as training_app
    monkeypatch.setattr("app.blueprints.training.run_training_job", lambda job_id, root_path=None: None)
    client = isolated_app.test_client()
    dataset = os.path.join(training_app.PATHS["train_work"], "ds_keep")
    os.makedirs(dataset, exist_ok=True)
    with open(os.path.join(dataset, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"items": []}, f)

    r = client.post("/api/train/start", json={
        "task_type": "depth", "dataset_dir": dataset, "epochs": 1})
    job = r.get_json()["job"]

    def boom(job_dict, job_dir, progress_cb=None):
        raise RuntimeError("GPU 爆了")

    monkeypatch.setattr("app.services.depth_train_service.train_depth_student", boom)
    from app.services.training_job_runner import run_training_job
    run_training_job(job["id"])
    detail = client.get("/api/train/jobs/" + job["id"]).get_json()
    assert detail["status"] == "failed"
    assert os.path.isfile(os.path.join(dataset, "manifest.json"))


@pytest.mark.integration
def test_video_start_depth_track_rejects_unknown_model(isolated_app, monkeypatch):
    """工单 06：非法 depth_model 返回 400，合法内置 id 通过。"""
    import app as training_app
    monkeypatch.setattr(
        training_app.video_inference_service, "start_job",
        lambda *a, **k: {"id": "job-x", "status": "running"})
    monkeypatch.setattr(
        "app.blueprints.video_test.resolve_video_path",
        lambda name: "/tmp/fake.mp4" if name == "fake.mp4" else None)
    from app.services import models_service
    monkeypatch.setattr(models_service, "list_models_by_kind", lambda kind: [])
    client = isolated_app.test_client()
    r = client.post("/api/video-test/start", json={
        "engine": "yolo", "mode": "depth_track", "target_fps": 2,
        "video_name": "fake.mp4", "depth_model": "bogus"})
    assert r.status_code == 400
    r = client.post("/api/video-test/start", json={
        "engine": "yolo", "mode": "depth_track", "target_fps": 2,
        "video_name": "fake.mp4", "depth_model": "moge2_vitl"})
    assert r.status_code == 200


@pytest.mark.unit
def test_activate_rejects_depth_kind(isolated_app, monkeypatch):
    """深度模型不能被激活为检测生产模型。"""
    from app.services.models_service import ModelNotActivatableError, activate_model
    from app.repositories.model_registry_repo import append_model_registry_record as append
    append({"id": "model_d1", "kind": "depth", "path": __file__})
    with pytest.raises(ModelNotActivatableError):
        activate_model("model_d1")


@pytest.mark.unit
def test_activate_depth_kind_http_returns_400_json(isolated_app):
    """HTTP 契约：激活 depth 模型必须返回 400 JSON（此前未捕获异常导致 500）。"""
    from app.repositories.model_registry_repo import append_model_registry_record as append
    append({"id": "model_d2", "kind": "depth", "path": __file__})
    client = isolated_app.test_client()
    r = client.post("/api/models/model_d2/activate")

    assert r.status_code == 400
    assert r.get_json()["error"]


@pytest.mark.unit
def test_run_pseudo_labeling_creates_depth_subdirs(tmp_path, monkeypatch):
    """回归（评审 HIGH）：depth/<stem>/ 未创建会让 np.save 必败。"""
    import cv2
    from app.services import depth_label_service as svc
    from plugins.yolo_depth import depth_models

    stem = "v1"
    out = tmp_path / "ds"
    # 帧必须位于 out_dir 内（生产布局契约：out/frames/<stem>/NNNNN.jpg）
    frames = []
    for i in range(2):
        d = out / "frames" / stem / f"{i:05d}.jpg"
        d.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(d), np.zeros((32, 32, 3), dtype=np.uint8))
        frames.append(str(d))

    class _FakeTeacher:
        def ensure_loaded(self):
            pass

        def estimate(self, img):
            return np.full((16, 16), 5.0, dtype=np.float32)

    monkeypatch.setattr(depth_models, "MoGeDepthEstimator", lambda *a, **k: _FakeTeacher())
    monkeypatch.setattr(svc, "extract_frames", lambda videos, fd, interval: frames)

    manifest = svc.run_pseudo_labeling(
        [{"name": "v1.mp4", "path": "v1.mp4"}], 0.2, str(out))

    assert manifest["frames_total"] == 2
    assert (out / "depth" / stem / "00000.npy").is_file()


@pytest.mark.unit
def test_depth_train_job_rejects_dataset_outside_train_work(tmp_path, monkeypatch):
    """dataset_dir 必须在 train_work 下（防任意服务器路径读取）。"""
    import os
    from app.common.config import PATHS
    from app.services.training_service import _build_depth_train_job

    ds = tmp_path / "job" / "dataset"
    ds.mkdir(parents=True)
    (ds / "manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setitem(PATHS, "train_work", str(tmp_path))

    ok = _build_depth_train_job({"dataset_dir": str(ds)})
    assert ok["task_type"] == "depth"

    outside = os.path.realpath(os.path.join(str(tmp_path), "..", "escape_ds"))
    os.makedirs(outside, exist_ok=True)
    with open(os.path.join(outside, "manifest.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    try:
        _build_depth_train_job({"dataset_dir": outside})
        raise AssertionError("should have raised ValueError")
    except ValueError as exc:
        assert "train_work" in str(exc) or "数据集" in str(exc)


def test_split_single_video_temporal_fallback():
    """single-video dataset: temporal 80/20 split with exclusion gap (review MEDIUM)."""
    from app.services.depth_train_service import split_items_by_video

    items = [{"frame": "frames/v1/%05d.jpg" % i, "depth": "depth/v1/%05d.npy" % i}
             for i in range(100)]
    r = split_items_by_video(items)

    train_frames = {it["frame"] for it in r["train"]}
    val_frames = {it["frame"] for it in r["val"]}
    assert len(r["train"]) == 80 and len(r["val"]) == 18
    assert not (train_frames & val_frames)
    assert len(train_frames) + len(val_frames) == 98  # gap frames excluded
