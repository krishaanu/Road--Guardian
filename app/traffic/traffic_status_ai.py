"""
Traffic Status AI Scoring Module for RoadGuardian ITS.

Computes confidence-weighted vehicle density (veh/km/lane) and average speed,
classifies roadway flow into Level of Service (LOS) classes:
NORMAL, SLOW, CONGESTED, SEVERE,
and produces an AI signal recommendation badge payload (e.g., 'Rec Green: +8s').
"""

from typing import List, Dict, Any, Optional
import numpy as np
from pydantic import BaseModel, Field
from app.traffic.corridor_speed import VehicleTrackState
from app.traffic.confidence_decay import ConfidenceEvaluation


class VehicleAnalyticsInput(BaseModel):
    """
    Combined metrics for an observed vehicle in a frame evaluation.
    """
    track_id: int
    speed_kmh: Optional[float] = None
    confidence_score: float = 100.0
    exclude_from_stats: bool = False
    in_corridor: bool = True


class SignalRecommendationBadge(BaseModel):
    """
    AI Traffic Signal recommendation badge payload.
    """
    badge: str = Field(..., description="Operator badge string, e.g. 'Rec Green: +8s'")
    adjustment_seconds: int = Field(..., description="Recommended green adjustment in seconds (+/-)")
    los_class: str = Field(..., description="Traffic flow state: 'NORMAL', 'SLOW', 'CONGESTED', 'SEVERE'")
    hcm_los_letter: str = Field("A", description="HCM Letter grade A-F")
    weighted_density_veh_km_lane: float
    weighted_avg_speed_kmh: float
    total_vehicles_observed: int
    qualified_vehicles_count: int
    mean_confidence_score: float
    badge_color: str = Field("emerald", description="Tailwind color token for UI rendering")
    policy_note: str


class TrafficStatusAIScorer:
    """
    Evaluates traffic states using confidence-weighted aggregation and generates adaptive signal recommendations.
    """

    def __init__(
        self,
        visible_road_length_km: float = 0.20,
        num_lanes: int = 3,
    ):
        self.road_length_km = max(0.01, visible_road_length_km)
        self.num_lanes = max(1, num_lanes)
        self.road_capacity_factor = self.road_length_km * self.num_lanes

    def score(
        self,
        vehicles: List[VehicleAnalyticsInput],
        current_light_state: str = "RED",
        cycle_remaining_sec: int = 20,
    ) -> SignalRecommendationBadge:
        """
        Calculates confidence-weighted density, speed, LOS classification, and AI signal timing adjustment.
        """
        # Filter out vehicles marked for exclusion (out-of-zone, passed yellow line, or low confidence)
        qualified = [
            v for v in vehicles
            if not v.exclude_from_stats and v.confidence_score > 25.0
        ]

        if not qualified:
            # Empty road / baseline state
            return SignalRecommendationBadge(
                badge="Rec Green: +0s",
                adjustment_seconds=0,
                los_class="NORMAL",
                hcm_los_letter="A",
                weighted_density_veh_km_lane=0.0,
                weighted_avg_speed_kmh=65.0,
                total_vehicles_observed=len(vehicles),
                qualified_vehicles_count=0,
                mean_confidence_score=100.0,
                badge_color="emerald",
                policy_note="Corridor clear. Standard timing schedule active.",
            )

        # 1. Confidence weights: w_i = confidence / 100.0
        weights = np.array([v.confidence_score / 100.0 for v in qualified], dtype=np.float32)
        total_weight = float(np.sum(weights))

        # 2. Weighted Vehicle Count & Density
        weighted_density = float(total_weight / self.road_capacity_factor)

        # 3. Weighted Average Speed
        speeds_with_weights = [
            (v.speed_kmh, v.confidence_score / 100.0)
            for v in qualified
            if v.speed_kmh is not None and v.speed_kmh > 0
        ]

        if speeds_with_weights:
            spds, spd_weights = zip(*speeds_with_weights)
            weighted_avg_speed = float(np.average(spds, weights=spd_weights))
        else:
            # Default to free-flow speed if no valid corridor speed measurements yet
            weighted_avg_speed = 60.0

        mean_confidence = float(np.mean([v.confidence_score for v in qualified]))

        # 4. Bucketing into LOS classes: NORMAL, SLOW, CONGESTED, SEVERE
        # Also determine HCM Letter grade
        if weighted_density < 11.0 and weighted_avg_speed >= 50.0:
            los_class = "NORMAL"
            hcm_los = "A" if weighted_density < 7.0 else "B"
            adjustment = 0
            badge_text = "Rec Green: 0s"
            color = "emerald"
            policy = f"Free-flow motorway conditions ({weighted_density:.1f} veh/km/lane, {weighted_avg_speed:.1f} km/h). Maintaining baseline cycle."
        elif weighted_density < 22.0 or (35.0 <= weighted_avg_speed < 50.0):
            los_class = "SLOW"
            hcm_los = "C" if weighted_density < 16.0 else "D"
            adjustment = 5
            badge_text = "Rec Green: +5s"
            color = "amber"
            policy = f"Moderate vehicle concentration ({weighted_density:.1f} veh/km/lane). Minor green extension recommended to clear queue."
        elif (22.0 <= weighted_density < 28.0) or (20.0 <= weighted_avg_speed < 35.0):
            los_class = "CONGESTED"
            hcm_los = "E"
            adjustment = 8
            badge_text = "Rec Green: +8s"
            color = "orange"
            policy = f"Heavy flow approaching capacity ({weighted_density:.1f} veh/km/lane, {weighted_avg_speed:.1f} km/h). Adaptive +8s green extension."
        else:
            los_class = "SEVERE"
            hcm_los = "F"
            adjustment = 15
            badge_text = "Rec Green: +15s"
            color = "rose"
            policy = f"Breakdown flow in verified corridor ({weighted_density:.1f} veh/km/lane, {weighted_avg_speed:.1f} km/h). Maximum emergency green extension triggered."

        return SignalRecommendationBadge(
            badge=badge_text,
            adjustment_seconds=adjustment,
            los_class=los_class,
            hcm_los_letter=hcm_los,
            weighted_density_veh_km_lane=round(weighted_density, 1),
            weighted_avg_speed_kmh=round(weighted_avg_speed, 1),
            total_vehicles_observed=len(vehicles),
            qualified_vehicles_count=len(qualified),
            mean_confidence_score=round(mean_confidence, 1),
            badge_color=color,
            policy_note=policy,
        )
