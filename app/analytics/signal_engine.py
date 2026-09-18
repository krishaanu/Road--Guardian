import time
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("SignalEngine")

# Internal state memory per junction for timing and transition control
_signal_memory: Dict[str, Dict[str, Any]] = {}


def _get_memory(junction_id: str) -> Dict[str, Any]:
    """Retrieves or initializes state memory for a junction."""
    if junction_id not in _signal_memory:
        _signal_memory[junction_id] = {
            "last_switch_time": 0.0,
            "current_green_lane": None,
            "in_yellow_transition": False,
            "yellow_start_time": 0.0,
            "next_green_lane": None,
            "last_preemption_lane": None,
        }
    return _signal_memory[junction_id]


def calculate_adaptive_signal(
    junction_config: Dict[str, Any],
    lane_densities: Dict[str, int],
    emergency_events: Optional[Any] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Calculates signal states based on density metrics, 7s hold timer, and emergency overrides.

    :param junction_config: Dict defining active junction lanes & junction_id
    :param lane_densities: Map of lane_id -> vehicle_count (e.g., {"lane_1": 18, "lane_2": 24, ...})
    :param emergency_events: Map of lane_id -> bool (true if emergency vehicle/siren detected) or lane_id string
    """
    now = time.time()
    CYCLE_HOLD_SEC = 7.0
    YELLOW_HOLD_SEC = 2.0

    junction_id = "Junction_1"
    if isinstance(junction_config, dict):
        junction_id = junction_config.get("junction_id", junction_config.get("id", "Junction_1"))

    mem = _get_memory(junction_id)

    # Normalize lane densities
    densities = dict(lane_densities) if lane_densities else {}
    if not densities and isinstance(junction_config, dict):
        c_cfgs = junction_config.get("camera_configs", {})
        for c in c_cfgs:
            densities[c] = 0
        for p in junction_config.get("phases", []):
            p_id = p.get("phase_id", "Phase")
            if p_id not in densities:
                densities[p_id] = 0
        for l in junction_config.get("lanes", []):
            l_id = l if isinstance(l, str) else l.get("id", "lane")
            if l_id not in densities:
                densities[l_id] = 0

    if not densities:
        densities = {"lane_1": 0, "lane_2": 0}

    # Normalize emergency events (support dict, str, or None)
    if isinstance(emergency_events, str):
        emergency_events = {emergency_events: True}
    elif not isinstance(emergency_events, dict):
        emergency_events = {}

    # Check for kwargs preemption_cam fallback
    if "preemption_cam" in kwargs and kwargs["preemption_cam"]:
        emergency_events[kwargs["preemption_cam"]] = True

    # If any standard LANE_ format is present, ensure default 4 lanes exist in densities
    if any(k.startswith("LANE_") for k in densities):
        for l in ["LANE_1", "LANE_2", "LANE_3", "LANE_4"]:
            if l not in densities:
                densities[l] = 0

    # 1. Check Emergency Overrides First (Priority Rule)
    emergency_lane = next((lane for lane, active in emergency_events.items() if active), None)

    if emergency_lane:
        # If emergency lane is not in densities, add it
        if emergency_lane not in densities:
            densities[emergency_lane] = 1

        # Trigger audit log when emergency preemption initiates or changes lane
        if mem.get("last_preemption_lane") != emergency_lane:
            try:
                from app.database.database import DatabaseManager
                db = DatabaseManager()
                time_saved_sec = 25.0
                db.log_preemption(
                    camera_id=str(emergency_lane),
                    junction_id=str(junction_id),
                    time_saved_sec=time_saved_sec,
                    vehicle_type="EMERGENCY_VEHICLE"
                )
                logger.info(f"Emergency preemption triggered on {emergency_lane} at {junction_id} (Saved ~{time_saved_sec}s)")
            except Exception as err:
                logger.warning(f"Could not log emergency preemption event to database: {err}")
            mem["last_preemption_lane"] = emergency_lane

        mem["current_green_lane"] = emergency_lane
        mem["in_yellow_transition"] = False
        mem["last_switch_time"] = now

        return build_signal_response(
            junction_id=junction_id,
            densities=densities,
            green_lane=emergency_lane,
            emergency=True,
            green_timer=7
        )

    # Reset preemption state if emergency cleared
    mem["last_preemption_lane"] = None

    # 2. Determine Lane with Highest Density (Assign GREEN to Lane with MAXIMUM Vehicle Count)
    highest_density_lane = max(densities, key=densities.get) if densities else list(densities.keys())[0]

    # Initialize first green lane if unset or stale
    if mem["current_green_lane"] is None or mem["current_green_lane"] not in densities:
        mem["current_green_lane"] = highest_density_lane
        mem["last_switch_time"] = now

    current_green = mem["current_green_lane"]

    # 3. Handle Yellow Buffer State (2s transition)
    if mem["in_yellow_transition"]:
        elapsed_yellow = now - mem["yellow_start_time"]
        if elapsed_yellow >= YELLOW_HOLD_SEC:
            _signal_memory[junction_id]["in_yellow_transition"] = False
            _signal_memory[junction_id]["current_green_lane"] = mem["next_green_lane"]
            _signal_memory[junction_id]["last_switch_time"] = now
            current_green = mem["next_green_lane"]
            _signal_memory[junction_id]["next_green_lane"] = None

    # 4. Enforce Minimum 7-Second Hold Cycle
    elif now - mem["last_switch_time"] >= CYCLE_HOLD_SEC:
        if highest_density_lane != current_green:
            _signal_memory[junction_id]["in_yellow_transition"] = True
            _signal_memory[junction_id]["yellow_start_time"] = now
            _signal_memory[junction_id]["next_green_lane"] = highest_density_lane

    # Calculate remaining timers
    if mem["in_yellow_transition"]:
        yellow_timer = max(1, int(round(YELLOW_HOLD_SEC - (now - mem["yellow_start_time"]))))
        green_timer = 0
    else:
        yellow_timer = 0
        elapsed_green = now - mem["last_switch_time"]
        green_timer = max(1, int(round(CYCLE_HOLD_SEC - elapsed_green)))

    return build_signal_response(
        junction_id=junction_id,
        densities=densities,
        green_lane=current_green,
        is_yellow=mem["in_yellow_transition"],
        next_green=mem.get("next_green_lane"),
        emergency=False,
        green_timer=green_timer,
        yellow_timer=yellow_timer
    )


def build_signal_response(
    junction_id_or_densities: Any = "JUNCTION_01",
    densities: Optional[Dict[str, int]] = None,
    green_lane: str = "LANE_1",
    is_yellow: bool = False,
    next_green: Optional[str] = None,
    emergency: bool = False,
    green_timer: int = 7,
    yellow_timer: int = 2,
    **kwargs
) -> Dict[str, Any]:
    """Builds structured signal response dictionary matching frontend expectations."""
    if isinstance(junction_id_or_densities, dict):
        densities = junction_id_or_densities
        junction_id = kwargs.get("junction_id", "JUNCTION_01")
    else:
        junction_id = str(junction_id_or_densities)

    target_densities = dict(densities) if densities else {}
    signals = {}

    for lane_id, count in target_densities.items():
        if emergency:
            state = "GREEN" if lane_id == green_lane else "RED"
            timer = green_timer if lane_id == green_lane else 0
        elif is_yellow:
            if lane_id == green_lane or lane_id == next_green:
                state = "YELLOW"
                timer = yellow_timer
            else:
                state = "RED"
                timer = 0
        else:
            state = "GREEN" if lane_id == green_lane else "RED"
            timer = green_timer if lane_id == green_lane else 0

        action = (
            "PROCEED (GREEN)"
            if state == "GREEN"
            else ("TRANSITION (YELLOW)" if state == "YELLOW" else "HOLD (RED)")
        )

        signals[lane_id] = {
            "state": state,
            "density": int(count),
            "timer": timer,
            "action": action
        }

    max_d = max(target_densities.values()) if target_densities else 0
    los = "LOS A" if max_d < 10 else ("LOS C" if max_d < 25 else "LOS E/F (CONGESTED)")

    return {
        "junction_id": junction_id,
        "active_phase": green_lane,
        "emergency_preemption": emergency,
        "signals": signals,
        "phase_green_times": {green_lane: green_timer if not is_yellow else yellow_timer},
        "level_of_service": los
    }
