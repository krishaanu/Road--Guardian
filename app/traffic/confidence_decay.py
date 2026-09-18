"""
Distance Confidence Decay Model for RoadGuardian ITS.

Calculates distance-based confidence decay relative to the calibrated blue datum line:
- 100.0% confidence at the blue line.
- Decreases by 0.25% per calibration band step (default: 5.0 meters moving towards yellow line).
- Formula: confidence = max(100.0 - (0.25 * bands_from_blue), floor_value) with floor_value = 25.0.
- Vehicles passing the yellow line or falling below floor confidence get tagged:
  LOW_CONFIDENCE / NEEDS_REVIEW and excluded from speed/density statistics (exclude_from_stats = True).
"""

from typing import Tuple, Optional
from pydantic import BaseModel, Field
from app.traffic.zone_calibration import CalibratedHomography


class ConfidenceEvaluation(BaseModel):
    """
    Result of evaluating distance confidence for an individual vehicle.
    """
    track_id: int
    axial_distance_m: float = Field(..., description="Longitudinal distance along the road in meters")
    bands_from_blue: float = Field(..., description="Distance in band steps from the blue datum line")
    confidence_score: float = Field(..., ge=0.0, le=100.0, description="Calculated confidence score [0.0 - 100.0]")
    status: str = Field(..., description="'OPTIMAL', 'VALID', 'LOW_CONFIDENCE', or 'NEEDS_REVIEW'")
    needs_review: bool = Field(False, description="Flagged for manual operator review")
    exclude_from_stats: bool = Field(False, description="Whether to exclude this vehicle from speed/density analytics")
    reason: Optional[str] = None


class DistanceConfidenceDecayModel:
    """
    Calculates distance-dependent measurement confidence and exclusion gating.
    """

    def __init__(
        self,
        homography: CalibratedHomography,
        band_step_m: float = 5.0,
        decay_rate_per_band: float = 0.25,
        floor_value: float = 25.0,
    ):
        self.homography = homography
        self.band_step_m = max(0.1, band_step_m)
        self.decay_rate_per_band = decay_rate_per_band
        self.floor_value = floor_value

        # Extract calibrated reference distances from anchors
        anchors = self.homography.config.anchors
        blue_anchor = anchors.get(self.homography.config.datum_line_id)
        yellow_anchor = anchors.get(self.homography.config.limit_line_id)

        self.blue_distance_m = blue_anchor.real_distance_m if blue_anchor else 35.0
        self.yellow_distance_m = yellow_anchor.real_distance_m if yellow_anchor else 110.0

    def evaluate(self, track_id: int, centroid: Tuple[float, float]) -> ConfidenceEvaluation:
        """
        Evaluates the distance confidence for a vehicle at a given pixel centroid.
        """
        # Compute real-world longitudinal distance
        axial_dist = self.homography.get_axial_distance(centroid)

        # Distance from blue line (moving towards yellow line)
        dist_from_blue = max(0.0, axial_dist - self.blue_distance_m)
        bands_from_blue = dist_from_blue / self.band_step_m

        # Step-wise linear decay formula:
        # confidence = max(100.0 - (0.25 * bands_from_blue), floor_value)
        raw_confidence = 100.0 - (self.decay_rate_per_band * bands_from_blue)
        confidence = max(raw_confidence, self.floor_value)
        confidence = min(100.0, round(float(confidence), 2))

        # Check exclusion criteria:
        # 1. Vehicle has passed beyond the yellow limit line
        passed_yellow = axial_dist >= self.yellow_distance_m
        # 2. Confidence score fell to or below floor value
        hit_floor = confidence <= self.floor_value

        if passed_yellow:
            status = "NEEDS_REVIEW"
            needs_review = True
            exclude_from_stats = True
            reason = f"Vehicle exceeded yellow limit line ({axial_dist:.1f}m >= {self.yellow_distance_m:.1f}m)"
        elif hit_floor:
            status = "LOW_CONFIDENCE"
            needs_review = True
            exclude_from_stats = True
            reason = f"Confidence score reached floor limit ({confidence:.1f}% <= {self.floor_value:.1f}%)"
        elif confidence >= 90.0:
            status = "OPTIMAL"
            needs_review = False
            exclude_from_stats = False
            reason = "Inside optimal zone near blue reference line"
        else:
            status = "VALID"
            needs_review = False
            exclude_from_stats = False
            reason = f"Confidence within acceptable tolerance ({confidence:.1f}%)"

        return ConfidenceEvaluation(
            track_id=track_id,
            axial_distance_m=round(axial_dist, 2),
            bands_from_blue=round(bands_from_blue, 2),
            confidence_score=confidence,
            status=status,
            needs_review=needs_review,
            exclude_from_stats=exclude_from_stats,
            reason=reason,
        )
