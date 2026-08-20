"""Unit tests for the vehicle depth estimator (box_distance + VehicleDepthEstimator).

Pure logic — no Flask, no models, no weights. Synthetic depth maps and track
sequences drive deterministic assertions on distance/speed/direction.
"""
import numpy as np
import pytest

from plugins.yolo_depth.estimator import (
    box_distance,
    build_vd_label,
    VehicleDepthEstimator,
)


@pytest.mark.unit
def test_box_distance_returns_median_of_lower_center_patch():
    # 50x100 depth map, all 10.0 except a 20.0 hotspot in the box's lower-center
    depth = np.full((50, 100), 10.0)
    # box [10,10,40,40]; lower-center zone (y 23..40, x 17..32) -> set 20.0
    depth[23:40, 17:32] = 20.0
    d = box_distance(depth, (10, 10, 40, 40))
    assert d is not None
    assert d == pytest.approx(20.0)


@pytest.mark.unit
def test_box_distance_falls_back_to_full_box_when_patch_too_small():
    # tiny box: lower-center patch < 10 px -> full-box median used
    depth = np.full((50, 100), 5.0)
    d = box_distance(depth, (10, 10, 15, 15))
    assert d == pytest.approx(5.0)


@pytest.mark.unit
def test_box_distance_invalid_or_fully_invalid_patch_returns_none():
    depth = np.full((50, 100), np.nan)
    assert box_distance(depth, (10, 10, 40, 40)) is None
    # box entirely out of bounds
    assert box_distance(depth, (-10, -10, -1, -1)) is None


@pytest.mark.unit
def test_box_distance_clips_partial_out_of_bounds_box():
    depth = np.full((50, 100), 7.0)
    # box mostly off-frame left; clip keeps in-frame remainder
    d = box_distance(depth, (-20, 10, 20, 40))
    assert d is not None
    assert d == pytest.approx(7.0)


@pytest.mark.unit
def test_estimator_first_sample_reports_distance_but_no_speed():
    est = VehicleDepthEstimator()
    dist, speed, direction = est.update(1, 0.0, 5.0)
    assert dist == pytest.approx(5.0)
    assert speed is None
    assert direction == ""


@pytest.mark.unit
def test_estimator_approaching_is_negative_speed_with_close_direction():
    est = VehicleDepthEstimator()
    est.update(1, 0.0, 100.0)
    dist, speed, direction = est.update(1, 1.0, 90.0)  # -10 m/s -> approaching
    assert dist == pytest.approx(90.0)
    assert speed is not None and speed < 0
    assert direction == "靠近"


@pytest.mark.unit
def test_estimator_receding_is_positive_speed_with_away_direction():
    est = VehicleDepthEstimator()
    est.update(1, 0.0, 50.0)
    dist, speed, direction = est.update(1, 1.0, 60.0)  # +10 m/s -> receding
    assert speed is not None and speed > 0
    assert direction == "远离"


@pytest.mark.unit
def test_estimator_stationary_reports_slow_direction():
    est = VehicleDepthEstimator()
    est.update(1, 0.0, 30.0)
    est.update(1, 1.0, 30.0)
    _, _, direction = est.update(1, 2.0, 30.0)
    assert direction == "静止/缓行"


@pytest.mark.unit
def test_estimator_none_distance_passthrough_no_state():
    est = VehicleDepthEstimator()
    est.update(1, 0.0, 5.0)
    dist, speed, direction = est.update(1, 1.0, None)
    assert dist is None and speed is None and direction == ""


@pytest.mark.unit
def test_estimator_tracks_two_ids_independently():
    est = VehicleDepthEstimator()
    est.update(1, 0.0, 100.0)  # A t0
    est.update(2, 0.0, 10.0)   # B t0
    est.update(1, 1.0, 120.0)  # A +20m in 1s -> receding
    _, sA, dA = est.update(1, 2.0, 130.0)
    est.update(2, 1.0, 9.0)    # B approaching
    _, sB, dB = est.update(2, 2.0, 8.0)
    assert sA is not None and sA > 0 and dA == "远离"
    assert sB is not None and sB < 0 and dB == "靠近"


@pytest.mark.unit
def test_build_vd_label_format():
    label = build_vd_label(8, "car", 4.5, -18.0, "靠近")
    assert label == "ID8 car 4.5m 18km/h 靠近"
    assert build_vd_label(3, "bus", None, None, "") == "ID3 bus"