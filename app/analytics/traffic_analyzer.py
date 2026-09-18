import time
import numpy as np
from typing import List, Optional


class LocalTrafficAnalyzer:
    """
    Implements Highway Capacity Manual (HCM) Level of Service (LOS) model
    with Temporal Hysteresis and Dual-Signal Gating for motorway traffic.
    """

    STATE_SMOOTH = "SMOOTH"
    STATE_HEAVY_FLOW = "HEAVY_FLOW"
    STATE_CONGESTED = "CONGESTED"

    def __init__(
        self,
        visible_road_length_km: float = 0.20,
        num_lanes: int = 3,
        history_window: int = 150,
        hysteresis_sec: float = 5.0,
        fps: float = 30.0,
        **kwargs,
    ):
        self.visible_road_length_km = max(float(visible_road_length_km), 0.001)
        self.num_lanes = max(int(num_lanes), 1)
        self.history_window = max(int(history_window), 1)
        self.hysteresis_sec = max(float(hysteresis_sec), 0.1)
        self.fps = max(float(fps), 1.0)

        self.count_history: List[float] = []
        self.speed_history: List[float] = []

        # State Machine & Hysteresis Timers
        self.current_state = self.STATE_SMOOTH
        self.congestion_start_time: Optional[float] = None
        self.recovery_start_time: Optional[float] = None

    def calculate_density(self, vehicle_count: float) -> float:
        """Calculates vehicle density in veh/km/lane."""
        road_capacity_factor = self.visible_road_length_km * self.num_lanes
        return float(vehicle_count) / road_capacity_factor

    def get_level_of_service(self, density: float) -> str:
        """
        Determines Highway Capacity Manual (HCM) Level of Service (LOS):
        - LOS A: < 7.0 veh/km/lane (Free flow)
        - LOS B: 7.0 - 11.0 veh/km/lane (Reasonably free flow)
        - LOS C: 11.0 - 16.0 veh/km/lane (Stable flow)
        - LOS D: 16.0 - 22.0 veh/km/lane (Approaching unstable flow)
        - LOS E: 22.0 - 28.0 veh/km/lane (Unstable flow / capacity)
        - LOS F: >= 28.0 veh/km/lane (Forced breakdown flow)
        """
        if density < 7.0:
            return "A"
        elif density < 11.0:
            return "B"
        elif density < 16.0:
            return "C"
        elif density < 22.0:
            return "D"
        elif density < 28.0:
            return "E"
        else:
            return "F"

    def analyze_frame(
        self,
        current_vehicle_count: int,
        speeds: list,
        timestamp: Optional[float] = None,
        is_non_linear: bool = False,
    ) -> dict:
        """
        Analyzes vehicle density and speed using Dual-Signal Gate and Temporal Hysteresis.

        - Dual-Signal Gate: Congestion requires BOTH Density >= 22.0 veh/km/lane AND Average Speed < 40 km/h.
          High density with moving traffic (speed >= 40 km/h) remains in HEAVY_FLOW.
        - Temporal Hysteresis: Requires qualification condition for >= 5 seconds to enter CONGESTED,
          and >= 5 seconds of recovery to clear back to HEAVY_FLOW / SMOOTH.
        - Non-linear zones suppress false standard density alerts.
        """
        now = time.time() if timestamp is None else timestamp
        avg_speed = float(np.mean(speeds)) if speeds else 65.0

        self.count_history.append(float(current_vehicle_count))
        self.speed_history.append(avg_speed)

        if len(self.count_history) > self.history_window:
            self.count_history.pop(0)
            self.speed_history.pop(0)

        smoothed_count = float(np.mean(self.count_history))
        smoothed_speed = float(np.mean(self.speed_history))

        smoothed_density = self.calculate_density(smoothed_count)
        los = self.get_level_of_service(smoothed_density)

        # Dual-Signal Gate: Only flag true congestion if Density >= 22.0 AND Average Speed < 40 km/h
        # Non-linear road layouts (e.g. roundabouts) suppress standard linear congestion alerts
        congestion_qualified = (smoothed_density >= 22.0) and (smoothed_speed < 40.0) and (not is_non_linear)

        # Temporal Hysteresis State Transition Logic
        if self.current_state != self.STATE_CONGESTED:
            # Check for transition into CONGESTED
            if congestion_qualified:
                if self.congestion_start_time is None:
                    self.congestion_start_time = now

                elapsed = now - self.congestion_start_time
                if elapsed >= self.hysteresis_sec:
                    self.current_state = self.STATE_CONGESTED
                    self.congestion_start_time = None
            else:
                self.congestion_start_time = None

                # Normal state assignment when not congested
                if smoothed_density >= 16.0 or (smoothed_density >= 22.0 and smoothed_speed >= 40.0):
                    self.current_state = self.STATE_HEAVY_FLOW
                else:
                    self.current_state = self.STATE_SMOOTH
        else:
            # Currently in CONGESTED state - require recovery condition sustained for >= 5s to clear
            if not congestion_qualified:
                if self.recovery_start_time is None:
                    self.recovery_start_time = now

                elapsed = now - self.recovery_start_time
                if elapsed >= self.hysteresis_sec:
                    if smoothed_density >= 16.0:
                        self.current_state = self.STATE_HEAVY_FLOW
                    else:
                        self.current_state = self.STATE_SMOOTH
                    self.recovery_start_time = None
                    self.congestion_start_time = None
            else:
                self.recovery_start_time = None

        # Build user-facing message
        if is_non_linear:
            message = f"Non-Linear Traffic Zone (LOS {los})"
        elif self.current_state == self.STATE_CONGESTED:
            message = f"Congestion / Breakdown (LOS {los})"
        elif self.current_state == self.STATE_HEAVY_FLOW:
            message = f"Heavy Traffic Flow (LOS {los})"
        else:
            message = f"Normal Flow (LOS {los})"

        return {
            "status": self.current_state,
            "message": message,
            "avg_speed_kmh": round(smoothed_speed, 1),
            "active_vehicles": int(round(smoothed_count)),
            "density_veh_km_lane": round(smoothed_density, 2),
            "los": los,
        }

    def reset(self):
        """Resets the history and hysteresis state."""
        self.count_history.clear()
        self.speed_history.clear()
        self.current_state = self.STATE_SMOOTH
        self.congestion_start_time = None
        self.recovery_start_time = None