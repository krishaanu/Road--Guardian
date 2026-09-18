import time
import math
import numpy as np
from typing import Dict, List, Tuple, Optional


class RoadLayoutClassifier:
    """
    Classifies road layout topology (ONE_WAY, TWO_WAY, NON_LINEAR) based on
    vehicle velocity heading vector distributions over a sliding temporal window.
    Suppresses linear collision and density alerts when in NON_LINEAR zones (e.g. roundabouts, sharp curves).
    """

    LAYOUT_ONE_WAY = "ONE_WAY"
    LAYOUT_TWO_WAY = "TWO_WAY"
    LAYOUT_NON_LINEAR = "NON_LINEAR"

    def __init__(
        self,
        window_sec: float = 8.0,
        min_speed_px_per_s: float = 8.0,
        min_samples_to_classify: int = 6,
        fps: float = 30.0,
    ):
        self.window_sec = window_sec
        self.min_speed_px_per_s = min_speed_px_per_s
        self.min_samples_to_classify = min_samples_to_classify
        self.fps = fps

        # Buffers: track_id -> List of (timestamp, heading_deg, speed)
        self.track_headings: Dict[int, List[Tuple[float, float, float]]] = {}
        self.current_layout = self.LAYOUT_ONE_WAY
        self.last_classification_time = 0.0

    @staticmethod
    def _angular_difference_deg(angle1: float, angle2: float) -> float:
        """Returns minimal absolute angular difference in degrees [0, 180]."""
        diff = abs(angle1 - angle2) % 360.0
        return 360.0 - diff if diff > 180.0 else diff

    @staticmethod
    def _circular_mean_std_deg(angles_deg: List[float]) -> Tuple[float, float]:
        """Calculates circular mean and circular standard deviation in degrees."""
        if not angles_deg:
            return 0.0, 0.0

        rads = np.radians(angles_deg)
        sin_sum = np.sum(np.sin(rads))
        cos_sum = np.sum(np.cos(rads))
        n = len(rads)

        mean_rad = math.atan2(sin_sum, cos_sum)
        mean_deg = math.degrees(mean_rad) % 360.0

        r_bar = math.hypot(sin_sum, cos_sum) / max(float(n), 1e-6)
        r_bar = min(1.0, max(0.0, r_bar))

        # Circular standard deviation formula: sqrt(-2 * ln(R_bar))
        if r_bar > 1e-4:
            circ_std_rad = math.sqrt(-2.0 * math.log(r_bar))
            circ_std_deg = math.degrees(circ_std_rad)
        else:
            circ_std_deg = 180.0

        return mean_deg, circ_std_deg

    def update(self, track_id: int, velocity: Tuple[float, float], timestamp: Optional[float] = None):
        """
        Records a velocity vector (vx, vy) for track_id into the temporal buffer.
        """
        now = time.time() if timestamp is None else timestamp
        vx, vy = velocity
        speed = math.hypot(vx, vy)

        # Ignore stationary or noisy jitter vectors
        if speed < self.min_speed_px_per_s:
            return

        # Heading angle in degrees [0, 360)
        heading_deg = math.degrees(math.atan2(vy, vx)) % 360.0

        if track_id not in self.track_headings:
            self.track_headings[track_id] = []

        self.track_headings[track_id].append((now, heading_deg, speed))

        # Purge entries outside window
        cutoff = now - self.window_sec
        self.track_headings[track_id] = [
            e for e in self.track_headings[track_id] if e[0] >= cutoff
        ]

    def classify(self, timestamp: Optional[float] = None) -> str:
        """
        Evaluates vector angular distributions across buffered tracks to determine road layout:
        - NON_LINEAR: High within-track angular variance (turning/roundabout) or scattered headings.
        - ONE_WAY: Unimodal heading distribution (within +-35 deg).
        - TWO_WAY: Bimodal heading distribution (~180 deg separation).
        """
        now = time.time() if timestamp is None else timestamp
        cutoff = now - self.window_sec

        track_mean_headings: List[float] = []
        high_curvature_track_count = 0
        evaluated_tracks = 0

        for tid, samples in list(self.track_headings.items()):
            valid_samples = [s for s in samples if s[0] >= cutoff]
            if len(valid_samples) < 3:
                continue

            evaluated_tracks += 1
            headings = [s[1] for s in valid_samples]
            mean_deg, circ_std_deg = self._circular_mean_std_deg(headings)

            # Angular variance per track trajectory > 25 deg indicates a turning / curving trajectory
            if circ_std_deg > 25.0:
                high_curvature_track_count += 1

            track_mean_headings.append(mean_deg)

        if evaluated_tracks < self.min_samples_to_classify:
            return self.current_layout

        # 1. Non-linear check: >25% of tracks exhibit significant trajectory curvature
        if high_curvature_track_count / float(evaluated_tracks) > 0.25:
            self.current_layout = self.LAYOUT_NON_LINEAR
            return self.current_layout

        # 2. Analyze distribution of track mean headings
        primary_heading = track_mean_headings[0]
        aligned_to_primary = []
        opposite_to_primary = []
        perpendicular = []

        for h in track_mean_headings:
            diff = self._angular_difference_deg(h, primary_heading)
            if diff <= 35.0:
                aligned_to_primary.append(h)
            elif abs(diff - 180.0) <= 35.0:
                opposite_to_primary.append(h)
            else:
                perpendicular.append(h)

        total = float(len(track_mean_headings))

        # Check if perpendicular / multi-directional flows dominate
        if len(perpendicular) / total > 0.25:
            self.current_layout = self.LAYOUT_NON_LINEAR
        # Check for TWO_WAY: Bimodal distribution with opposing directions
        elif (len(aligned_to_primary) / total >= 0.20) and (len(opposite_to_primary) / total >= 0.20):
            self.current_layout = self.LAYOUT_TWO_WAY
        # Check for ONE_WAY: Dominantly aligned in a single direction (within +-35 deg)
        elif len(aligned_to_primary) / total >= 0.70 or len(opposite_to_primary) / total >= 0.70:
            self.current_layout = self.LAYOUT_ONE_WAY
        else:
            self.current_layout = self.LAYOUT_TWO_WAY

        return self.current_layout

    def is_non_linear(self) -> bool:
        """Returns True if the road layout is classified as NON_LINEAR."""
        return self.current_layout == self.LAYOUT_NON_LINEAR

    def should_suppress_alerts(self) -> bool:
        """
        Suppresses standard linear collision and density alerts when traversing NON_LINEAR zones.
        """
        return self.is_non_linear()

    def purge_lost_tracks(self, active_track_ids: List[int]):
        """Purges heading history for tracks that are no longer active."""
        active_set = set(active_track_ids)
        for tid in list(self.track_headings.keys()):
            if tid not in active_set:
                del self.track_headings[tid]

    def reset(self):
        """Resets all classification buffers."""
        self.track_headings.clear()
        self.current_layout = self.LAYOUT_ONE_WAY
