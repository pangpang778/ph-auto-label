"""Video upload + frame extraction service.

Saves an uploaded video to a temp file in uploads, extracts frames at a
fixed interval via OpenCV, then removes the temp file. Extracted verbatim
from the ``upload_video`` route handler + its ``extract_frames`` helper
(no char test covers this route). The handler passes raw video bytes so
this service stays Flask-agnostic.
"""
import os
import uuid

import cv2

from app.common.config import PATHS


def extract_video_frames(video_bytes, original_filename, frame_interval):
    """Save video bytes to a temp file, extract frames, remove temp.

    Returns the list of extracted frame filenames.
    """
    original_name = os.path.splitext(original_filename)[0]
    video_ext = os.path.splitext(original_filename)[1] or '.mp4'
    temp_filename = f"temp_{uuid.uuid4().hex}{video_ext}"
    temp_video_path = os.path.join(PATHS['uploads'], temp_filename)
    try:
        with open(temp_video_path, 'wb') as f:
            f.write(video_bytes)
        return _extract_frames(temp_video_path, frame_interval, original_name)
    finally:
        if os.path.exists(temp_video_path):
            os.remove(temp_video_path)


def _extract_frames(video_path, frame_interval, original_name=None):
    """Open a video file and save every ``frame_interval``-th frame as a jpg.

    Verbatim from the monolith's ``extract_frames`` helper (Windows
    Chinese-path-safe cv2.imencode + file write).
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        abs_path = os.path.abspath(video_path)
        cap = cv2.VideoCapture(abs_path)
        if not cap.isOpened():
            raise Exception(f"无法打开视频文件: {video_path}")

    frame_count = 0
    saved_frame_count = 0
    extracted_frames = []

    if original_name is None:
        video_basename = os.path.basename(video_path)
        if video_basename.startswith('temp_'):
            video_basename = video_basename[5:]
        original_name = os.path.splitext(video_basename)[0]

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_count % frame_interval == 0:
            frame_filename = f"{original_name}_frame_{saved_frame_count:06d}.jpg"
            frame_path = os.path.join(PATHS['uploads'], frame_filename)
            success, encoded_img = cv2.imencode('.jpg', frame)
            if success:
                with open(frame_path, 'wb') as f:
                    f.write(encoded_img.tobytes())
                extracted_frames.append(frame_filename)
                saved_frame_count += 1
        frame_count += 1

    cap.release()
    return extracted_frames
