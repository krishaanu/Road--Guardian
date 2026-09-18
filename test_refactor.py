import os
import sys
import tempfile
import time
import numpy as np

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.database.database import DatabaseManager
from app.audio.speaker import AudioSpeaker
from app.analytics.traffic_analyzer import LocalTrafficAnalyzer
from app.utils.perspective import PerspectiveTransformer
from app.behavior.advanced_behavior import AdvancedBehaviorEngine


def test_database_upsert():
    print("--- Testing Database Upsert Logic ---")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        temp_db_path = tf.name

    try:
        db = DatabaseManager(temp_db_path)

        # 1. Insert vehicle 1
        db.register_or_update_vehicle(
            global_vehicle_id="GV_001",
            plate_text="KA01AB1234",
            vehicle_type="car",
            vehicle_subtype="Sedan",
            color="White",
        )

        # 2. Upsert same vehicle ID with updated info (should NOT throw IntegrityError)
        db.register_or_update_vehicle(
            global_vehicle_id="GV_001",
            plate_text="KA01AB1234",
            vehicle_type="car",
            vehicle_subtype="Sedan",
            color="Red",
        )

        # 3. Collision test: different global_vehicle_id with duplicate plate_text
        # In the original code, this threw sqlite3.IntegrityError: UNIQUE constraint failed: vehicle_registry.plate_text
        # or when updating, collided on primary key. Now it must update gracefully without throwing!
        db.register_or_update_vehicle(
            global_vehicle_id="GV_002",
            plate_text="KA01AB1234",
            vehicle_type="car",
            vehicle_subtype="Hatchback",
            color="Blue",
        )

        # 4. Insert vehicle with no plate text and upsert it
        db.register_or_update_vehicle(
            global_vehicle_id="GV_003",
            plate_text="",
            vehicle_type="truck",
            vehicle_subtype="Heavy",
            color="Yellow",
        )
        db.register_or_update_vehicle(
            global_vehicle_id="GV_003",
            plate_text=None,
            vehicle_type="truck",
            vehicle_subtype="Heavy",
            color="Green",
        )

        # Verify records in database
        import sqlite3
        with sqlite3.connect(temp_db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT global_vehicle_id, plate_text, color FROM vehicle_registry")
            rows = cursor.fetchall()
            print(f"Database rows after upserts: {rows}")
            assert len(rows) >= 2, "Records should exist in vehicle_registry"

        print("Database upsert tests PASSED successfully.")
    finally:
        import gc
        gc.collect()
        try:
            if os.path.exists(temp_db_path):
                os.remove(temp_db_path)
        except Exception:
            pass


def test_audio_speaker_safety_and_cooldown():
    print("--- Testing AudioSpeaker Safety & Cooldown ---")
    speaker = AudioSpeaker(audio_dir="data/test_audio", cooldown_sec=3.5)
    assert speaker.cooldown_sec >= 3.0, "Cooldown must be >= 3.0 seconds"

    # Cooldown logic testing
    initial_sound_time = speaker.last_sound_played_timestamp
    assert initial_sound_time == 0.0

    # Call speak_alert
    speaker.speak_alert("Test alert 1")
    t1 = speaker.last_sound_played_timestamp
    if speaker.mixer_initialized:
        assert t1 > 0.0, "Timestamp should be updated on speech"
        # Immediate subsequent call should be suppressed by cooldown
        speaker.speak_alert("Test alert 2")
        t2 = speaker.last_sound_played_timestamp
        assert t1 == t2, "Second sound within cooldown should be dropped"
    print("AudioSpeaker safety and cooldown tests PASSED successfully.")


def test_hcm_traffic_analyzer():
    print("--- Testing HCM Level of Service (LOS) Traffic Analyzer ---")
    # Visible length 0.20 km, 3 lanes => road_capacity_factor = 0.60 km*lane
    analyzer = LocalTrafficAnalyzer(visible_road_length_km=0.20, num_lanes=3, history_window=10)

    # 1. Test Density and LOS levels:
    # LOS A: < 7 veh/km/lane -> count < 7 * 0.6 = 4.2 vehicles
    assert analyzer.get_level_of_service(5.0) == "A"
    # LOS B: 7 to 11 veh/km/lane
    assert analyzer.get_level_of_service(8.5) == "B"
    # LOS C: 11 to 16 veh/km/lane
    assert analyzer.get_level_of_service(14.0) == "C"
    # LOS D: 16 to 22 veh/km/lane
    assert analyzer.get_level_of_service(18.0) == "D"
    # LOS E: 22 to 28 veh/km/lane
    assert analyzer.get_level_of_service(25.0) == "E"
    # LOS F: >= 28 veh/km/lane
    assert analyzer.get_level_of_service(30.0) == "F"

    # 2. Test Congestion flag:
    # Requires density >= 22.0 AND avg_speed < 45.0 km/h
    # High density (count=15 -> density = 15 / 0.6 = 25.0 veh/km/lane), high speed (80 km/h) -> Not POSSIBLE_ERROR
    for _ in range(15):
        res = analyzer.analyze_frame(current_vehicle_count=15, speeds=[80.0, 85.0])
    assert res["status"] != "POSSIBLE_ERROR", f"High speed should not trigger POSSIBLE_ERROR: {res}"
    assert res["los"] in ["E", "F"]

    # High density (count=15 -> density 25.0) AND slow speed (35 km/h < 40 km/h)
    # With 5-second hysteresis, advance time past 5.0s to trigger CONGESTED
    analyzer.reset()
    t = 100.0
    for _ in range(5):
        analyzer.analyze_frame(current_vehicle_count=15, speeds=[35.0, 40.0], timestamp=t)
        t += 0.5
    res = analyzer.analyze_frame(current_vehicle_count=15, speeds=[35.0, 40.0], timestamp=t + 6.0)
    assert res["status"] in ["POSSIBLE_ERROR", "CONGESTED"], f"High density + slow speed after hysteresis must trigger CONGESTED: {res}"
    assert "Congestion" in res["message"] or "Breakdown" in res["message"]

    # Low density (count=3 -> density 5.0) AND slow speed (20 km/h) -> NOT POSSIBLE_ERROR
    analyzer.reset()
    for _ in range(15):
        res = analyzer.analyze_frame(current_vehicle_count=3, speeds=[20.0])
    assert res["status"] == "SMOOTH", f"Low density (LOS A) should be SMOOTH: {res}"

    print("HCM Traffic Analyzer tests PASSED successfully.")


def test_perspective_transformer():
    print("--- Testing PerspectiveTransformer ---")
    pt = PerspectiveTransformer(road_width_m=10.5, road_length_m=200.0, frame_size=(1920, 1080))

    # Test single point transformation
    p_img = (960, 1026)  # near bottom center
    ground_pt = pt.transform_point(p_img)
    print(f"Pixel {p_img} -> Ground metric: {ground_pt}")
    assert isinstance(ground_pt, tuple) and len(ground_pt) == 2

    # Test inverse transformation (roundtrip)
    img_pt_rec = pt.inverse_transform_point(ground_pt)
    assert abs(img_pt_rec[0] - p_img[0]) < 1e-2 and abs(img_pt_rec[1] - p_img[1]) < 1e-2, "Inverse transform failed roundtrip precision"

    # Test vectorized transform_points
    pts_img = np.array([[960, 1026], [500, 800], [1200, 800]])
    ground_pts = pt.transform_points(pts_img)
    assert ground_pts.shape == (3, 2), "Vectorized output shape mismatch"

    # Test ground distance calculation
    dist = pt.calculate_ground_distance((500, 800), (1200, 800))
    print(f"Calculated ground distance across lanes: {dist:.2f} meters")
    assert dist > 0.0

    print("PerspectiveTransformer tests PASSED successfully.")


def test_behavior_engine_tuning():
    print("--- Testing AdvancedBehaviorEngine Tuning ---")
    engine = AdvancedBehaviorEngine(
        pixels_per_meter=15.0,
        speed_limit_kmh=100.0,
        drift_angle_threshold_deg=50.0,
        min_drift_speed_kmh=75.0,
    )
    assert engine.min_drift_speed_kmh >= 75.0
    assert engine.drift_angle_threshold_deg >= 50.0

    # Low-speed sharp turn (e.g. 50 km/h) should NOT be detected as high-speed motorway drift
    traj_sharp = [(100, 100), (105, 105), (110, 110), (130, 115), (160, 110)]
    assert not engine.detect_drifting(traj_sharp, current_speed_kmh=50.0)
    print("AdvancedBehaviorEngine tuning tests PASSED successfully.")


if __name__ == "__main__":
    test_database_upsert()
    test_audio_speaker_safety_and_cooldown()
    test_hcm_traffic_analyzer()
    test_perspective_transformer()
    test_behavior_engine_tuning()
    print("\nALL REFACTORING VALIDATION TESTS PASSED!")
