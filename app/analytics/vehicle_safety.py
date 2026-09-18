import math
import time
import os
import cv2
import numpy as np
from typing import Dict, List, Tuple, Optional, Any


def format_speed(speed_kmh: float) -> str:
    """
    Formats calibrated real-world speed in km/h.
    Filters sensor/centroid noise below 1.0 km/h to '0 km/h'
    and renders stable integer format for video HUD and telemetry.
    """
    if speed_kmh < 1.0 or math.isnan(speed_kmh):
        return "0 km/h"
    return f"{int(round(speed_kmh))} km/h"


def compute_bbox_iou(boxA: Tuple[int, int, int, int], boxB: Tuple[int, int, int, int]) -> float:
    """Computes Intersection over Union (IoU) between two bounding boxes (x1, y1, x2, y2)."""
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2])
    yB = min(boxA[3], boxB[3])

    interWidth = max(0, xB - xA)
    interHeight = max(0, yB - yA)
    interArea = interWidth * interHeight

    if interArea <= 0:
        return 0.0

    boxAArea = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
    boxBArea = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])

    unionArea = float(boxAArea + boxBArea - interArea)
    if unionArea <= 0:
        return 0.0

    return float(interArea / unionArea)


class SpeedEstimator:
    """
    Measures speed using actual elapsed wall-clock time between detections
    with exponential moving average (EMA) smoothing and physical outlier rejection.
    Operates in real-world ground-plane meter coordinates (x_m, y_m).
    """
    def __init__(self, ema_alpha: float = 0.60, max_valid_speed: float = 180.0):
        self.ema_alpha = ema_alpha
        self.max_valid_speed = max_valid_speed
        # track_id -> list of (x_m, y_m, timestamp, smoothed_speed)
        self.track_history: Dict[int, List[Tuple[float, float, float, float]]] = {}
        self.smoothed_speeds: Dict[int, float] = {}

    def update(self, track_id: int, x_meters: float, y_meters: float, timestamp: float) -> float:
        """
        Updates tracking history for a vehicle in real-world meters.
        Measures ACTUAL elapsed wall-clock time, computes Euclidean displacement,
        applies outlier rejection, and updates EMA smoothed speed.
        Returns smoothed speed in km/h.
        """
        if track_id not in self.track_history:
            self.track_history[track_id] = []
            self.smoothed_speeds[track_id] = 0.0

        history = self.track_history[track_id]

        if len(history) == 0:
            history.append((x_meters, y_meters, timestamp, 0.0))
            return 0.0

        prev_x, prev_y, prev_t, prev_spd = history[-1]
        dt = timestamp - prev_t

        # Guard against zero/negative delta time or large disconnect gaps (> 2.0s)
        if dt <= 0.001 or dt > 2.0:
            history.append((x_meters, y_meters, timestamp, prev_spd))
            if len(history) > 25:
                history.pop(0)
            return self.smoothed_speeds.get(track_id, 0.0)

        # Real-world Euclidean displacement in meters
        dx = x_meters - prev_x
        dy = y_meters - prev_y
        dist_m = math.hypot(dx, dy)

        # Instantaneous speed: (meters / second) * 3.6 -> km/h
        instantaneous_speed = (dist_m / dt) * 3.6

        # Outlier rejection: filter unphysical detection jitter / coordinate jumps
        if instantaneous_speed > self.max_valid_speed:
            instantaneous_speed = min(self.max_valid_speed, prev_spd * 1.2 if prev_spd > 0 else 60.0)

        # EMA Smoothing: smooth = alpha * inst + (1 - alpha) * prev_smooth
        curr_smoothed = self.smoothed_speeds.get(track_id, 0.0)
        if curr_smoothed == 0.0:
            new_smoothed = instantaneous_speed
        else:
            new_smoothed = (self.ema_alpha * instantaneous_speed) + ((1.0 - self.ema_alpha) * curr_smoothed)

        self.smoothed_speeds[track_id] = new_smoothed
        history.append((x_meters, y_meters, timestamp, new_smoothed))
        if len(history) > 25:
            history.pop(0)

        return new_smoothed

    def get_speed(self, track_id: int) -> float:
        """Returns the current smoothed speed in km/h."""
        return self.smoothed_speeds.get(track_id, 0.0)

    def get_speed_history(self, track_id: int, window_sec: float = 1.5) -> List[Tuple[float, float]]:
        """Returns list of (timestamp, smoothed_speed) within the last window_sec."""
        history = self.track_history.get(track_id, [])
        if not history:
            return []
        latest_t = history[-1][2]
        return [(t, spd) for (_, _, t, spd) in history if (latest_t - t) <= window_sec]

    def purge_track(self, track_id: int):
        """Purges track history when vehicle exits surveillance frame."""
        self.track_history.pop(track_id, None)
        self.smoothed_speeds.pop(track_id, None)


class CrashDetector:
    """
    Collision and crash detector with dual-condition confirmation:
    (1) Tracked vehicle's smoothed speed drops abruptly (>25 km/h within ~1.5s) from meaningful speed (>15 km/h).
    (2) Vehicle's bounding box spatially overlaps another tracked vehicle's box at the same moment.
    Requires 3 consecutive candidate checks before confirmation to filter single-frame tracking glitches.
    """
    def __init__(
        self,
        min_pre_speed: float = 15.0,
        min_speed_drop: float = 25.0,
        window_sec: float = 1.5,
        min_consecutive_checks: int = 3,
        cooldown_sec: float = 10.0,
    ):
        self.min_pre_speed = min_pre_speed
        self.min_speed_drop = min_speed_drop
        self.window_sec = window_sec
        self.min_consecutive_checks = min_consecutive_checks
        self.cooldown_sec = cooldown_sec

        # track_id -> consecutive matching candidate count
        self.candidate_counts: Dict[int, int] = {}
        # track_id -> timestamp when confirmed
        self.confirmed_alerts: Dict[int, float] = {}

    def check(
        self,
        track_id: int,
        bbox: Tuple[int, int, int, int],
        all_current_bboxes: Dict[int, Tuple[int, int, int, int]],
        speed_estimator: SpeedEstimator,
        current_time: Optional[float] = None,
    ) -> bool:
        """
        Evaluates track_id for confirmed crash event.
        Returns True ONLY on confirmed crash (persisting for 3 consecutive checks).
        """
        now = current_time or time.time()

        # Check cooldown to prevent duplicate triggers for the same vehicle incident
        if track_id in self.confirmed_alerts and (now - self.confirmed_alerts[track_id] < self.cooldown_sec):
            return False

        # Condition 1: Abrupt speed drop check over window_sec
        speed_hist = speed_estimator.get_speed_history(track_id, window_sec=self.window_sec)
        if len(speed_hist) < 2:
            self.candidate_counts[track_id] = 0
            return False

        max_pre_speed = max(spd for (_, spd) in speed_hist)
        current_speed = speed_estimator.get_speed(track_id)
        speed_drop = max_pre_speed - current_speed

        condition_speed = (max_pre_speed >= self.min_pre_speed) and (speed_drop >= self.min_speed_drop)

        # Condition 2: Bounding box overlap with another vehicle in the same frame
        condition_overlap = False
        for other_id, other_bbox in all_current_bboxes.items():
            if other_id == track_id:
                continue
            iou = compute_bbox_iou(bbox, other_bbox)
            if iou > 0.05:
                condition_overlap = True
                break

        # Dual simultaneous check + persistence counter
        if condition_speed and condition_overlap:
            self.candidate_counts[track_id] = self.candidate_counts.get(track_id, 0) + 1
            if self.candidate_counts[track_id] >= self.min_consecutive_checks:
                self.confirmed_alerts[track_id] = now
                self.candidate_counts[track_id] = 0
                return True
        else:
            # Non-matching frame resets candidate counter
            self.candidate_counts[track_id] = 0

        return False

    def purge_track(self, track_id: int):
        """Purges internal state for track."""
        self.candidate_counts.pop(track_id, None)
        self.confirmed_alerts.pop(track_id, None)
