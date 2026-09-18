"""
Corridor Speed Detection & OCR Gating Module for RoadGuardian ITS.

Tracks vehicle centroids across video frames and enforces corridor-based gating:
- Outside red corridor: status OUT_OF_ZONE, no speed calculation, OCR gated (ocr_eligible=False).
- Inside red corridor: velocity calculated via homography ground distance and entry/exit timestamps,
  with sliding window median filtering across last N samples, and OCR enabled (ocr_eligible=True).
"""

from typing import Dict, List, Optional, Tuple
import numpy as np
from pydantic import BaseModel, Field
from app.traffic.zone_calibration import CalibratedHomography


class VehicleTrackState(BaseModel):
    """
    Tracks state, position, and velocity history for an individual vehicle track.
    """
    track_id: int
    status: str = Field("OUT_OF_ZONE", description="Tracking zone status: 'OUT_OF_ZONE', 'IN_CORRIDOR', 'COMPLETED'")
    current_centroid: Optional[Tuple[float, float]] = None
    entry_timestamp: Optional[float] = None
    entry_position: Optional[Tuple[float, float]] = None
    last_timestamp: Optional[float] = None
    exit_timestamp: Optional[float] = None
    exit_position: Optional[Tuple[float, float]] = None
    instantaneous_speeds_kmh: List[float] = Field(default_factory=list)
    filtered_speed_kmh: Optional[float] = None
    transit_speed_kmh: Optional[float] = None
    ocr_eligible: bool = False
    ocr_priority: str = Field("LOW", description="'LOW' (outside zone) or 'HIGH' (inside verified corridor)")
    total_samples_inside: int = 0


class CorridorSpeedDetector:
    """
    Manages vehicle centroid trajectories and applies gating rules for speed estimation and OCR execution.
    """

    def __init__(
        self,
        homography: CalibratedHomography,
        median_window_size: int = 5,
        min_samples_for_speed: int = 3,
    ):
        self.homography = homography
        self.median_window_size = max(3, median_window_size)
        self.min_samples_for_speed = min_samples_for_speed
        self.tracks: Dict[int, VehicleTrackState] = {}

    def update_vehicle(
        self,
        track_id: int,
        centroid: Tuple[float, float],
        timestamp: float,
    ) -> VehicleTrackState:
        """
        Updates the track state for a given vehicle centroid at the given frame timestamp.
        Applies gating rules for corridor entry, inside-corridor velocity, median filtering, and OCR eligibility.
        """
        is_inside = self.homography.is_in_corridor(centroid)

        if track_id not in self.tracks:
            self.tracks[track_id] = VehicleTrackState(track_id=track_id)

        track = self.tracks[track_id]
        prev_centroid = track.current_centroid
        prev_timestamp = track.last_timestamp

        track.current_centroid = centroid
        track.last_timestamp = timestamp

        if is_inside:
            # Entering or continuing inside the red corridor
            if track.status != "IN_CORRIDOR":
                # Initial entry into the corridor
                track.status = "IN_CORRIDOR"
                track.entry_timestamp = timestamp
                track.entry_position = centroid
                track.total_samples_inside = 1
                track.ocr_eligible = True
                track.ocr_priority = "HIGH"
            else:
                track.total_samples_inside += 1
                track.ocr_eligible = True
                track.ocr_priority = "HIGH"

                # Calculate incremental ground velocity
                if prev_centroid is not None and prev_timestamp is not None:
                    dt = timestamp - prev_timestamp
                    if dt > 0.001:
                        # Ground distance in meters via homography
                        dist_m = self.homography.calculate_ground_distance(prev_centroid, centroid)
                        # Speed in km/h
                        inst_speed = (dist_m / dt) * 3.6

                        # Sanity clamp speed (0 to 220 km/h)
                        if 0.0 <= inst_speed <= 220.0:
                            track.instantaneous_speeds_kmh.append(inst_speed)
                            if len(track.instantaneous_speeds_kmh) > self.median_window_size:
                                track.instantaneous_speeds_kmh.pop(0)

                            # Apply median filtering across last N samples
                            if len(track.instantaneous_speeds_kmh) >= self.min_samples_for_speed:
                                track.filtered_speed_kmh = round(float(np.median(track.instantaneous_speeds_kmh)), 1)
        else:
            # Outside the red corridor
            if track.status == "IN_CORRIDOR":
                # Just transitioned out of the red corridor -> mark COMPLETED
                track.status = "COMPLETED"
                track.exit_timestamp = timestamp
                track.exit_position = centroid
                track.ocr_eligible = False
                track.ocr_priority = "LOW"

                # Calculate overall corridor transit speed
                if track.entry_timestamp and track.entry_position:
                    transit_dt = timestamp - track.entry_timestamp
                    if transit_dt > 0.1:
                        corridor_dist_m = self.homography.calculate_ground_distance(
                            track.entry_position, centroid
                        )
                        track.transit_speed_kmh = round((corridor_dist_m / transit_dt) * 3.6, 1)
            elif track.status != "COMPLETED":
                # Vehicle has never entered the corridor or has left
                track.status = "OUT_OF_ZONE"
                track.filtered_speed_kmh = None
                track.ocr_eligible = False
                track.ocr_priority = "LOW"

        return track

    def get_track(self, track_id: int) -> Optional[VehicleTrackState]:
        """Returns the current state for a given track ID."""
        return self.tracks.get(track_id)

        stale = [
            tid for tid, state in self.tracks.items()
            if state.last_timestamp and (current_timestamp - state.last_timestamp) > max_idle_sec
        ]
        for tid in stale:
            del self.tracks[tid]


class MultiCameraCorridorSpeedDetector:
    """
    Orchestrates per-camera corridor tracking detectors.
    Allows graceful removal of active tracking pipelines when a camera is deleted.
    """

    def __init__(self):
        self.detectors: Dict[str, CorridorSpeedDetector] = {}

    def get_detector(self, camera_id: str, homography: CalibratedHomography) -> CorridorSpeedDetector:
        """Gets or creates a speed detector for the specified camera."""
        if camera_id not in self.detectors:
            self.detectors[camera_id] = CorridorSpeedDetector(homography=homography)
        return self.detectors[camera_id]

    def clear_camera(self, camera_id: str) -> bool:
        """Gracefully removes active tracking pipeline for a deleted camera."""
        if camera_id in self.detectors:
            del self.detectors[camera_id]
            return True
        return False


multi_camera_speed_detector = MultiCameraCorridorSpeedDetector()
