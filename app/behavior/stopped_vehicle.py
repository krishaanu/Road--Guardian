import time
import numpy as np
from typing import Dict, Tuple


class StoppedVehicleDetector:
    """Detects stationary vehicles using spatial trajectory stability."""

    def __init__(self, move_threshold_px: float = 5.0, stop_duration_sec: float = 3.0):
        self.move_threshold_px = move_threshold_px
        self.stop_duration_sec = stop_duration_sec
        self.track_stop_times: Dict[int, Tuple[float, Tuple[float, float]]] = {}

    def update(self, track_id: int, current_centroid: Tuple[float, float]) -> Tuple[bool, float]:
        now = time.time()
        cx, cy = current_centroid

        if track_id not in self.track_stop_times:
            self.track_stop_times[track_id] = (now, (cx, cy))
            return False, 0.0

        first_seen_stop, (lx, ly) = self.track_stop_times[track_id]
        distance_moved = np.hypot(cx - lx, cy - ly)

        if distance_moved > self.move_threshold_px:
            self.track_stop_times[track_id] = (now, (cx, cy))
            return False, 0.0

        stopped_duration = now - first_seen_stop
        is_stopped = stopped_duration >= self.stop_duration_sec

        return is_stopped, stopped_duration

    def purge_lost_tracks(self, active_track_ids: list):
        current_keys = list(self.track_stop_times.keys())
        for tid in current_keys:
            if tid not in active_track_ids:
                del self.track_stop_times[tid]