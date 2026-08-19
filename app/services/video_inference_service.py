"""视频 AI 对比测试 — 离线推理管线。

参考 sam-changkang/video_service.py 的 ffmpeg raw bgr24 逐帧模式：
ffprobe 取信息 -> ffmpeg 解码 -> 按 target_fps 抽帧 -> 逐帧推理(YOLO/SAM3)
-> cv2 绘框+标签+置信度 -> ffmpeg libx264 编码成 AI 视频。

设计要点：
- 抽帧采样：stride = round(src_fps / target_fps)，只对抽到的帧推理，编码按 target_fps 输出，
  使 AI 视频 duration 与原视频对齐（便于双视频同步联动）。
- YOLO 在主进程 lazy-load 并缓存（逐帧推理，子进程开销不可接受）。
- SAM3 复用 sam3_service 单例（GPU），通过其 public detect_frame 方法。
- job 状态存在内存 dict，SSE generator 周期性读取推送进度。
- 本模块是视频推理管线的唯一 owner：素材/输出目录来自共享 PATHS 注册表（构造器可注入覆盖，
  供测试重定向），blueprint 只经 service 接口调用。
"""

import json
import logging
import os
import subprocess
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

from app.common.config import PATHS, VIDEO_EXTENSIONS

logger = logging.getLogger("video-inference")

# 类别配色（与 app.py color_for_index 风格一致）
_PALETTE = [
    "#3aa757", "#4c9ffd", "#ff9d00", "#dc3545", "#6f42c1",
    "#20c997", "#fd7e14", "#17a2b8", "#e83e8c", "#6610f2",
]


def _hex_to_bgr(hex_color: str) -> Tuple[int, int, int]:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return (b, g, r)  # cv2 用 BGR


def _color_for_class(name: str) -> Tuple[int, int, int]:
    idx = abs(hash(name)) % len(_PALETTE)
    return _hex_to_bgr(_PALETTE[idx])


def probe_video(video_path: str) -> Dict[str, Any]:
    """用 ffprobe 取视频宽/高/fps/总帧数/时长。"""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", "-show_format", video_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe 失败: {res.stderr.strip()[:300]}")
    probe = json.loads(res.stdout)
    vstream = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), None)
    if vstream is None:
        raise RuntimeError("未找到视频流")

    width = int(vstream["width"])
    height = int(vstream["height"])
    fps = 30.0
    rfr = vstream.get("r_frame_rate") or "30/1"
    try:
        if "/" in rfr:
            num, den = rfr.split("/")
            den_f = float(den)
            fps = float(num) / den_f if den_f != 0 else 30.0
        else:
            fps = float(rfr)
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    total_frames = int(vstream.get("nb_frames") or 0)
    duration = float(vstream.get("duration") or probe.get("format", {}).get("duration") or 0)
    if (not total_frames) and duration and fps:
        total_frames = int(duration * fps)
    return {"width": width, "height": height, "fps": round(fps, 3),
            "total_frames": total_frames, "duration": round(duration, 3)}


def build_encode_command(video_path: str, width: int, height: int, fps: float, output_path: str) -> List[str]:
    return [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{width}x{height}", "-r", str(fps),
        "-i", "pipe:0",
        "-i", video_path,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "libx264",
        "-c:a", "copy",
        "-pix_fmt", "yuv420p",
        "-preset", "fast", "-crf", "23",
        "-shortest",
        "-v", "quiet",
        output_path,
    ]


def _drain_stderr(proc: subprocess.Popen, sink: Optional[List[str]] = None) -> threading.Thread:
    """后台读取子进程 stderr，防止管道缓冲区满导致死锁。"""
    def _read():
        try:
            for line in proc.stderr:
                if sink is not None:
                    sink.append(line)
        except Exception:
            pass
    t = threading.Thread(target=_read, daemon=True)
    t.start()
    return t


def _sse(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _is_within(base: str, name: str) -> Optional[str]:
    """Resolve ``name`` under ``base`` and return the real path if it stays
    inside, else ``None``.

    Rejects traversal (``..``), absolute paths, and any resolve that escapes
    ``base``. This guards the path fed to ffmpeg/YOLO so a ``<path:name>`` route
    cannot read arbitrary files. ponytail: local impl; consolidating into
    app.common.path_safety is a separate refactor.
    """
    if not name or not isinstance(name, str):
        return None
    if os.path.isabs(name) or '..' in name.replace('\\', '/').split('/'):
        return None
    base_real = os.path.realpath(base)
    child = os.path.realpath(os.path.join(base_real, name))
    try:
        base_nc = os.path.normcase(base_real)
        if os.path.commonpath([base_nc, os.path.normcase(child)]) != base_nc:
            return None
    except ValueError:
        # Different drives (Windows) -> incomparable, treat as outside.
        return None
    if not os.path.isfile(child):
        return None
    return child


class VideoTestError(Exception):
    """视频测试请求错误；携带应返回给客户端的 HTTP 状态。"""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _parse_classes(raw: Any) -> List[str]:
    """把 SAM3 目标类别从 list/str 解析成干净的字符串列表。"""
    if isinstance(raw, list):
        return [str(c).strip() for c in raw if str(c).strip()]
    if isinstance(raw, str):
        s = raw.replace('，', ',').replace('\n', ',').replace(';', ',')
        return [c.strip() for c in s.split(',') if c.strip()]
    return []


def _parse_params(data: Dict[str, Any]) -> Tuple[str, str, int, float]:
    """解析并校验视频测试请求参数。参数非法时抛 ValueError。"""
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
        raise ValueError('引擎必须是 yolo 或 sam3')
    if target_fps not in (1, 2, 5):
        raise ValueError('帧率仅支持 1/2/5')
    return name, engine, target_fps, conf


class VideoInferenceService:
    """视频推理服务：管理 job 状态 + 后台推理线程 + MJPEG 流会话。

    素材与输出目录取共享 PATHS 注册表（call-time），也可用构造参数注入覆盖，
    便于测试重定向到临时目录而不写真实路径。
    """

    def __init__(self, upload_video_dir: Optional[str] = None, static_video_dir: Optional[str] = None) -> None:
        self._upload_override = upload_video_dir
        self._static_override = static_video_dir
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        # YOLO 模型缓存 {model_path: YOLO}
        self._yolo_cache: Dict[str, Any] = {}
        self._yolo_lock = threading.Lock()
        # 流式 MJPEG 会话 {session_id: {...}}
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def _upload_dir(self) -> str:
        return self._upload_override or PATHS["video_uploads"]

    def _static_dir(self) -> str:
        return self._static_override or PATHS["video_static"]

    # ---------- blueprint 单一接口（facade） ----------
    def start_job(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Facade：接收原始请求 dict，校验后启动离线推理任务。"""
        return self._dispatch(data, self._launch_job)

    def start_stream_session(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Facade：接收原始请求 dict，校验后启动 MJPEG 流会话。"""
        return self._dispatch(data, self._launch_stream)

    def _dispatch(self, data: Dict[str, Any], launcher: Callable[..., Dict[str, Any]]) -> Dict[str, Any]:
        try:
            name, engine, target_fps, conf = _parse_params(data)
        except ValueError as exc:
            raise VideoTestError(400, str(exc)) from exc
        path = self.resolve_video(name)
        if not path:
            raise VideoTestError(400, f"视频不存在: {name}")
        if engine == "sam3":
            if not self.sam3_loaded():
                raise VideoTestError(503, "SAM3 模型未加载，请先确认模型已就绪")
            classes = _parse_classes(data.get("classes"))
            if not classes:
                raise VideoTestError(400, "SAM3 需要填写目标类别(text)，如 person,car")
            return launcher(path, "sam3", classes=classes, target_fps=target_fps, conf=conf)
        model_path = data.get("model") or "yolo11n.pt"
        return launcher(path, "yolo", model_path=model_path, target_fps=target_fps, conf=conf)

    def sam3_loaded(self) -> bool:
        from plugins.sam3_service import sam3_service
        return sam3_service.is_loaded

    def list_videos(self) -> List[Dict[str, Any]]:
        """列出可选视频：static/video_compare（默认素材）+ uploads/video_compare（上传）。"""
        seen = {}

        def _scan(directory: str, source: str, skip_ai: bool) -> None:
            try:
                names = sorted(os.listdir(directory))
            except OSError:
                return
            for fn in names:
                if fn.lower().endswith(VIDEO_EXTENSIONS) and not (skip_ai and fn.startswith("ai_")):
                    try:
                        size = os.path.getsize(os.path.join(directory, fn))
                    except OSError:
                        continue
                    seen[fn] = {"name": fn, "source": source,
                                "url": f"/api/video-test/video/{fn}", "size": size}

        _scan(self._static_dir(), "default", skip_ai=True)
        _scan(self._upload_dir(), "upload", skip_ai=False)
        return list(seen.values())

    def resolve_video(self, name: str) -> Optional[str]:
        """按文件名在两个目录里找视频。拒绝越界路径（../、绝对路径）。"""
        for d in (self._upload_dir(), self._static_dir()):
            p = _is_within(d, name)
            if p:
                return p
        return None

    # ---------- job 管理 ----------
    def _launch_job(
        self,
        video_path: str,
        engine: str,
        model_path: Optional[str] = None,
        classes: Optional[List[str]] = None,
        target_fps: int = 2,
        conf: float = 0.35,
    ) -> Dict[str, Any]:
        job_id = f"vjob_{uuid.uuid4().hex[:10]}"
        job = {
            "id": job_id,
            "status": "queued",
            "progress": 0,
            "message": "排队中...",
            "engine": engine,
            "model_path": model_path,
            "classes": classes or [],
            "target_fps": target_fps,
            "conf": conf,
            "video_path": video_path,
            "ai_video": None,
            "ai_video_url": None,
            "error": None,
            "started_at": time.time(),
            "completed_at": None,
            "total_frames": 0,
            "processed_frames": 0,
            "eta_seconds": None,
        }
        with self._lock:
            self._jobs[job_id] = job
        t = threading.Thread(target=self._run, args=(job_id,), daemon=True)
        t.start()
        return job

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._jobs.get(job_id)) if job_id in self._jobs else None

    def stream_progress(self, job_id: str):
        """SSE 生成器：周期读取 job 状态并推送，直到 terminal。"""
        last_sent = None
        idle = 0
        while True:
            job = self.get_job(job_id)
            if job is None:
                yield _sse({"status": "error", "message": "任务不存在"})
                return
            payload = {
                "status": job["status"],
                "progress": job["progress"],
                "message": job["message"],
                "processed_frames": job["processed_frames"],
                "total_frames": job["total_frames"],
                "eta_seconds": job["eta_seconds"],
            }
            if job["status"] in ("completed", "failed"):
                payload["ai_video_url"] = job.get("ai_video_url")
                payload["error"] = job.get("error")
                yield _sse(payload)
                return
            # 只在状态变化或进度推进时发送，减少噪声
            sig = (payload["status"], round(payload["progress"], 1), payload["processed_frames"])
            if sig != last_sent:
                yield _sse(payload)
                last_sent = sig
                idle = 0
            else:
                idle += 1
            time.sleep(0.5)
            # 兜底：长时间无变化也定期心跳（30s）
            if idle > 60:
                yield _sse({**payload, "heartbeat": True})
                idle = 0

    # ---------- 推理主循环 ----------
    def _run(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        try:
            self._set(job_id, status="running", message="读取视频信息...")

            video_path = job["video_path"]
            if not os.path.isfile(video_path):
                raise FileNotFoundError(f"视频不存在: {video_path}")

            info = probe_video(video_path)
            width, height = info["width"], info["height"]
            src_fps = info["fps"] or 30.0
            total_src = info["total_frames"]
            stride = 1  # 每帧都推理（全帧处理，不抽帧）

            # 预估要处理的帧数
            est_frames = total_src if total_src else 0
            self._set(job_id, total_frames=est_frames, message=f"开始推理 {info['width']}x{info['height']} {src_fps}fps, 全帧推理 {est_frames or '?'} 帧")

            out_name = f"ai_{job_id}.mp4"
            out_path = os.path.join(self._static_dir(), out_name)

            engine = job["engine"]
            yolo_model = None
            if engine == "yolo":
                yolo_model = self._get_yolo(job["model_path"])

            # ffmpeg 解码：raw bgr24
            decode_cmd = ["ffmpeg", "-i", video_path, "-f", "rawvideo",
                          "-pix_fmt", "bgr24", "-v", "quiet", "pipe:1"]
            encode_err: List[str] = []
            decode_proc = None
            encode_proc = None
            decode_thread = None
            encode_thread = None
            try:
                decode_proc = subprocess.Popen(decode_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                decode_thread = _drain_stderr(decode_proc)

                encode_cmd = build_encode_command(video_path, width, height, src_fps, out_path)
                encode_proc = subprocess.Popen(encode_cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
                encode_thread = _drain_stderr(encode_proc, encode_err)

                frame_size = width * height * 3
                frame_idx = 0
                processed = 0
                total_dets = 0
                t0 = time.time()

                while True:
                    raw = decode_proc.stdout.read(frame_size)
                    if len(raw) != frame_size:
                        break
                    frame = np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 3))
                    frame = np.array(frame, dtype=np.uint8)  # 可写拷贝

                    if frame_idx % stride == 0:
                        dets = self._infer_frame(job, frame, yolo_model)
                        total_dets += len(dets)
                        frame = self._draw(frame, dets)
                        processed += 1

                        encode_proc.stdin.write(frame.tobytes())

                        # 更新进度
                        if est_frames:
                            prog = min(99.0, processed / est_frames * 100)
                            elapsed = time.time() - t0
                            eta = (elapsed / processed * (est_frames - processed)) if processed else None
                            self._set(
                                job_id, progress=round(prog, 1),
                                processed_frames=processed,
                                eta_seconds=(round(eta, 1) if eta else None),
                                message=f"已处理 {processed}/{est_frames} 帧, 累计 {total_dets} 个目标",
                            )
                    frame_idx += 1

                # 刷新并关闭编码输入，确保所有帧落盘
                try:
                    encode_proc.stdin.flush()
                except Exception:
                    pass
            finally:
                if decode_proc is not None:
                    try:
                        decode_proc.stdout.close()
                    except Exception:
                        pass
                    try:
                        decode_proc.terminate()
                    except Exception:
                        pass
                    try:
                        decode_proc.wait(timeout=5)
                    except Exception:
                        try:
                            decode_proc.kill()
                        except Exception:
                            pass
                if decode_thread is not None:
                    decode_thread.join(timeout=2)

                if encode_proc is not None:
                    try:
                        encode_proc.stdin.close()
                    except Exception:
                        pass
                    if encode_thread is not None:
                        encode_thread.join(timeout=8)
                    try:
                        encode_proc.wait()
                    except Exception:
                        pass
                    if encode_proc.returncode not in (None, 0):
                        err = "".join(encode_err).strip()[:500]
                        raise RuntimeError(f"ffmpeg 编码失败: {err}")

            if not os.path.isfile(out_path) or os.path.getsize(out_path) < 1024:
                raise RuntimeError("AI 视频未生成或为空")

            ai_url = f"/static/video_compare/{out_name}"
            self._set(
                job_id, status="completed", progress=100,
                ai_video=out_path, ai_video_url=ai_url,
                processed_frames=processed, total_frames=est_frames,
                completed_at=time.time(),
                message=f"完成: 处理 {processed} 帧, {total_dets} 个目标, 用时 {time.time()-t0:.1f}s",
            )
            logger.info(f"video job {job_id} done: {processed} frames, {total_dets} dets")

        except Exception as exc:
            logger.error(f"video job {job_id} failed: {exc}", exc_info=True)
            self._set(job_id, status="failed", error=str(exc),
                      completed_at=time.time(),
                      message=f"失败: {exc}")

    # ---------- 推理与绘制 ----------
    def _infer_frame(self, job: Dict[str, Any], frame: np.ndarray, yolo_model) -> List[Dict[str, Any]]:
        engine = job["engine"]
        conf = float(job["conf"])
        if engine == "yolo":
            return self._infer_yolo(yolo_model, frame, conf)
        elif engine == "sam3":
            from plugins.sam3_service import sam3_service
            return sam3_service.detect_frame(frame, text=job["classes"], conf=conf)
        raise ValueError(f"未知引擎: {engine}")

    def _infer_yolo(self, model, frame: np.ndarray, conf: float) -> List[Dict[str, Any]]:
        results = model(frame, conf=conf, verbose=False)
        if not results:
            return []
        r = results[0]
        out: List[Dict[str, Any]] = []
        boxes = getattr(r, "boxes", None)
        names = getattr(r, "names", {}) or {}
        if boxes is None or boxes.xyxy is None:
            return out
        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy() if boxes.conf is not None else []
        clss = boxes.cls.cpu().numpy().astype(int) if boxes.cls is not None else []
        for i in range(len(xyxy)):
            x1, y1, x2, y2 = (float(v) for v in xyxy[i])
            cid = int(clss[i]) if i < len(clss) else -1
            cname = names.get(cid, str(cid)) if isinstance(names, dict) else str(cid)
            c = float(confs[i]) if i < len(confs) else 0.0
            out.append({"class": str(cname), "conf": c, "xyxy": [x1, y1, x2, y2]})
        return out

    def _draw(self, frame: np.ndarray, dets: List[Dict[str, Any]]) -> np.ndarray:
        for d in dets:
            x1, y1, x2, y2 = (int(v) for v in d["xyxy"])
            color = _color_for_class(d["class"])
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            label = f"{d['class']} {d['conf']:.2f}"
            (tw, th), base = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            ytop = max(0, y1 - th - 6)
            cv2.rectangle(frame, (x1, ytop), (x1 + tw + 6, ytop + th + 6), color, -1)
            cv2.putText(frame, label, (x1 + 3, ytop + th + 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
        return frame

    # ---------- 流式 MJPEG 边算边播 ----------
    def _launch_stream(
        self,
        video_path: str,
        engine: str,
        model_path: Optional[str] = None,
        classes: Optional[List[str]] = None,
        target_fps: int = 2,
        conf: float = 0.35,
    ) -> Dict[str, Any]:
        sid = f"vstr_{uuid.uuid4().hex[:10]}"
        session = {
            "id": sid,
            "video_path": video_path,
            "engine": engine,
            "model_path": model_path,
            "classes": classes or [],
            "target_fps": target_fps,
            "conf": conf,
            "stop_flag": threading.Event(),
            "status": "running",
            "current_time": 0.0,
            "total_time": 0.0,
            "frames_pushed": 0,
            "error": None,
            "started_at": time.time(),
        }
        with self._lock:
            self._sessions[sid] = session
        return {"session_id": sid, "status": "running"}

    def get_session(self, sid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            s = self._sessions.get(sid)
        if not s:
            return None
        return {
            "id": s["id"], "status": s["status"],
            "current_time": round(s["current_time"], 2),
            "total_time": round(s["total_time"], 2),
            "frames_pushed": s["frames_pushed"],
            "engine": s["engine"], "error": s["error"],
        }

    def stop_session(self, sid: str) -> bool:
        with self._lock:
            s = self._sessions.get(sid)
        if not s:
            return False
        s["stop_flag"].set()
        if s["status"] == "running":
            s["status"] = "stopped"
        return True

    def stream_mjpeg(self, sid: str):
        """MJPEG 流 generator：逐帧读视频 → 抽帧 → 推理 → 原帧+AI帧水平拼接 → JPEG 推送。

        前端用 <img src="此端点"> 即可边算边播，左原右 AI 天然逐帧同步。
        """
        with self._lock:
            session = self._sessions.get(sid)
        if not session:
            return

        engine = session["engine"]
        classes = session["classes"]
        conf = float(session["conf"])
        target_fps = int(session["target_fps"])
        stop_flag = session["stop_flag"]

        yolo_model = None
        if engine == "yolo":
            try:
                yolo_model = self._get_yolo(session["model_path"])
            except Exception as exc:
                session["error"] = f"YOLO 模型加载失败: {exc}"
                session["status"] = "failed"
                return

        cap = cv2.VideoCapture(session["video_path"])
        if not cap.isOpened():
            session["error"] = "无法打开视频"
            session["status"] = "failed"
            return
        try:
            src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            session["total_time"] = round(total_frames / src_fps, 2) if total_frames and src_fps else 0.0
            stride = max(1, round(src_fps / target_fps))
            idx = 0
            while not stop_flag.is_set():
                ok, frame = cap.read()
                if not ok:
                    break
                if idx % stride == 0:
                    try:
                        if engine == "sam3":
                            from plugins.sam3_service import sam3_service
                            dets = sam3_service.detect_frame(frame, text=classes, conf=conf) if classes else []
                        else:
                            dets = self._infer_yolo(yolo_model, frame, conf)
                    except Exception as exc:
                        logger.warning(f"stream frame {idx} infer failed: {exc}")
                        dets = []
                    ai_drawn = self._draw(np.array(frame, dtype=np.uint8), dets)
                    combined = np.hstack([frame, ai_drawn])
                    ok_enc, buf = cv2.imencode('.jpg', combined, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    if ok_enc:
                        session["current_time"] = idx / src_fps if src_fps else 0.0
                        session["frames_pushed"] += 1
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n\r\n') + buf.tobytes() + b'\r\n'
                idx += 1
            session["status"] = "stopped" if stop_flag.is_set() else "completed"
        except Exception as exc:
            logger.error(f"stream {sid} error: {exc}", exc_info=True)
            session["error"] = str(exc)
            session["status"] = "failed"
        finally:
            try:
                cap.release()
            except Exception:
                pass

    # ---------- YOLO 缓存 ----------
    def _get_yolo(self, model_path: Optional[str]):
        path = model_path or "yolo11n.pt"
        with self._yolo_lock:
            if path not in self._yolo_cache:
                from ultralytics import YOLO
                logger.info(f"loading YOLO for video: {path}")
                self._yolo_cache[path] = YOLO(path)
            return self._yolo_cache[path]

    # ---------- 内部 ----------
    def _set(self, job_id: str, **fields) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(fields)
            # 终态任务超过上限时，淘汰最旧的已完成/失败任务，避免内存无限增长
            if fields.get("status") in ("completed", "failed") and len(self._jobs) > 100:
                term = [(jid, j) for jid, j in self._jobs.items()
                        if j.get("status") in ("completed", "failed")]
                term.sort(key=lambda kv: kv[1].get("completed_at") or 0)
                for jid, _ in term[:max(0, len(term) - 100)]:
                    self._jobs.pop(jid, None)


# 全局单例
video_inference_service = VideoInferenceService()
