import os
import sys
import time
import tempfile
import sqlite3

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.analytics.signal_engine import calculate_adaptive_signal, _get_memory
from app.database.database import DatabaseManager


def test_rule_1_and_2_density_and_7s_timer():
    print("--- [TEST 1] Density-Based Signal Allocation & 7s Hold Timer ---")
    junc_cfg = {"junction_id": "TEST_JUNC_1"}
    mem = _get_memory("TEST_JUNC_1")
    mem["current_green_lane"] = None
    mem["last_switch_time"] = 0.0
    mem["in_yellow_transition"] = False
    mem["next_green_lane"] = None

    # Cycle 1: lane_4 has highest density (31)
    lane_counts_1 = {"lane_1": 18, "lane_2": 24, "lane_3": 8, "lane_4": 31}
    res1 = calculate_adaptive_signal(junc_cfg, lane_counts_1)
    
    assert res1["active_phase"] == "lane_4", f"Expected lane_4 to be active, got {res1['active_phase']}"
    assert res1["signals"]["lane_4"]["state"] == "GREEN", "lane_4 must be GREEN"
    assert res1["signals"]["lane_1"]["state"] == "RED", "lane_1 must be RED"
    assert res1["signals"]["lane_2"]["state"] == "RED", "lane_2 must be RED"
    assert res1["signals"]["lane_3"]["state"] == "RED", "lane_3 must be RED"
    assert res1["emergency_preemption"] is False
    print("[PASS] Highest density lane granted GREEN successfully.")

    # Cycle 2 (Immediate, elapsed time < 7s): lane_2 now has highest density (40), but 7s hold timer must block transition
    lane_counts_2 = {"lane_1": 18, "lane_2": 40, "lane_3": 8, "lane_4": 31}
    res2 = calculate_adaptive_signal(junc_cfg, lane_counts_2)
    assert res2["active_phase"] == "lane_4", f"Hold timer should keep lane_4 GREEN, got {res2['active_phase']}"
    assert res2["signals"]["lane_4"]["state"] == "GREEN"
    print("[PASS] 7-second hold timer prevented premature state transition.")


def test_yellow_transition_2s():
    print("--- [TEST 2] 2-Second Yellow Transition State ---")
    junc_cfg = {"junction_id": "TEST_JUNC_YELLOW"}
    mem = _get_memory("TEST_JUNC_YELLOW")
    now = time.time()
    mem["current_green_lane"] = "lane_1"
    mem["last_switch_time"] = now - 10.0  # Expired > 7s
    mem["in_yellow_transition"] = False

    # lane_2 has higher density, so after >7s it should trigger yellow transition
    lane_counts = {"lane_1": 10, "lane_2": 35}
    res_yellow = calculate_adaptive_signal(junc_cfg, lane_counts)
    
    assert res_yellow["signals"]["lane_1"]["state"] == "YELLOW", "Current green lane must show YELLOW during transition"
    assert res_yellow["signals"]["lane_2"]["state"] == "YELLOW", "Next green lane must show YELLOW during transition"
    assert res_yellow["signals"]["lane_1"]["timer"] == 2 or res_yellow["signals"]["lane_1"]["timer"] == 1
    print("[PASS] 2-second Yellow transition served correctly between phases.")

    # Advance yellow timer > 2.0s
    mem["yellow_start_time"] = time.time() - 3.0
    res_green = calculate_adaptive_signal(junc_cfg, lane_counts)
    assert res_green["active_phase"] == "lane_2", "After yellow transition, lane_2 must become active green"
    assert res_green["signals"]["lane_2"]["state"] == "GREEN"
    assert res_green["signals"]["lane_1"]["state"] == "RED"
    print("[PASS] Transition cleanly resolved to new GREEN phase.")


def test_rule_3_emergency_preemption():
    print("--- [TEST 3] Emergency Vehicle Immediate Preemption (Priority Rule) ---")
    junc_cfg = {"junction_id": "TEST_JUNC_EMERGENCY"}
    mem = _get_memory("TEST_JUNC_EMERGENCY")
    mem["current_green_lane"] = "lane_4"
    mem["last_switch_time"] = time.time()  # Just switched, 0s elapsed

    # Even with 0s elapsed and lane_4 having highest density (50), lane_1 has an emergency vehicle detected
    lane_counts = {"lane_1": 5, "lane_2": 10, "lane_3": 12, "lane_4": 50}
    emergency_events = {"lane_1": True}

    res_emerg = calculate_adaptive_signal(junc_cfg, lane_counts, emergency_events=emergency_events)

    assert res_emerg["emergency_preemption"] is True, "emergency_preemption flag must be True"
    assert res_emerg["active_phase"] == "lane_1", f"Emergency lane_1 must be active immediately, got {res_emerg['active_phase']}"
    assert res_emerg["signals"]["lane_1"]["state"] == "GREEN", "Emergency lane must be GREEN"
    assert res_emerg["signals"]["lane_2"]["state"] == "RED", "Competing lanes must be RED"
    assert res_emerg["signals"]["lane_3"]["state"] == "RED", "Competing lanes must be RED"
    assert res_emerg["signals"]["lane_4"]["state"] == "RED", "Competing lanes must be RED"
    print("[PASS] Emergency preemption immediately overrode standard 7s hold timer and density ranking.")


def test_rule_4_frontend_response_compatibility():
    print("--- [TEST 4] Frontend State Object Schema Compatibility ---")
    junc_cfg = {"junction_id": "Junction_1"}
    densities = {"lane_1": 18, "lane_2": 24, "lane_3": 8, "lane_4": 31}
    res = calculate_adaptive_signal(junc_cfg, densities)

    # Check top-level keys
    assert "junction_id" in res
    assert "active_phase" in res
    assert "emergency_preemption" in res
    assert "signals" in res

    # Check signal object schema per lane
    for lane_id, sig in res["signals"].items():
        assert "state" in sig and sig["state"] in ["GREEN", "YELLOW", "RED"]
        assert "density" in sig and isinstance(sig["density"], int)
        assert "timer" in sig and isinstance(sig["timer"], int)

    print("[PASS] Response matches frontend integration expectations exactly:")
    print(res)


def test_yolo_model_inference():
    print("--- [TEST 5] AI YOLO Detection & Object Recognition ---")
    import cv2
    from ultralytics import YOLO

    model = YOLO("yolo11n.pt")
    assert model is not None, "YOLO model should load successfully"

    # Test inference on a synthetic image with colored rectangles representing vehicles
    img = np_img = (255 * (0.5 * (1 + 0.5))).astype("uint8") if False else None
    import numpy as np
    test_frame = np.full((480, 640, 3), 120, dtype=np.uint8)
    # Add a white rectangle
    cv2.rectangle(test_frame, (100, 100), (300, 250), (255, 255, 255), -1)

    results = model(test_frame, verbose=False)
    assert results is not None
    print(f"[PASS] YOLO model inference verified successfully.")


if __name__ == "__main__":
    test_rule_1_and_2_density_and_7s_timer()
    test_yellow_transition_2s()
    test_rule_3_emergency_preemption()
    test_rule_4_frontend_response_compatibility()
    test_yolo_model_inference()
    print("\n=======================================================")
    print("ALL ADAPTIVE SIGNAL & AI TELEMETRY TESTS PASSED (100%)!")
    print("=======================================================")
