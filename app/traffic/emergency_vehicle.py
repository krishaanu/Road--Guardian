"""
Emergency Vehicle Tracking & Detection Module for RoadGuardian ITS.

Detects emergency vehicles (AMBULANCE, POLICE, FIRE_TRUCK), monitors lane occupancy,
and dispatches PRIORITY_REQUEST events into the traffic control pipeline
when an emergency vehicle approaches or enters a red-light signal group lane.
"""

from typing import Dict, List, Optional, Tuple, Literal
import time
import uuid
from pydantic import BaseModel, Field


class EmergencyDetection(BaseModel):
    """
    Detection payload for an identified emergency vehicle on the roadway.
    """
    track_id: int
    vehicle_type: Literal["AMBULANCE", "POLICE", "FIRE_TRUCK"] = "AMBULANCE"
    lane_id: int = Field(..., ge=1, le=10, description="1-indexed lane number where vehicle is located")
    centroid: Tuple[float, float] = Field(..., description="(x, y) pixel coordinates of vehicle centroid")
    timestamp: float = Field(default_factory=time.time, description="Detection timestamp (epoch seconds)")
    speed_kmh: Optional[float] = None
    confidence: float = Field(1.0, ge=0.0, le=1.0)


class PriorityRequestEvent(BaseModel):
    """
    Event payload generated when an emergency vehicle requests priority signal preemption.
    """
    event_id: str = Field(default_factory=lambda: f"PR_{uuid.uuid4().hex[:8]}")
    event_type: str = "PRIORITY_REQUEST"
    emergency_detection: EmergencyDetection
    target_signal_group_id: str
    current_signal_state: str
    action_recommended: Literal["EARLY_GREEN", "EXTEND_GREEN", "HOLD_GREEN"]
    timestamp: float = Field(default_factory=time.time)
    cleared: bool = False
    notes: Optional[str] = None


class EmergencyVehicleTracker:
    """
    Maintains active emergency vehicle detections across video frames
    and evaluates preemption requirements against assigned signal groups.
    """

    def __init__(self, lane_boundaries: Optional[List[int]] = None):
        # Default lane divider x-coordinates for 3-lane road (e.g. at 640x360 or 1920x1080)
        self.lane_boundaries = lane_boundaries or [400, 750]
        self.active_emergency_tracks: Dict[int, EmergencyDetection] = {}

    def assign_lane(self, centroid_x: float) -> int:
        """
        Determines the 1-indexed lane number from centroid X coordinate.
        """
        for i, boundary in enumerate(self.lane_boundaries):
            if centroid_x < boundary:
                return i + 1
        return len(self.lane_boundaries) + 1

    def update_detection(
        self,
        track_id: int,
        vehicle_type: Literal["AMBULANCE", "POLICE", "FIRE_TRUCK"],
        centroid: Tuple[float, float],
        timestamp: Optional[float] = None,
        speed_kmh: Optional[float] = None,
        confidence: float = 1.0,
    ) -> EmergencyDetection:
        """
        Registers or updates an emergency vehicle track.
        """
        ts = timestamp if timestamp is not None else time.time()
        lane_id = self.assign_lane(centroid[0])

        detection = EmergencyDetection(
            track_id=track_id,
            vehicle_type=vehicle_type,
            lane_id=lane_id,
            centroid=centroid,
            timestamp=ts,
            speed_kmh=speed_kmh,
            confidence=confidence,
        )
        self.active_emergency_tracks[track_id] = detection
        return detection

    def clear_track(self, track_id: int):
        """Removes an emergency vehicle that has cleared the intersection corridor."""
        self.active_emergency_tracks.pop(track_id, None)

    def prune_stale(self, current_timestamp: float, max_idle_sec: float = 10.0):
        """Prunes emergency tracks that haven't been detected recently."""
        stale = [
            tid for tid, det in self.active_emergency_tracks.items()
            if (current_timestamp - det.timestamp) > max_idle_sec
        ]
        for tid in stale:
            del self.active_emergency_tracks[tid]
