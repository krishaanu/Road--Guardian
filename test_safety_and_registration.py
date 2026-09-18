import os
import sys
import time
import numpy as np

# Ensure project root in sys.path
sys.path.insert(0, r"C:\Users\krish\RoadGuardian")

from app.analytics.vehicle_safety import SpeedEstimator, CrashDetector, format_speed, compute_bbox_iou
from app.core.source_resolver import resolve_camera_source
from fastapi.testclient import TestClient
from app.dashboard.server import app

def run_tests():
    print("=== [TEST 1] format_speed Sanitization ===")
    assert format_speed(0.0) == "0 km/h"
    assert format_speed(0.4) == "0 km/h"
    assert format_speed(0.9) == "0 km/h"
    assert format_speed(1.0) == "1 km/h"
    assert format_speed(45.2) == "45 km/h"
    assert format_speed(45.8) == "46 km/h"
    print("[PASS] format_speed sanitization verified.")

    print("\n=== [TEST 2] SpeedEstimator with Wall-Clock Elapsed Time & EMA Smoothing ===")
    estimator = SpeedEstimator(ema_alpha=0.60, max_valid_speed=180.0)
    track_id = 101

    # Simulate vehicle moving along Y axis at 15 m/s (54 km/h) with variable dt (frame drops)
    t = 1000.0
    y = 0.0
    speeds = []
    
    # Frame 1 (t=1000.0, y=0.0)
    s1 = estimator.update(track_id, 0.0, y, t)
    
    # Frame 2 (dt=0.04s, y=0.6m -> 15 m/s = 54 km/h)
    t += 0.04
    y += 0.6
    s2 = estimator.update(track_id, 0.0, y, t)
    speeds.append(s2)

    # Frame 3 (frame drop: dt=0.08s, y=1.2m -> 15 m/s = 54 km/h)
    t += 0.08
    y += 1.2
    s3 = estimator.update(track_id, 0.0, y, t)
    speeds.append(s3)

    # Frame 4 (dt=0.04s, y=0.6m)
    t += 0.04
    y += 0.6
    s4 = estimator.update(track_id, 0.0, y, t)
    speeds.append(s4)

    print(f"Computed speeds across variable frame intervals: {[round(s, 1) for s in speeds]}")
    # Speeds should converge to ~54 km/h regardless of frame drop (dt=0.08s)
    assert abs(speeds[-1] - 54.0) < 3.0, f"Expected ~54 km/h, got {speeds[-1]}"
    print("[PASS] SpeedEstimator wall-clock elapsed time accuracy verified.")

    print("\n=== [TEST 3] CrashDetector Dual-Condition & 3-Frame Persistence Filter ===")
    detector = CrashDetector(min_pre_speed=15.0, min_speed_drop=25.0, window_sec=1.5, min_consecutive_checks=3)
    
    # Vehicle A: moving fast at 60 km/h
    est_a = SpeedEstimator(ema_alpha=0.60)
    tid_a = 201
    tid_b = 202

    t = 2000.0
    # Step 1: Normal cruising at 60 km/h for 1.0s
    for i in range(10):
        t += 0.05
        est_a.update(tid_a, 0.0, float(i * 0.83), t) # ~16.6 m/s = 60 km/h

    assert est_a.get_speed(tid_a) > 50.0

    # Test Case A: Normal deceleration (braking at red light) with NO overlap
    for i in range(5):
        t += 0.05
        est_a.update(tid_a, 0.0, float(10 * 0.83 + i * 0.05), t) # decelerates to ~3 km/h
        # Non-overlapping bboxes
        bbox_a = (100, 100, 150, 150)
        all_bboxes = {tid_a: bbox_a, tid_b: (300, 300, 350, 350)}
        assert detector.check(tid_a, bbox_a, all_bboxes, est_a, current_time=t) == False
    print("[PASS] Case A (Normal braking without collision): No false positive.")

    # Test Case B: Single-frame glitch (overlap + drop on 1 frame only)
    detector.purge_track(tid_a)
    est_a = SpeedEstimator(ema_alpha=0.60)
    t = 3000.0
    for i in range(10):
        t += 0.05
        est_a.update(tid_a, 0.0, float(i * 0.83), t) # cruising at 60 km/h

    last_y = float(9 * 0.83)

    # 1 frame of abrupt stop (dy = 0) + overlap
    t += 0.05
    est_a.update(tid_a, 0.0, last_y, t) # speed drops abruptly
    bbox_a = (100, 100, 150, 150)
    all_bboxes_crash = {tid_a: bbox_a, tid_b: (110, 110, 160, 160)} # overlapping boxes
    res_glitch = detector.check(tid_a, bbox_a, all_bboxes_crash, est_a, current_time=t)
    assert res_glitch == False, "Single frame glitch should NOT trigger confirmed crash"
    print("[PASS] Case B (Single-frame glitch): Correctly filtered by persistence check (candidate count: 1).")

    # Test Case C: True collision persisting for 3 consecutive checks
    # Frame 2 of crash
    t += 0.05
    est_a.update(tid_a, 0.0, last_y, t)
    res_f2 = detector.check(tid_a, bbox_a, all_bboxes_crash, est_a, current_time=t)
    assert res_f2 == False, "Frame 2 should still be in candidate state (candidate count: 2)"

    # Frame 3 of crash (3rd consecutive check)
    t += 0.05
    est_a.update(tid_a, 0.0, last_y, t)
    res_f3 = detector.check(tid_a, bbox_a, all_bboxes_crash, est_a, current_time=t)
    assert res_f3 == True, "3rd consecutive check MUST confirm crash"
    print("[PASS] Case C (True persistent crash): Successfully confirmed on 3rd frame.")

    print("\n=== [TEST 4] Camera ID Sanitization & Ingestion with Spaces ('cam 5') ===")
    client = TestClient(app)
    
    # Test registering 'cam 5' with spaces
    add_payload = {
        "camera_id": "cam 5",
        "name": "Highway Corridor 5",
        "location_name": "Sector 5 West",
        "source": "uploads/sample.mp4",
        "source_type": "file",
        "gps": [25.2914, 79.8713],
        "direction_covered": "Northbound",
    }
    reg_resp = client.post("/api/cameras/add", json=add_payload)
    assert reg_resp.status_code == 200, f"Registration failed: {reg_resp.text}"
    reg_data = reg_resp.json()
    assert reg_data["status"] == "success"
    assert reg_data["data"]["id"] == "CAM_5", f"Expected sanitized 'CAM_5', got {reg_data['data']['id']}"
    print(f"[PASS] Camera ID 'cam 5' sanitized and registered successfully as '{reg_data['data']['id']}'.")

    print("\n=== [TEST 5] Multi-Lane Video Upload Isolation (Lanes 1, 2, 3) ===")
    sample_mp4 = "uploads/13002160_1920_1080_60fps.mp4"
    if not os.path.exists(sample_mp4):
        for root, _, files in os.walk("uploads"):
            for f in files:
                if f.endswith(".mp4"):
                    sample_mp4 = os.path.join(root, f)
                    break
            if os.path.exists(sample_mp4):
                break

    if os.path.exists(sample_mp4):
        lanes = ["SIM_JUNCTION_01_LANE_1", "SIM_JUNCTION_01_LANE_2", "SIM_JUNCTION_01_LANE_3"]
        uploaded_paths = {}
        for lane in lanes:
            with open(sample_mp4, "rb") as vf:
                files = {"file": (f"{lane}_test.mp4", vf, "video/mp4")}
                res = client.post(f"/api/upload_video?camera_id={lane}", files=files)
                assert res.status_code == 200
                data = res.json()
                uploaded_paths[lane] = data["file_path"]
                assert lane in data["file_path"], f"Expected {lane} in path, got {data['file_path']}"
                print(f"[PASS] {lane} uploaded to isolated directory: {data['file_path']}")

        # Ensure all paths are unique per lane
        assert len(set(uploaded_paths.values())) == len(lanes), "Paths must be distinct per lane"
        print("[PASS] All lanes use independent unique storage paths.")

    print("\n=======================================================")
    print("ALL SAFETY, SPEED, CRASH & REGISTRATION TESTS PASSED (100%)!")
    print("=======================================================")

if __name__ == "__main__":
    run_tests()
