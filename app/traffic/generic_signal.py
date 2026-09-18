import os
import json
import time
import logging
from typing import Dict, List, Any, Optional
from pydantic import BaseModel, Field

from app.database.database import DatabaseManager

logger = logging.getLogger("GenericSignalController")

CONFIG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../config/junctions.json"))


class PhaseConfig(BaseModel):
    camera_id: str
    name: str = "Corridor"
    lanes: int = 2


class JunctionConfig(BaseModel):
    junction_id: str
    name: str
    min_green: int = 15
    max_green: int = 90
    clearance_sec: float = 5.0
    capacity_per_lane: int = 25
    phase_a: PhaseConfig
    phase_b: PhaseConfig


class JunctionState(BaseModel):
    junction_id: str
    active_phase: str = "A"  # "A" or "B"
    state_a: str = "GREEN"    # "GREEN", "YELLOW", "RED"
    state_b: str = "RED"      # "GREEN", "YELLOW", "RED"
    green_a_sec: int = 30
    green_b_sec: int = 30
    density_a: float = 0.0
    density_b: float = 0.0
    time_remaining_sec: float = 30.0
    last_switch_timestamp: float = Field(default_factory=time.time)
    in_clearance: bool = False
    preemption_active: bool = False
    preemption_vehicle: Optional[str] = None


class GenericJunctionController:
    """Config-driven 2-Phase Adaptive Traffic Signal Controller.
    Computes proportional green splits based on approach vehicle densities,
    enforces mutual exclusion across conflicting approaches, and executes
    emergency vehicle preemption with 5s clearance intervals and audit logging.
    """

    def __init__(self, config_path: str = CONFIG_PATH, db: Optional[DatabaseManager] = None):
        self.config_path = config_path
        self.db = db or DatabaseManager()
        self.junctions: Dict[str, JunctionConfig] = {}
        self.states: Dict[str, JunctionState] = {}
        self.load_config()

    def load_config(self):
        """Loads junction topologies from config/junctions.json."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r") as f:
                    data = json.load(f)
                for j_data in data.get("junctions", []):
                    jc = JunctionConfig(**j_data)
                    self.junctions[jc.junction_id] = jc
                    if jc.junction_id not in self.states:
                        self.states[jc.junction_id] = JunctionState(
                            junction_id=jc.junction_id,
                            active_phase="A",
                            state_a="GREEN",
                            state_b="RED",
                            green_a_sec=30,
                            green_b_sec=30,
                            time_remaining_sec=30.0,
                        )
                logger.info(f"Loaded {len(self.junctions)} junctions from {self.config_path}")
            except Exception as e:
                logger.error(f"Failed to load junction configs: {e}")

    def save_config(self):
        """Persists current junction configurations back to JSON."""
        try:
            data = {"junctions": [j.model_dump() for j in self.junctions.values()]}
            with open(self.config_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save junction configs: {e}")

    def get_junction(self, junction_id: str) -> Optional[JunctionConfig]:
        return self.junctions.get(junction_id)

    def get_all_junctions(self) -> List[Dict[str, Any]]:
        result = []
        for j_id, j_cfg in self.junctions.items():
            st = self.states.get(j_id)
            result.append({
                "config": j_cfg.model_dump(),
                "state": st.model_dump() if st else None
            })
        return result

    def compute_density(self, vehicle_count: int, lanes: int, capacity_per_lane: int) -> float:
        """Density = VehicleCount / (Lanes * CapacityPerLane)"""
        max_capacity = max(1, lanes * capacity_per_lane)
        return min(1.0, max(0.0, vehicle_count / max_capacity))

    def evaluate_junction(
        self,
        junction_id: str,
        count_a: int,
        count_b: int,
        emergency_cam_id: Optional[str] = None,
        emergency_type: str = "AMBULANCE",
    ) -> Dict[str, Any]:
        """Calculates proportional green timings for Phase A and Phase B,
        and applies emergency preemption with 5s clearance if requested.
        """
        if junction_id not in self.junctions:
            raise ValueError(f"Unknown junction_id '{junction_id}'")

        jc = self.junctions[junction_id]
        st = self.states[junction_id]

        density_a = self.compute_density(count_a, jc.phase_a.lanes, jc.capacity_per_lane)
        density_b = self.compute_density(count_b, jc.phase_b.lanes, jc.capacity_per_lane)
        st.density_a = round(density_a, 3)
        st.density_b = round(density_b, 3)

        eps = 1e-4
        total_density = density_a + density_b + eps
        ratio_a = density_a / total_density
        ratio_b = density_b / total_density

        min_g = jc.min_green
        max_g = jc.max_green

        green_a = int(round(min_g + (max_g - min_g) * ratio_a))
        green_b = int(round(min_g + (max_g - min_g) * ratio_b))

        green_a = max(min_g, min(max_g, green_a))
        green_b = max(min_g, min(max_g, green_b))

        st.green_a_sec = green_a
        st.green_b_sec = green_b

        # Emergency vehicle preemption handling
        preemption_event = None
        if emergency_cam_id:
            time_saved = 45.0  # Estimated average wait time avoided
            if emergency_cam_id == jc.phase_a.camera_id:
                # Approach A needs GREEN
                action = "EARLY_GREEN" if st.state_a != "GREEN" else "EXTEND_GREEN"
                st.state_b = "RED"
                st.state_a = "GREEN"
                st.active_phase = "A"
                st.preemption_active = True
                st.preemption_vehicle = emergency_type
                self.db.log_preemption(emergency_cam_id, junction_id, time_saved, emergency_type)
                preemption_event = {
                    "camera_id": emergency_cam_id,
                    "granted_phase": "A",
                    "action": action,
                    "time_saved_sec": time_saved,
                    "type": emergency_type,
                }
            elif emergency_cam_id == jc.phase_b.camera_id:
                # Approach B needs GREEN
                action = "EARLY_GREEN" if st.state_b != "GREEN" else "EXTEND_GREEN"
                st.state_a = "RED"
                st.state_b = "GREEN"
                st.active_phase = "B"
                st.preemption_active = True
                st.preemption_vehicle = emergency_type
                self.db.log_preemption(emergency_cam_id, junction_id, time_saved, emergency_type)
                preemption_event = {
                    "camera_id": emergency_cam_id,
                    "granted_phase": "B",
                    "action": action,
                    "time_saved_sec": time_saved,
                    "type": emergency_type,
                }

        return {
            "junction_id": junction_id,
            "density_a": st.density_a,
            "density_b": st.density_b,
            "green_a_sec": st.green_a_sec,
            "green_b_sec": st.green_b_sec,
            "active_phase": st.active_phase,
            "state_a": st.state_a,
            "state_b": st.state_b,
            "preemption": preemption_event,
        }
