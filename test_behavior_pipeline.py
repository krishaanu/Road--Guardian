import os
import sys
import numpy as np

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.behavior.kalman_tracker import VehicleStateFilter
from app.behavior.trajectory_intersection import TrajectoryIntersectionEngine
from app.analytics.road_classifier import RoadLayoutClassifier
from app.analytics.traffic_analyzer import LocalTrafficAnalyzer


def test_kalman_filter():
    print("--- 1. Testing VehicleStateFilter (Kalman Filter) ---")
    kf = VehicleStateFilter(sigma_m=4.0, sigma_a=60.0, fps=30.0)

    # Simulate vehicle moving along x at 30 pixels/frame (900 px/s) with +/- 3px jitter
    np.random.seed(42)
    true_x = 100.0
    true_y = 200.0
    vx_true = 30.0 * 30.0  # 900 px/s

    errors_raw = []
    errors_kf = []

    for frame in range(1, 25):
        noise = np.random.normal(0.0, 3.0)
        raw_x = true_x + noise
        raw_y = true_y + np.random.normal(0.0, 3.0)

        sx, sy, vx, vy = kf.update(track_id=1, raw_anchor=(raw_x, raw_y), dt=1.0 / 30.0)

        if frame > 5:
            errors_raw.append(abs(raw_x - true_x))
            errors_kf.append(abs(sx - true_x))

        true_x += 30.0

    print(f"Mean Raw Jitter Error: {np.mean(errors_raw):.2f} px")
    print(f"Mean Kalman Smoothed Error: {np.mean(errors_kf):.2f} px")
    assert np.mean(errors_kf) < np.mean(errors_raw), "Kalman filter should reduce position jitter"

    # Verify track age
    assert kf.get_track_age(1) == 24
    assert kf.get_position(1) is not None
    assert kf.get_velocity(1) is not None

    # Test purge
    kf.purge_lost_tracks([2, 3])
    assert kf.get_position(1) is None
    assert kf.get_track_age(1) == 0

    print("VehicleStateFilter tests PASSED.")


def test_trajectory_intersection_and_impact():
    print("--- 2. Testing TrajectoryIntersectionEngine (Strict Confirmed Collisions) ---")
    from app.behavior.trajectory_intersection import compute_iou

    # 1. Test compute_iou
    box_a = (100, 100, 200, 200)
    box_b = (150, 100, 250, 200)  # Overlap 50 x 100 = 5000, Union = 15000 -> IoU = 0.333
    iou = compute_iou(box_a, box_b)
    assert abs(iou - (1.0 / 3.0)) < 1e-3, f"IoU expected 0.333, got {iou}"

    # Non-overlapping boxes
    box_c = (300, 300, 400, 400)
    assert compute_iou(box_a, box_c) == 0.0

    engine = TrajectoryIntersectionEngine(iou_threshold=0.15, min_deceleration_kmh=25.0)

    boxes = {10: box_a, 20: box_b}

    # 2. Test Track Age Suppression: age < 10 frames must NOT trigger
    young_ages = {10: 5, 20: 15}
    speeds = {10: 10.0, 20: 10.0}
    prev_speeds = {10: 50.0, 20: 50.0}  # Deceleration = 40 km/h > 25 km/h
    collisions = engine.detect_confirmed_collisions(boxes, speeds, prev_speeds, young_ages)
    assert len(collisions) == 0, "Collisions must be ignored when track age < 10"

    # 3. Test Physical Overlap without Deceleration
    mature_ages = {10: 15, 20: 15}
    constant_speeds = {10: 50.0, 20: 50.0}
    prev_constant_speeds = {10: 50.0, 20: 50.0}  # No decel
    for _ in range(5):
        c = engine.detect_confirmed_collisions(boxes, constant_speeds, prev_constant_speeds, mature_ages)
    assert len(c) == 0, "No collision without deceleration even with physical overlap"

    # 4. Test Deceleration without Physical Overlap
    no_overlap_boxes = {10: box_a, 20: box_c}
    for _ in range(5):
        c = engine.detect_confirmed_collisions(no_overlap_boxes, speeds, prev_speeds, mature_ages)
    assert len(c) == 0, "No collision without physical bounding box overlap"

    # 5. Test Confirmed Impact with 3-frame Debounce
    engine.reset()
    # Frame 1 and 2: sustained < 3 frames -> no collision alert yet
    c1 = engine.detect_confirmed_collisions(boxes, speeds, prev_speeds, mature_ages)
    assert len(c1) == 0, "Frame 1 must not trigger before 3-frame persistence"
    c2 = engine.detect_confirmed_collisions(boxes, speeds, prev_speeds, mature_ages)
    assert len(c2) == 0, "Frame 2 must not trigger before 3-frame persistence"

    # Frame 3: sustained >= 3 frames -> confirmed collision!
    c3 = engine.detect_confirmed_collisions(boxes, speeds, prev_speeds, mature_ages)
    assert len(c3) == 1, "Frame 3 must confirm collision"
    assert c3[0] == (10, 20)

    # 6. Test Purge & Reset
    engine.purge_lost_tracks([10])  # Track 20 lost
    assert len(engine.impact_debounce) == 0

    engine.reset()
    assert len(engine.impact_debounce) == 0
    print("TrajectoryIntersectionEngine strict collision tests PASSED.")


def test_road_classifier():
    print("--- 3. Testing RoadLayoutClassifier ---")
    classifier = RoadLayoutClassifier(window_sec=8.0, fps=30.0)

    # 1. Test ONE_WAY: Unimodal headings (all ~0 deg)
    t = 1000.0
    for tid in range(1, 8):
        for _ in range(5):
            classifier.update(tid, velocity=(50.0, 2.0), timestamp=t)
            t += 0.1

    layout = classifier.classify(timestamp=t)
    print(f"Unimodal flow classified as: {layout}")
    assert layout == RoadLayoutClassifier.LAYOUT_ONE_WAY
    assert not classifier.is_non_linear()
    assert not classifier.should_suppress_alerts()

    # 2. Test TWO_WAY: Bimodal headings (half at ~0 deg, half at ~180 deg)
    classifier.reset()
    t = 2000.0
    for tid in range(1, 5):
        for _ in range(5):
            classifier.update(tid, velocity=(50.0, 0.0), timestamp=t)
            t += 0.1
    for tid in range(5, 9):
        for _ in range(5):
            classifier.update(tid, velocity=(-50.0, 0.0), timestamp=t)
            t += 0.1

    layout = classifier.classify(timestamp=t)
    print(f"Opposing bimodal flow classified as: {layout}")
    assert layout == RoadLayoutClassifier.LAYOUT_TWO_WAY
    assert not classifier.is_non_linear()

    # 3. Test NON_LINEAR: High trajectory curvature (e.g. roundabout turning vectors)
    classifier.reset()
    t = 3000.0
    for tid in range(1, 8):
        # Each track turns through 90 degrees
        angles = [0.0, 30.0, 60.0, 90.0]
        for a in angles:
            vx = 40.0 * np.cos(np.radians(a))
            vy = 40.0 * np.sin(np.radians(a))
            classifier.update(tid, velocity=(vx, vy), timestamp=t)
            t += 0.1

    layout = classifier.classify(timestamp=t)
    print(f"Turning roundabout flow classified as: {layout}")
    assert layout == RoadLayoutClassifier.LAYOUT_NON_LINEAR
    assert classifier.is_non_linear()
    assert classifier.should_suppress_alerts()

    print("RoadLayoutClassifier tests PASSED.")


def test_traffic_analyzer_hysteresis_and_dual_signal():
    print("--- 4. Testing LocalTrafficAnalyzer (Hysteresis & Dual-Signal Gate) ---")
    analyzer = LocalTrafficAnalyzer(
        visible_road_length_km=0.20,
        num_lanes=3,
        history_window=10,
        hysteresis_sec=5.0,
        fps=30.0,
    )
    # Road capacity factor = 0.20 * 3 = 0.60 km*lane
    # Density >= 22.0 requires count >= 22 * 0.6 = 13.2 vehicles (e.g. 15 vehicles)

    t = 100.0

    # 1. Dual-Signal Gate: High Density (15 veh) with Moving Traffic (speed = 70 km/h >= 40)
    # MUST stay in HEAVY_FLOW, not CONGESTED!
    for _ in range(20):
        res = analyzer.analyze_frame(current_vehicle_count=15, speeds=[70.0, 75.0], timestamp=t)
        t += 0.5
    print(f"High Density + Moving Speed Status: {res['status']}")
    assert res["status"] == LocalTrafficAnalyzer.STATE_HEAVY_FLOW
    assert res["los"] in ["E", "F"]

    # 2. Dual-Signal Gate: High Density (15 veh) AND Slow Speed (25 km/h < 40)
    # Requires 5.0 seconds of hysteresis to switch to CONGESTED
    analyzer.reset()
    t = 200.0

    # First 4.0 seconds: should NOT be CONGESTED yet!
    for step in range(8):
        res = analyzer.analyze_frame(current_vehicle_count=15, speeds=[25.0, 30.0], timestamp=t)
        t += 0.5
    assert res["status"] != LocalTrafficAnalyzer.STATE_CONGESTED, f"Should not trigger CONGESTED before 5s elapsed: {res}"

    # After 5.0 seconds elapsed from t=200.0 (e.g. t = 205.5): MUST switch to CONGESTED
    t = 205.5
    res = analyzer.analyze_frame(current_vehicle_count=15, speeds=[25.0, 30.0], timestamp=t)
    print(f"After 5.5s hysteresis: {res['status']} ({res['message']})")
    assert res["status"] == LocalTrafficAnalyzer.STATE_CONGESTED

    # 3. Recovery Hysteresis: Traffic clears (count drops to 4, speed goes to 80 km/h)
    # Must sustain recovery for >= 5.0 seconds before clearing CONGESTED
    for step in range(8):
        t += 0.5
        res = analyzer.analyze_frame(current_vehicle_count=4, speeds=[80.0], timestamp=t)
    assert res["status"] == LocalTrafficAnalyzer.STATE_CONGESTED, "Must remain CONGESTED until 5s recovery holds"

    # After 5.0s of sustained recovery:
    for step in range(6):
        t += 0.5
        res = analyzer.analyze_frame(current_vehicle_count=4, speeds=[80.0], timestamp=t)
    print(f"After recovery hysteresis holds for >= 5s: {res['status']} ({res['message']})")
    assert res["status"] == LocalTrafficAnalyzer.STATE_SMOOTH

    # 4. Non-linear road zone suppresses standard density alert
    analyzer.reset()
    res = analyzer.analyze_frame(
        current_vehicle_count=20, speeds=[10.0], timestamp=10.0, is_non_linear=True
    )
    assert res["status"] != LocalTrafficAnalyzer.STATE_CONGESTED
    assert "Non-Linear" in res["message"]

    print("LocalTrafficAnalyzer Hysteresis tests PASSED.")


if __name__ == "__main__":
    test_kalman_filter()
    test_trajectory_intersection_and_impact()
    test_road_classifier()
    test_traffic_analyzer_hysteresis_and_dual_signal()
    print("\nALL PIPELINE ARCHITECTURAL REFACTOR TESTS PASSED!")
