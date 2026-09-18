import time
from typing import Dict, Any, Optional

class SignalAllocator:
    """
    Autonomous Adaptive Signal State Machine with:
    - 10.0s Minimum Green Hold (prevents rapid accident-prone toggling)
    - 3-Vehicle Hysteresis Threshold (prevents oscillating switches on minor fluctuations)
    - 2.0s Yellow Transition Safety Buffer
    - Immediate Emergency Vehicle Preemption Override
    """
    def __init__(
        self,
        green_hold_seconds: float = 10.0,
        yellow_hold_seconds: float = 2.0,
        min_safety_hold_seconds: float = 5.0,
        hysteresis_threshold: int = 3
    ):
        self.MIN_GREEN_HOLD_SEC = green_hold_seconds
        self.YELLOW_HOLD_SECONDS = yellow_hold_seconds
        self.MIN_SAFETY_HOLD_SECONDS = min_safety_hold_seconds
        self.HYSTERESIS_THRESHOLD = hysteresis_threshold

        self.lane_densities: Dict[str, int] = {}
        self.lane_special_vehicles: Dict[str, bool] = {}
        
        self.active_green_lane: str = "LANE_1"
        self.current_state: str = "GREEN"  # "GREEN", "YELLOW"
        self.state_start_time: float = time.time()
        self.last_served_lane: Optional[str] = None
        self.pending_next_lane: Optional[str] = None

    def update_density(self, lane_id: str, vehicle_count: int, has_special_vehicle: bool = False):
        self.lane_densities[lane_id] = max(0, vehicle_count)
        self.lane_special_vehicles[lane_id] = has_special_vehicle

    def tick(self) -> Dict[str, Any]:
        now = time.time()
        elapsed = now - self.state_start_time

        default_lanes = ["LANE_1", "LANE_2", "LANE_3", "LANE_4"]
        for l in default_lanes:
            if l not in self.lane_densities:
                self.lane_densities[l] = 0
                self.lane_special_vehicles[l] = False

        # 1. Emergency Preemption Evaluation
        preempting_lane = None
        special_candidates = [
            l for l, active in self.lane_special_vehicles.items() 
            if active and l != self.active_green_lane
        ]
        if special_candidates and (elapsed >= self.MIN_SAFETY_HOLD_SECONDS or self.current_state == "YELLOW"):
            preempting_lane = max(special_candidates, key=lambda l: self.lane_densities.get(l, 0))

        # 2. State Machine Transitions
        if self.current_state == "YELLOW":
            if elapsed >= self.YELLOW_HOLD_SECONDS:
                self.current_state = "GREEN"
                self.active_green_lane = self.pending_next_lane or self.get_highest_density_lane(exclude=[self.active_green_lane])
                self.pending_next_lane = None
                self.state_start_time = now

        elif self.current_state == "GREEN":
            # Emergency vehicle gets immediate override
            if preempting_lane:
                self.current_state = "YELLOW"
                self.pending_next_lane = preempting_lane
                self.last_served_lane = self.active_green_lane
                self.state_start_time = now
            elif elapsed >= self.MIN_GREEN_HOLD_SEC:
                current_count = self.lane_densities.get(self.active_green_lane, 0)
                highest_lane = self.get_highest_density_lane(exclude=[self.active_green_lane])
                highest_count = self.lane_densities.get(highest_lane, 0)

                # Switch ONLY if competing lane exceeds current lane by hysteresis margin
                if highest_lane != self.active_green_lane and highest_count >= (current_count + self.HYSTERESIS_THRESHOLD):
                    self.current_state = "YELLOW"
                    self.pending_next_lane = highest_lane
                    self.last_served_lane = self.active_green_lane
                    self.state_start_time = now

        return self.snapshot()

    def get_highest_density_lane(self, exclude: list = None) -> str:
        exclude = exclude or []
        candidates = {l: c for l, c in self.lane_densities.items() if l not in exclude}
        if not candidates:
            return self.active_green_lane or "LANE_1"
        return max(candidates, key=candidates.get)

    def snapshot(self) -> Dict[str, Any]:
        signals = {}
        now = time.time()
        elapsed = now - self.state_start_time
        if self.current_state == "YELLOW":
            remaining_timer = max(1, int(round(self.YELLOW_HOLD_SECONDS - elapsed)))
        else:
            remaining_timer = max(1, int(round(self.MIN_GREEN_HOLD_SEC - elapsed)))

        for lane_id, count in self.lane_densities.items():
            if self.current_state == "YELLOW":
                if lane_id == self.active_green_lane or lane_id == self.pending_next_lane:
                    state = "YELLOW"
                    timer = remaining_timer
                else:
                    state = "RED"
                    timer = 0
            else:
                if lane_id == self.active_green_lane:
                    state = "GREEN"
                    timer = remaining_timer
                else:
                    state = "RED"
                    timer = 0

            signals[lane_id] = {
                "state": state,
                "density": count,
                "timer": timer,
                "special_vehicle": self.lane_special_vehicles.get(lane_id, False),
                "action": "PROCEED (GREEN)" if state == "GREEN" else ("TRANSITION (YELLOW)" if state == "YELLOW" else "HOLD (RED)")
            }

        return {
            "active_phase": self.active_green_lane,
            "current_state": self.current_state,
            "signals": signals
        }


def format_speed(raw_kmh: float, track_frame_count: int) -> str:
    """Format speed values to suppress noise floor errors."""
    if track_frame_count < 5:
        return "Not enough data"
    if raw_kmh < 1.0:
        return "Vehicle stopped"
    if raw_kmh < 3.0:
        return "Not confirmed"
    return f"{raw_kmh:.1f} km/h"
