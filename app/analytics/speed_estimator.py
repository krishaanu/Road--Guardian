import math
import cv2
import numpy as np
from typing import Tuple, List, Dict, Optional, Set


class HomographySpeedEstimator:
    """
    Computes accurate real-world vehicle speed using ground-plane homography transformation.
    Calibrated across near, mid, and far visible road depth (6-point perspective mapping).
    Applies median filtering over rolling 8-frame windows to eliminate detection jitter.
    """
    def __init__(
        self,
        src_points: Optional[np.ndarray] = None,
        dst_points: Optional[np.ndarray] = None,
        frame_width: int = 640,
        frame_height: int = 360,
    ):
        self.frame_width = frame_width
        self.frame_height = frame_height
        self.horizon_y = float(frame_height * 0.25)

        if src_points is None or dst_points is None:
            # 6-point perspective calibration spanning far, mid, and near road depths
            w, h = float(frame_width), float(frame_height)
            src_points = np.array([
                [0.34 * w, 0.36 * h], [0.66 * w, 0.36 * h],  # Far field (~60m distance)
                [0.20 * w, 0.64 * h], [0.80 * w, 0.64 * h],  # Mid field (~30m distance)
                [0.06 * w, 0.97 * h], [0.94 * w, 0.97 * h],  # Near field (~0m distance)
            ], dtype=np.float32)

            # Ground-plane coordinates in meters (3 lanes = 10.5m wide; 60m depth)
            dst_points = np.array([
                [0.0, 60.0], [10.5, 60.0],
                [0.0, 30.0], [10.5, 30.0],
                [0.0, 0.0],  [10.5, 0.0],
            ], dtype=np.float32)

        self.src_points = src_points
        self.dst_points = dst_points
        self.H, _ = cv2.findHomography(src_points, dst_points)
        if self.H is None:
            self.H = np.eye(3, dtype=np.float32)

        # Per-track isolated histories
        self.history: Dict[int, List[Tuple[float, float, float]]] = {}  # track_id -> [(x_m, y_m, timestamp)]
        self.speed_windows: Dict[int, List[float]] = {}                  # track_id -> [speeds_kmh]

    def set_frame_dimensions(self, width: int, height: int):
        """Re-scales homography calibration points if frame dimensions change."""
        if width == self.frame_width and height == self.frame_height:
            return
        self.frame_width = width
        self.frame_height = height
        self.horizon_y = float(height * 0.25)
        w, h = float(width), float(height)
        self.src_points = np.array([
            [0.34 * w, 0.36 * h], [0.66 * w, 0.36 * h],
            [0.20 * w, 0.64 * h], [0.80 * w, 0.64 * h],
            [0.06 * w, 0.97 * h], [0.94 * w, 0.97 * h],
        ], dtype=np.float32)
        H, _ = cv2.findHomography(self.src_points, self.dst_points)
        if H is not None:
            self.H = H

    def transform_point(self, px: float, py: float) -> Tuple[float, float]:
        """Transforms a 2D image coordinate into real-world meters using Homography."""
        # Guard against singularity at/above horizon line
        clamped_py = max(self.horizon_y, float(py))
        pt = np.array([px, clamped_py, 1.0], dtype=np.float32).reshape(3, 1)
        transformed = np.dot(self.H, pt)
        w = float(transformed[2, 0])
        if abs(w) > 1e-4:
            rx = float(transformed[0, 0] / w)
            ry = float(transformed[1, 0] / w)
            # Bound within physically reasonable road corridor (-5m to +16m across, -10m to 120m depth)
            rx = max(-5.0, min(16.0, rx))
            ry = max(-10.0, min(120.0, ry))
            return rx, ry
        return px * 0.05, py * 0.05

    def compute_speed(self, track_id: int, bbox: Tuple[int, int, int, int], timestamp: float) -> float:
        """
        Computes calibrated ground speed in km/h for a tracked vehicle.
        Requires a minimum of 5 consistent frames before emitting speed to prevent edge entry spikes.
        """
        # Bottom-center coordinate for ground contact accuracy
        bx = (bbox[0] + bbox[2]) / 2.0
        by = float(bbox[3])
        rx, ry = self.transform_point(bx, by)

        if track_id not in self.history:
            self.history[track_id] = []
            self.speed_windows[track_id] = []

        self.history[track_id].append((rx, ry, timestamp))
        if len(self.history[track_id]) > 15:
            self.history[track_id].pop(0)

        # Require at least 5 frames of history before emitting speed to guard against entry/exit edge spikes
        if len(self.history[track_id]) < 5:
            return 0.0

        pos_now = self.history[track_id][-1]
        pos_prev = self.history[track_id][-4]

        dt = pos_now[2] - pos_prev[2]
        # Guard against zero or negative dt
        if dt < 0.033:
            return float(np.median(self.speed_windows[track_id])) if self.speed_windows[track_id] else 0.0

        dist_m = math.sqrt((pos_now[0] - pos_prev[0]) ** 2 + (pos_now[1] - pos_prev[1]) ** 2)
        inst_speed_kmh = (dist_m / dt) * 3.6

        # Outlier rejection: if instantaneous speed exceeds physical vehicle bounds (> 200 km/h),
        # substitute with running median to avoid frame-skip artifacts
        if inst_speed_kmh > 200.0:
            inst_speed_kmh = float(np.median(self.speed_windows[track_id])) if self.speed_windows[track_id] else 50.0

        # Rolling Window Smoothing (8-frame median filter)
        self.speed_windows[track_id].append(inst_speed_kmh)
        if len(self.speed_windows[track_id]) > 8:
            self.speed_windows[track_id].pop(0)

        smoothed_speed = float(np.median(self.speed_windows[track_id]))
        # No artificial 140 km/h clamp; return true smoothed physical speed bounded between 0 and 200
        return round(float(np.clip(smoothed_speed, 0.0, 200.0)), 1)

    def cleanup_stale_tracks(self, active_track_ids: Set[int]):
        """Frees memory for tracks no longer actively detected."""
        stale = [tid for tid in self.history if tid not in active_track_ids]
        for tid in stale:
            self.history.pop(tid, None)
            self.speed_windows.pop(tid, None)


def evaluate_multi_signal_crash(
    track_a_speed_hist: List[float],
    track_b_speed_hist: List[float],
    track_a_positions: List[Tuple[float, float]],
    iou_val: float,
    stationary_duration_sec: float
) -> Tuple[bool, float]:
    """
    Multi-Signal Collision Engine combining:
    1. Sudden Deceleration (>70% speed drop)
    2. Trajectory Angle Disruption (>60 deg shift)
    3. IoU Bounding Box Overlap Jump (>0.35)
    4. Post-Event Stationary Confirmation (v < 5 km/h for >3 seconds)
    """
    # Signal 1: Sudden Deceleration Check
    sig1 = 0.0
    if len(track_a_speed_hist) >= 5:
        sp_prev = max(track_a_speed_hist[-5:-2])
        sp_now = track_a_speed_hist[-1]
        if sp_prev > 15.0 and sp_now < (sp_prev * 0.3):
            sig1 = 1.0

    # Signal 2: Trajectory Vector Angle Disruption
    sig2 = 0.0
    if len(track_a_positions) >= 3:
        p1, p2, p3 = track_a_positions[-3], track_a_positions[-2], track_a_positions[-1]
        v1 = np.array([p2[0] - p1[0], p2[1] - p1[1]])
        v2 = np.array([p3[0] - p2[0], p3[1] - p2[1]])
        norm1, norm2 = np.linalg.norm(v1), np.linalg.norm(v2)
        if norm1 > 0.1 and norm2 > 0.1:
            cos_angle = np.dot(v1, v2) / (norm1 * norm2)
            angle_deg = np.degrees(np.arccos(np.clip(cos_angle, -1.0, 1.0)))
            if angle_deg > 60.0:
                sig2 = 1.0

    # Signal 3: Bounding Box Overlap Jump
    sig3 = 1.0 if iou_val > 0.35 else 0.0

    # Weighted Crash Score
    crash_score = (sig1 * 0.35) + (sig2 * 0.30) + (sig3 * 0.35)

    # Signal 4: Post-Event Stationary Confirmation Gate
    sig4_confirmed = stationary_duration_sec >= 3.0

    if crash_score >= 0.65 and sig4_confirmed:
        return True, float(np.clip(crash_score, 0.0, 1.0))

    return False, 0.0