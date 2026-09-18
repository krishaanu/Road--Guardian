"""
Single Source of Truth Live Detection State for RoadGuardian ITS.
Maintains synchronized detection, tracking, trajectory, and signal state per camera.
"""

import time
import threading
from typing import Dict, List, Tuple, Any, Optional
from dataclasses import dataclass, field


@dataclass
class CameraLiveState:
    camera_id: str
    vehicle_count: int = 0
    tracked_count: int = 0
    active_tracks: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    trajectories: Dict[int, List[Tuple[int, int]]] = field(default_factory=dict)
    speeds: Dict[int, float] = field(default_factory=dict)
    emergency_active: bool = False
    density: float = 0.0
    los: str = "LOS A"
    signal_state: str = "GREEN"
    signal_countdown: int = 15
    recommended_green: int = 20
    last_updated: float = field(default_factory=time.time)


class LiveStateManager:
    """Thread-safe centralized manager for live surveillance state."""

    def __init__(self):
        self._lock = threading.Lock()
        self._states: Dict[str, CameraLiveState] = {}

    def get_or_create(self, camera_id: str) -> CameraLiveState:
        with self._lock:
            if camera_id not in self._states:
                self._states[camera_id] = CameraLiveState(camera_id=camera_id)
            return self._states[camera_id]

    def update_camera(
        self,
        camera_id: str,
        vehicle_count: int,
        active_tracks: Dict[int, Dict[str, Any]],
        trajectories: Dict[int, List[Tuple[int, int]]],
        speeds: Dict[int, float],
        emergency_active: bool = False,
    ):
        with self._lock:
            if camera_id not in self._states:
                self._states[camera_id] = CameraLiveState(camera_id=camera_id)

            st = self._states[camera_id]
            st.vehicle_count = vehicle_count
            st.tracked_count = len(active_tracks)
            st.active_tracks = active_tracks
            st.trajectories = trajectories
            st.speeds = speeds
            st.emergency_active = emergency_active
            st.density = round(vehicle_count * 1.5, 1)

            # Compute Level of Service based on HCM density
            if vehicle_count < 5:
                st.los = "LOS A"
            elif vehicle_count < 10:
                st.los = "LOS B"
            elif vehicle_count < 18:
                st.los = "LOS C"
            elif vehicle_count < 25:
                st.los = "LOS D"
            else:
                st.los = "LOS E/F"

            st.last_updated = time.time()

    def update_signal(
        self,
        camera_id: str,
        state: str,
        countdown: int,
        recommended_green: int,
    ):
        with self._lock:
            if camera_id in self._states:
                st = self._states[camera_id]
                st.signal_state = state
                st.signal_countdown = countdown
                st.recommended_green = recommended_green

    def get_camera_state(self, camera_id: str) -> Optional[CameraLiveState]:
        with self._lock:
            return self._states.get(camera_id)

    def get_all_camera_counts(self) -> Dict[str, int]:
        with self._lock:
            return {cid: st.vehicle_count for cid, st in self._states.items()}

    def get_all_snapshots(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            snapshots = {}
            for cid, st in self._states.items():
                snapshots[cid] = {
                    "camera_id": st.camera_id,
                    "vehicle_count": st.vehicle_count,
                    "tracked_count": st.tracked_count,
                    "density": st.density,
                    "los": st.los,
                    "emergency_active": st.emergency_active,
                    "signal": {
                        "current_light": st.signal_state,
                        "countdown": st.signal_countdown,
                        "cycle_duration": 30,
                        "recommended_green": st.recommended_green,
                        "mode": "AI_ADAPTIVE",
                    },
                    "last_updated": st.last_updated,
                }
            return snapshots


# Module-level singleton
live_state_manager = LiveStateManager()
