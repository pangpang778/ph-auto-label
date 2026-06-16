"""SAM3 semantic detection service for auto-annotation.

Adapted from sam-changkang's model_service.py.
Uses ultralytics SAM3SemanticPredictor for open-vocabulary detection.
"""

import logging
import os
import threading
import traceback
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

logger = logging.getLogger("sam3-service")

_DEFAULT_MODEL_PATH = os.path.join(os.path.dirname(__file__), "sam3", "models", "model.pt")


class SAM3Service:
    """Singleton SAM3 model service. Loads model once, thread-safe inference."""

    def __init__(self) -> None:
        self._predictor = None
        self._model_lock = threading.Lock()
        self._model_path: str = ""
        self._loaded: bool = False

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def load_model(self, model_path: Optional[str] = None) -> None:
        if self._predictor is not None:
            return
        with self._model_lock:
            if self._predictor is not None:
                return
            path = model_path or os.environ.get("SAM3_MODEL_PATH", _DEFAULT_MODEL_PATH)
            path = os.path.abspath(path)
            if not os.path.isfile(path):
                raise FileNotFoundError(
                    f"SAM3 model not found: {path}. "
                    f"Set SAM3_MODEL_PATH env var or place model.pt at {_DEFAULT_MODEL_PATH}"
                )
            logger.info(f"Loading SAM3SemanticPredictor from {path}")
            try:
                from ultralytics.models.sam import SAM3SemanticPredictor

                overrides = dict(
                    conf=0.45,
                    imgsz=1078,
                    task="segment",
                    mode="predict",
                    model=path,
                    half=True,
                    save=False,
                    verbose=False,
                )
                self._predictor = SAM3SemanticPredictor(overrides=overrides)
                self._model_path = path
                self._loaded = True
                logger.info("SAM3SemanticPredictor loaded successfully")
            except Exception as e:
                logger.error(f"SAM3 model load failed: {e}")
                logger.error(traceback.format_exc())
                raise

    def warmup(self, image_path: Optional[str] = None, text: str = "person") -> None:
        if not self._loaded:
            return
        warmup_path = image_path or os.path.join(os.path.dirname(__file__), "..", "uploads")
        # Try to find any image in uploads for warmup
        try:
            if os.path.isdir(warmup_path):
                for f in os.listdir(warmup_path):
                    if f.lower().endswith(('.jpg', '.png', '.jpeg', '.bmp')):
                        warmup_path = os.path.join(warmup_path, f)
                        break
            if not os.path.isfile(warmup_path):
                logger.info("No warmup image found, skipping warmup")
                return
            logger.info(f"Warming up SAM3 with {warmup_path}")
            image = cv2.imread(warmup_path)
            if image is None:
                return
            with self._model_lock:
                self._predictor.set_image(image)
                self._predictor(text=[text])
            logger.info("SAM3 warmup complete")
        except Exception as e:
            logger.warning(f"SAM3 warmup failed (non-fatal): {e}")

    def detect_from_file(
        self,
        image_path: str,
        text: Optional[List[str]] = None,
        conf: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Run SAM3 detection on an image file. Returns annotation-compatible dicts."""
        if not self._loaded:
            raise RuntimeError("SAM3 model not loaded. Call load_model() first.")
        if not os.path.isfile(image_path):
            raise FileNotFoundError(f"Image not found: {image_path}")

        image = cv2.imread(image_path)
        if image is None:
            raise ValueError(f"Failed to read image: {image_path}")

        text_list = [t.strip() for t in (text or []) if t.strip()]
        if not text_list:
            raise ValueError("text (target classes) is required for SAM3 detection")

        if conf is not None:
            self._predictor.args.conf = conf

        acquired = self._model_lock.acquire(timeout=120)
        if not acquired:
            raise RuntimeError("SAM3 inference timeout (120s), model busy")
        try:
            self._predictor.set_image(image)
            results = self._predictor(text=text_list)
        finally:
            self._model_lock.release()

        return self._to_annotations(results, text_list)

    def detect_batch_from_files(
        self,
        image_paths: List[str],
        text: Optional[List[str]] = None,
        conf: Optional[float] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Run SAM3 detection on multiple image files. Returns {path: [annotations]}."""
        if not self._loaded:
            raise RuntimeError("SAM3 model not loaded. Call load_model() first.")

        text_list = [t.strip() for t in (text or []) if t.strip()]
        if not text_list:
            raise ValueError("text (target classes) is required for SAM3 detection")

        if conf is not None:
            self._predictor.args.conf = conf

        all_results: Dict[str, List[Dict[str, Any]]] = {}

        for img_path in image_paths:
            if not os.path.isfile(img_path):
                all_results[img_path] = []
                continue
            try:
                image = cv2.imread(img_path)
                if image is None:
                    all_results[img_path] = []
                    continue
                acquired = self._model_lock.acquire(timeout=120)
                if not acquired:
                    logger.warning(f"Timeout on {img_path}, skipping")
                    all_results[img_path] = []
                    continue
                try:
                    self._predictor.set_image(image)
                    results = self._predictor(text=text_list)
                finally:
                    self._model_lock.release()
                all_results[img_path] = self._to_annotations(results, text_list)
            except Exception as e:
                logger.warning(f"SAM3 detection failed for {img_path}: {e}")
                all_results[img_path] = []

        return all_results

    def detect_frame(
        self,
        frame: "np.ndarray",
        text: Optional[List[str]] = None,
        conf: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """对单帧(BGR ndarray)做 SAM3 开放词汇检测，返回 [{class,conf,xyxy}]。

        供视频逐帧推理复用单例 predictor，避免每帧重载。
        """
        if not self._loaded:
            raise RuntimeError("SAM3 model not loaded. Call load_model() first.")
        text_list = [t.strip() for t in (text or []) if t.strip()]
        if not text_list:
            raise ValueError("text (target classes) is required for SAM3 detection")

        _saved_conf = None
        if conf is not None:
            try:
                _saved_conf = self._predictor.args.conf
            except Exception:
                _saved_conf = None
            self._predictor.args.conf = conf

        acquired = self._model_lock.acquire(timeout=120)
        if not acquired:
            raise RuntimeError("SAM3 inference timeout (120s), model busy")
        try:
            self._predictor.set_image(frame)
            results = self._predictor(text=text_list)
        finally:
            if _saved_conf is not None:
                try:
                    self._predictor.args.conf = _saved_conf
                except Exception:
                    pass
            self._model_lock.release()

        out: List[Dict[str, Any]] = []
        for result in results:
            boxes = getattr(result, "boxes", None)
            if boxes is None or boxes.xyxy is None:
                continue
            xyxy_list = boxes.xyxy.cpu().tolist()
            conf_list = boxes.conf.cpu().tolist() if boxes.conf is not None else []
            cls_list = boxes.cls.cpu().tolist() if boxes.cls is not None else []
            names = getattr(result, "names", {}) or {}
            for i in range(len(xyxy_list)):
                cid = int(cls_list[i]) if i < len(cls_list) else -1
                if isinstance(names, dict) and names:
                    cname = names.get(cid, str(cid))
                else:
                    cname = text_list[cid] if 0 <= cid < len(text_list) else str(cid)
                x1, y1, x2, y2 = (float(v) for v in xyxy_list[i])
                c = float(conf_list[i]) if i < len(conf_list) else 0.0
                out.append({"class": str(cname), "conf": c, "xyxy": [x1, y1, x2, y2]})
        return out

    def _to_annotations(self, results: Any, text: List[str]) -> List[Dict[str, Any]]:
        """Convert SAM3 results to annotation format compatible with the labeling UI."""
        annotations: List[Dict[str, Any]] = []

        for result in results:
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue

            xyxy_list = boxes.xyxy.cpu().tolist() if boxes.xyxy is not None else []
            conf_list = boxes.conf.cpu().tolist() if boxes.conf is not None else []
            cls_list = boxes.cls.cpu().tolist() if boxes.cls is not None else []
            names = getattr(result, "names", {}) or {}

            for i in range(len(xyxy_list)):
                cls_id = int(cls_list[i]) if i < len(cls_list) else -1
                conf = float(conf_list[i]) if i < len(conf_list) else 0.0
                x1, y1, x2, y2 = [float(v) for v in xyxy_list[i]]

                if isinstance(names, dict):
                    cls_name = names.get(cls_id, str(cls_id))
                elif isinstance(names, list) and cls_id < len(names):
                    cls_name = names[cls_id]
                else:
                    cls_name = text[cls_id] if cls_id < len(text) else str(cls_id)

                annotations.append({
                    "class": cls_name,
                    "confidence": conf,
                    "points": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
                    "type": "rectangle",
                    "auto": True,
                })

        return annotations


# Global singleton
sam3_service = SAM3Service()
