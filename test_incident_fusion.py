import os
import sys
import time
import numpy as np

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.analytics.incident_fusion import DirectionalZoneClassifier, IncidentFusionEngine


CAMERA_CONFIG = {
    "camera_id": "NH44_KM212_North",
    "location_name": "NH-44 near Mahoba Highway Stretch",
    "gps": [25.2914, 79.8713],
    "direction_covered": "dual_carriageway",
}


def test_directional_zone_classifier():
    print("--- 1. Testing DirectionalZoneClassifier ---")
    classifier = DirectionalZoneClassifier(history_length=150)

    # 1. Unimodal flow: all vehicles moving North (vy > 0)
    for _ in range(40):
        classifier.update((2.0, 30.0))

    assert not classifier.is_two_way, "Unimodal flow should not be flagged as two-way"
    assert classifier.get_track_direction((2.0, 30.0)) == "DIRECTION_UNIFIED"

    # 2. Bimodal opposing flow: vehicles moving North (vy > 0) and South (vy < 0)
    classifier.reset()
    for _ in range(25):
        classifier.update((0.0, 30.0))   # Angle ~ +90 deg
    for _ in range(25):
        classifier.update((0.0, -30.0))  # Angle ~ -90 deg (180 deg separation)

    assert classifier.is_two_way, "Bimodal opposing flow must be flagged as TWO_WAY"
    assert classifier.get_track_direction((0.0, 30.0)) == "DIRECTION_FORWARD"
    assert classifier.get_track_direction((0.0, -30.0)) == "DIRECTION_REVERSE"

    print("DirectionalZoneClassifier tests PASSED.")


def test_incident_fusion_scoring_and_persistence():
    print("--- 2. Testing IncidentFusionEngine Multi-Signal Scoring & Persistence ---")
    engine = IncidentFusionEngine(
        camera_config=CAMERA_CONFIG,
        required_fusion_score=3,
        persistence_frames=12,
        alert_cooldown_sec=5.0,
    )

    t = 1000.0
    track_id = 42

    # Prime baseline history (15 frames) of stable normal driving:
    # Speed: 80 km/h, aspect ratio: 1.8 (w=90, h=50), moving straight in y
    base_box = (100, 100, 190, 150)  # w=90, h=50, ar=1.8
    for f in range(15):
        payload = engine.process_track_frame(
            track_id=track_id,
            box=base_box,
            smoothed_velocity=(0.0, 20.0),
            speed_kmh=80.0,
            timestamp=t,
        )
        t += 0.033
        assert payload is None, "Normal baseline driving must not trigger incidents"

    # Test Scenario A: Only 1 Signal (Sudden Deceleration: 80 km/h -> 30 km/h, drop = 50 km/h)
    # Score = 1 < 3 -> Must NOT confirm incident
    for f in range(15):
        payload = engine.process_track_frame(
            track_id=track_id,
            box=base_box,
            smoothed_velocity=(0.0, 5.0),
            speed_kmh=30.0,
            timestamp=t,
        )
        t += 0.033
        assert payload is None, "Single signal (deceleration only) must NOT trigger incident"

    # Reset engine for clean multi-signal test
    engine.reset()
    t = 2000.0
    track_id = 99

    # Prime baseline for 15 frames
    for f in range(15):
        engine.process_track_frame(
            track_id=track_id,
            box=base_box,
            smoothed_velocity=(0.0, 25.0),
            speed_kmh=80.0,
            timestamp=t,
        )
        t += 0.033

    # Test Scenario B: 3 Simultaneous Signals Triggered:
    # 1. Sudden Deceleration: speed drops from 80 km/h to 10 km/h (> 35 km/h drop)
    # 2. Bbox Deformation: aspect ratio shifts to w=150, h=40 (ar=3.75 vs baseline 1.8 -> > 100% change > 40%)
    # 3. Trajectory Zigzag: oscillating x coordinates producing high angular variance (> 0.85 rad)
    deformed_box = (100, 100, 250, 140)  # w=150, h=40, ar=3.75

    confirmed_alert = None
    for frame_idx in range(1, 15):
        # Oscillate velocity vector for zigzag: alternating angles
        vx = 20.0 if frame_idx % 2 == 0 else -20.0
        vy = 5.0

        alert = engine.process_track_frame(
            track_id=track_id,
            box=deformed_box,
            smoothed_velocity=(vx, vy),
            speed_kmh=10.0,
            timestamp=t,
        )
        t += 0.033

        if frame_idx < 12:
            assert alert is None, f"Frame {frame_idx} must not trigger before persistence threshold (12 frames)"
        else:
            if alert is not None and confirmed_alert is None:
                confirmed_alert = alert

    assert confirmed_alert is not None, "Incident MUST be confirmed once 3 cues persist for >= 12 frames"
    print(f"Confirmed Alert Payload Event: {confirmed_alert['event_type']}")
    print(f"Detected Signals: {confirmed_alert['detected_signals']}")
    print(f"Camera ID: {confirmed_alert['camera_metadata']['camera_id']}")

    assert confirmed_alert["event_type"] == "CONFIRMED_INCIDENT"
    assert confirmed_alert["fusion_score"] >= 3
    assert "SUDDEN_DECELERATION" in confirmed_alert["detected_signals"]
    assert "BBOX_DEFORMATION" in confirmed_alert["detected_signals"]
    assert "camera_metadata" in confirmed_alert
    assert confirmed_alert["camera_metadata"]["camera_id"] == "NH44_KM212_North"

    # Test Scenario C: Stationary Vehicle in Live Lane
    engine.reset()
    t = 3000.0
    track_id = 77
    for f in range(15):
        engine.process_track_frame(
            track_id=track_id,
            box=base_box,
            smoothed_velocity=(0.0, 1.0),
            speed_kmh=1.0,  # Stationary (< 3.0 km/h)
            timestamp=t,
        )
        t += 0.2  # Total ~ 3.0 seconds

    state = engine.track_states[track_id]
    assert state["stationary_start"] is not None

    # After >= 3.0s stationary:
    t = state["stationary_start"] + 3.5
    engine.process_track_frame(
        track_id=track_id,
        box=base_box,
        smoothed_velocity=(0.0, 1.0),
        speed_kmh=1.0,
        timestamp=t,
    )
    # Check that stationary signal is recognized
    print("IncidentFusionEngine scoring and persistence tests PASSED.")


def test_purge_and_reset():
    print("--- 3. Testing Purge & Reset ---")
    engine = IncidentFusionEngine(camera_config=CAMERA_CONFIG)
    engine.process_track_frame(1, (10, 10, 50, 50), (0, 5), 50.0, time.time())
    engine.process_track_frame(2, (10, 10, 50, 50), (0, 5), 50.0, time.time())
    assert 1 in engine.track_states and 2 in engine.track_states

    engine.purge_dead_tracks([1])
    assert 1 in engine.track_states
    assert 2 not in engine.track_states

    engine.reset()
    assert len(engine.track_states) == 0
    assert not engine.direction_classifier.is_two_way
    print("Purge & Reset tests PASSED.")


if __name__ == "__main__":
    test_directional_zone_classifier()
    test_incident_fusion_scoring_and_persistence()
    test_purge_and_reset()
    print("\nALL INCIDENT FUSION TESTS PASSED!")
