import numpy as np
import time
from typing import Dict, List, Tuple, Optional, Any


class DirectionalZoneClassifier:
    """Infers whether the road is one-way or two-way by clustering vehicle heading vectors over a sliding window."""

    def __init__(self, history_length: int = 150):
        self.history_length = history_length
        self.vector_history: List[Tuple[float, float]] = []
        self.is_two_way: bool = False
        self.dominant_angles: List[float] = []

    def update(self, velocity_vector: Tuple[float, float]):
        """Adds a velocity vector (vx, vy) to history and evaluates directional clustering."""
        vx, vy = velocity_vector
        speed = float(np.hypot(vx, vy))
        if speed < 1.5:  # Ignore stationary/jitter vectors
            return

        self.vector_history.append((vx, vy))
        if len(self.vector_history) > self.history_length:
            self.vector_history.pop(0)

        self._reclassify()

    def _reclassify(self):
        if len(self.vector_history) < 30:
            return

        angles = [np.degrees(np.arctan2(vy, vx)) for vx, vy in self.vector_history]
        # Histogram into 8 angular bins over [-180, 180]
        counts, bin_edges = np.histogram(angles, bins=8, range=(-180.0, 180.0))
        peak_indices = np.argsort(counts)[-2:]

        # If two distinct peaks exist separated by >= 120 and <= 240 degrees, flag as TWO_WAY
        if counts[peak_indices[0]] > 5 and counts[peak_indices[1]] > 5:
            angle_diff = abs(bin_edges[peak_indices[0]] - bin_edges[peak_indices[1]])
            if 120.0 <= angle_diff <= 240.0:
                self.is_two_way = True
                return

        self.is_two_way = False

    def get_track_direction(self, velocity_vector: Tuple[float, float]) -> str:
        """Assigns track to DIRECTION_FORWARD or DIRECTION_REVERSE based on current velocity vector."""
        vx, vy = velocity_vector
        if not self.is_two_way:
            return "DIRECTION_UNIFIED"

        # Directional split based on vertical / dominant longitudinal flow direction
        return "DIRECTION_FORWARD" if vy >= 0.0 else "DIRECTION_REVERSE"

    def reset(self):
        """Clears directional vector history."""
        self.vector_history.clear()
        self.is_two_way = False
        self.dominant_angles.clear()


class IncidentFusionEngine:
    """Multi-Signal Accident Fusion Engine.

    Fuses:
    1. Sudden Deceleration Spike (> 35 km/h drop in 1s)
    2. Trajectory Zigzag / Swerve (heading std > 0.85 rad)
    3. Stationary Vehicle in Travel Lane (speed < 3 km/h for >= 3.0s)
    4. Bounding Box Deformation (aspect ratio shift > 40%)
    """

    def __init__(
        self,
        camera_config: Dict[str, Any],
        required_fusion_score: int = 3,
        persistence_frames: int = 12,
        alert_cooldown_sec: float = 10.0,
    ):
        self.camera_config = camera_config
        self.required_fusion_score = required_fusion_score
        self.persistence_frames = persistence_frames
        self.alert_cooldown_sec = alert_cooldown_sec

        # Per-track temporal history: track_id -> dict of tracking state
        self.track_states: Dict[int, Dict[str, Any]] = {}

        # Direction Classifier
        self.direction_classifier = DirectionalZoneClassifier()

        # Cooldown management: track_id -> timestamp of last confirmed alert
        self.last_alert_timestamps: Dict[int, float] = {}

    def process_track_frame(
        self,
        track_id: int,
        box: Tuple[int, int, int, int],
        smoothed_velocity: Tuple[float, float],
        speed_kmh: float,
        timestamp: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Evaluates a single vehicle track and returns a confirmed incident payload if score & persistence thresholds are met."""
        now = time.time() if timestamp is None else timestamp
        x1, y1, x2, y2 = box
        w, h = max(1, x2 - x1), max(1, y2 - y1)
        aspect_ratio = w / float(h)
        center_pt = (x1 + w // 2, y1 + h // 2)

        self.direction_classifier.update(smoothed_velocity)
        direction_zone = self.direction_classifier.get_track_direction(smoothed_velocity)

        # Initialize track state buffer
        if track_id not in self.track_states:
            self.track_states[track_id] = {
                "first_seen": now,
                "history_positions": [],
                "history_speeds": [],
                "history_headings": [],
                "history_aspect_ratios": [],
                "stationary_start": None,
                "consecutive_incident_frames": 0,
                "direction_zone": direction_zone,
            }

        state = self.track_states[track_id]
        state["history_positions"].append(center_pt)
        state["history_speeds"].append(speed_kmh)
        state["history_aspect_ratios"].append(aspect_ratio)
        state["direction_zone"] = direction_zone

        vel_mag = np.hypot(smoothed_velocity[0], smoothed_velocity[1])
        if vel_mag > 0.5:
            state["history_headings"].append(float(np.arctan2(smoothed_velocity[1], smoothed_velocity[0])))
        elif len(state["history_headings"]) > 0:
            state["history_headings"].append(state["history_headings"][-1])
        else:
            state["history_headings"].append(0.0)

        # Trim sliding windows (~1.5 second history at 30 FPS)
        if len(state["history_positions"]) > 45:
            state["history_positions"].pop(0)
            state["history_speeds"].pop(0)
            state["history_aspect_ratios"].pop(0)
            state["history_headings"].pop(0)

        track_age_frames = len(state["history_positions"])
        # Suppress newly spawned tracks (< 12 frames) to prevent ByteTrack occlusion artifacts
        if track_age_frames < 12:
            return None

        # --- EVALUATE INDIVIDUAL SIGNALS ---
        fusion_score = 0
        signals_triggered = []

        # 1. Sudden Deceleration Spike (Speed drops sharply > 35 km/h within 1 second / 30 frames)
        if len(state["history_speeds"]) >= 12:
            lookback = min(30, len(state["history_speeds"]))
            prior_max = max(state["history_speeds"][-lookback:])
            if (prior_max - speed_kmh) > 35.0:
                fusion_score += 1
                signals_triggered.append("SUDDEN_DECELERATION")

        # 2. Trajectory Zigzag / Sudden steering swerve / Severe Heading Variance
        zigzag_detected = False
        if len(state["history_headings"]) >= 2:
            # Check sudden swerve vs baseline heading
            ref_heading = state["history_headings"][0]
            if len(state["history_headings"]) >= 12:
                ref_heading = float(np.median(state["history_headings"][-25:-5]))
            curr_heading = state["history_headings"][-1]
            diff = abs(curr_heading - ref_heading)
            diff = min(diff, 2 * np.pi - diff)
            if diff > 0.85:
                zigzag_detected = True

        if not zigzag_detected and len(state["history_headings"]) >= 6:
            h_win = np.array(state["history_headings"][-10:])
            if float(np.std(h_win)) > 0.85:
                zigzag_detected = True

        if not zigzag_detected and len(state["history_positions"]) >= 10:
            pts = np.array(state["history_positions"][-10:])
            diffs = np.diff(pts, axis=0)
            valid_diffs = diffs[np.hypot(diffs[:, 0], diffs[:, 1]) > 0.5]
            if len(valid_diffs) >= 4:
                angles = np.arctan2(valid_diffs[:, 1], valid_diffs[:, 0])
                angle_std = float(np.std(angles))
                if angle_std > 0.85:  # High directional instability
                    zigzag_detected = True

        if zigzag_detected:
            fusion_score += 1
            signals_triggered.append("TRAJECTORY_ZIGZAG")

        # 3. Post-Impact Stationary in Travel Lane (> 3.0 seconds)
        if speed_kmh < 3.0:
            if state["stationary_start"] is None:
                state["stationary_start"] = now
            elif (now - state["stationary_start"]) >= 3.0:
                fusion_score += 1
                signals_triggered.append("STATIONARY_IN_LIVE_LANE")
        else:
            state["stationary_start"] = None

        # 4. Bounding Box Deformation (Sudden aspect ratio shift > 40% vs baseline history)
        if len(state["history_aspect_ratios"]) >= 15:
            baseline_ar = float(np.median(state["history_aspect_ratios"][:-5]))
            curr_ar = aspect_ratio
            if abs(curr_ar - baseline_ar) / max(0.01, baseline_ar) > 0.40:
                fusion_score += 1
                signals_triggered.append("BBOX_DEFORMATION")

        # --- FUSION GATE & DEBOUNCE ---
        if fusion_score >= self.required_fusion_score:
            state["consecutive_incident_frames"] += 1
        else:
            state["consecutive_incident_frames"] = max(0, state["consecutive_incident_frames"] - 1)

        # Confirm incident only after sustained verification over persistence_frames
        if state["consecutive_incident_frames"] >= self.persistence_frames:
            last_alert = self.last_alert_timestamps.get(track_id, 0.0)
            if (now - last_alert) >= self.alert_cooldown_sec:
                self.last_alert_timestamps[track_id] = now
                return self._build_incident_payload(
                    track_id=track_id,
                    signals=signals_triggered,
                    score=fusion_score,
                    timestamp=now,
                    direction_zone=direction_zone,
                )

        return None

    def purge_dead_tracks(self, active_track_ids: List[int]):
        """Cleans up internal buffers for tracks no longer visible."""
        active_set = set(active_track_ids)
        dead_ids = [tid for tid in self.track_states if tid not in active_set]
        for tid in dead_ids:
            del self.track_states[tid]
            self.last_alert_timestamps.pop(tid, None)

    def reset(self):
        """Resets all track states and directional classifier."""
        self.track_states.clear()
        self.last_alert_timestamps.clear()
        self.direction_classifier.reset()

    def _build_incident_payload(
        self,
        track_id: int,
        signals: List[str],
        score: int,
        timestamp: float,
        direction_zone: str = "DIRECTION_UNIFIED",
    ) -> Dict[str, Any]:
        """Formats the structured JSON payload for operator confirmation and alert dispatch."""
        return {
            "event_type": "CONFIRMED_INCIDENT",
            "status": "PENDING_OPERATOR_CONFIRMATION",
            "track_id": track_id,
            "fusion_score": score,
            "detected_signals": signals,
            "direction_zone": direction_zone,
            "timestamp": timestamp,
            "camera_metadata": {
                "camera_id": self.camera_config.get("camera_id", "NH44_KM212_North"),
                "location_name": self.camera_config.get(
                    "location_name", "NH-44 near Mahoba toll plaza, northbound"
                ),
                "gps": self.camera_config.get("gps", [25.2914, 79.8713]),
                "direction_covered": self.camera_config.get(
                    "direction_covered", "northbound"
                ),
            },
        }
