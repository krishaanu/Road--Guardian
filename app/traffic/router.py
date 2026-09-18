"""
FastAPI Router for RoadGuardian Traffic Analytics & Signal Management.

Exposes REST endpoints for:
- Zone calibration & corridor tracking metadata
- Signal group configuration and manual light state override
- Emergency vehicle preemption, multi-group conflict resolution, and audit logging
- Frame-level evaluation of corridor speeds, confidence decay, and AI traffic status scoring
"""

from typing import List, Dict, Any, Optional, Tuple, Literal
import time
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.traffic.zone_calibration import (
    ZoneAnchor,
    ZoneCalibrationConfig,
    CalibratedHomography,
)
from app.traffic.corridor_speed import (
    CorridorSpeedDetector,
    VehicleTrackState,
)
from app.traffic.confidence_decay import (
    DistanceConfidenceDecayModel,
    ConfidenceEvaluation,
)
from app.traffic.traffic_status_ai import (
    TrafficStatusAIScorer,
    VehicleAnalyticsInput,
    SignalRecommendationBadge,
)
from app.traffic.emergency_vehicle import (
    EmergencyDetection,
    EmergencyVehicleTracker,
    PriorityRequestEvent,
)
from app.traffic.traffic_controller import (
    SignalGroup,
    SignalGroupCreate,
    SignalStateToggle,
    AuditLogEntry,
    signal_manager,
)

traffic_router = APIRouter(prefix="/api/traffic", tags=["Traffic Analytics & Signals"])

# Shared pipeline instances
homography = CalibratedHomography()
speed_detector = CorridorSpeedDetector(homography=homography)
confidence_model = DistanceConfidenceDecayModel(homography=homography)
ai_scorer = TrafficStatusAIScorer(visible_road_length_km=0.20, num_lanes=3)
emergency_tracker = EmergencyVehicleTracker()


# --- INPUT SCHEMAS FOR EVALUATION ---
class VehicleDetectionInput(BaseModel):
    track_id: int
    centroid: Tuple[float, float] = Field(..., description="(x, y) pixel coordinates of vehicle centroid")
    timestamp: Optional[float] = Field(None, description="Detection timestamp (defaults to current time)")
    vehicle_type: Optional[str] = "Car"


class FrameEvaluationRequest(BaseModel):
    camera_id: str = "CAMERA_01"
    timestamp: Optional[float] = None
    detections: List[VehicleDetectionInput] = Field(default_factory=list)


class FrameEvaluationResponse(BaseModel):
    camera_id: str
    timestamp: float
    total_detections: int
    corridor_active_count: int
    vehicles: List[Dict[str, Any]]
    ai_recommendation: SignalRecommendationBadge
    emergency_events: List[PriorityRequestEvent] = Field(default_factory=list)


# --- 1. ZONE CALIBRATION & HOMOGRAPHY ENDPOINTS ---
@traffic_router.get("/calibration", response_model=ZoneCalibrationConfig)
def get_calibration():
    """Returns the current zone calibration anchors, corridor definition, and road parameters."""
    return homography.config


@traffic_router.post("/calibration")
def update_calibration(config: ZoneCalibrationConfig):
    """Updates zone calibration anchors and recomputes the homography matrix and corridor."""
    global homography, speed_detector, confidence_model
    homography = CalibratedHomography(config)
    speed_detector = CorridorSpeedDetector(homography=homography)
    confidence_model = DistanceConfidenceDecayModel(homography=homography)
    return {"status": "success", "message": "Zone calibration and corridor updated successfully."}


# --- 2. SIGNAL GROUP MANAGEMENT ENDPOINTS ---
@traffic_router.get("/signal-groups", response_model=List[SignalGroup])
def list_signal_groups():
    """Retrieves all configured signal groups, assigned lanes, and current light states."""
    return list(signal_manager.signal_groups.values())


@traffic_router.post("/signal-groups", response_model=SignalGroup)
def create_or_update_signal_group(config: SignalGroupCreate):
    """Assigns roadway lanes to a signal group or updates an existing group."""
    return signal_manager.register_signal_group(config)


@traffic_router.post("/signal-groups/{group_id}/state", response_model=SignalGroup)
def toggle_signal_state(group_id: str, toggle: SignalStateToggle):
    """
    Manually toggles or sets a signal group's state (RED, YELLOW, GREEN)
    with optional hold duration and operator rationale.
    """
    try:
        return signal_manager.set_signal_state(
            group_id=group_id,
            target_state=toggle.target_state,
            reason=toggle.reason or "Manual operator command",
            hold_duration_sec=toggle.hold_duration_sec,
            operator_id=toggle.operator_id,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


# --- 3. EMERGENCY VEHICLE OVERRIDE & CONFLICT RESOLUTION ---
@traffic_router.post("/emergency-detection")
def ingest_emergency_detection(detection: EmergencyDetection):
    """
    Registers an emergency vehicle detection. If approaching a RED signal group lane,
    triggers a PRIORITY_REQUEST and resolves multi-group conflicts via Earliest-Detection Priority.
    """
    # Track the detection
    emergency_tracker.update_detection(
        track_id=detection.track_id,
        vehicle_type=detection.vehicle_type,
        centroid=detection.centroid,
        timestamp=detection.timestamp,
        speed_kmh=detection.speed_kmh,
        confidence=detection.confidence,
    )

    # Process priority request against assigned signal groups
    event = signal_manager.process_emergency_detection(detection)

    # Apply conflict resolution if pending requests exist
    resolution = signal_manager.resolve_conflicts_and_apply_preemption()

    return {
        "status": "success",
        "priority_event_triggered": event is not None,
        "event": event.model_dump() if event else None,
        "preemption_resolution": resolution,
    }


@traffic_router.get("/audit-logs", response_model=List[AuditLogEntry])
def get_audit_logs(limit: int = Query(50, le=200)):
    """Retrieves immutable audit log entries recording signal overrides and conflict resolutions."""
    return signal_manager.audit_log[:limit]


# --- 4. INTEGRATED FRAME EVALUATION PIPELINE ---
@traffic_router.post("/evaluate-frame", response_model=FrameEvaluationResponse)
def evaluate_frame(req: FrameEvaluationRequest):
    """
    End-to-end evaluation pipeline processing vehicle detections through:
    1. Red corridor speed detection and OCR gating (OUT_OF_ZONE vs IN_CORRIDOR).
    2. Distance confidence decay model relative to the blue datum line.
    3. Traffic Status AI scoring (confidence-weighted density, speed, LOS, and AI badge).
    4. Emergency vehicle preemption verification.
    """
    ts = req.timestamp if req.timestamp is not None else time.time()
    evaluated_vehicles = []
    analytics_inputs = []
    emergency_events = []

    for det in req.detections:
        # 1. Update corridor speed tracking & OCR gating
        track_state = speed_detector.update_vehicle(
            track_id=det.track_id,
            centroid=det.centroid,
            timestamp=ts,
        )

        # 2. Compute distance confidence decay relative to blue line
        conf_eval = confidence_model.evaluate(
            track_id=det.track_id,
            centroid=det.centroid,
        )

        # 3. Check for emergency vehicle override
        if det.vehicle_type and det.vehicle_type.upper() in ["AMBULANCE", "POLICE", "FIRE_TRUCK"]:
            em_det = emergency_tracker.update_detection(
                track_id=det.track_id,
                vehicle_type=det.vehicle_type.upper(),  # type: ignore
                centroid=det.centroid,
                timestamp=ts,
                speed_kmh=track_state.filtered_speed_kmh or track_state.transit_speed_kmh,
                confidence=conf_eval.confidence_score / 100.0,
            )
            ev_event = signal_manager.process_emergency_detection(em_det)
            if ev_event:
                emergency_events.append(ev_event)

        # Build analytics input for AI scoring
        effective_speed = track_state.filtered_speed_kmh or track_state.transit_speed_kmh
        analytics_inputs.append(VehicleAnalyticsInput(
            track_id=det.track_id,
            speed_kmh=effective_speed,
            confidence_score=conf_eval.confidence_score,
            exclude_from_stats=conf_eval.exclude_from_stats or (track_state.status == "OUT_OF_ZONE"),
            in_corridor=(track_state.status == "IN_CORRIDOR"),
        ))

        evaluated_vehicles.append({
            "track_id": det.track_id,
            "vehicle_type": det.vehicle_type,
            "zone_status": track_state.status,
            "ocr_eligible": track_state.ocr_eligible,
            "ocr_priority": track_state.ocr_priority,
            "filtered_speed_kmh": track_state.filtered_speed_kmh,
            "transit_speed_kmh": track_state.transit_speed_kmh,
            "axial_distance_m": conf_eval.axial_distance_m,
            "bands_from_blue": conf_eval.bands_from_blue,
            "confidence_score": conf_eval.confidence_score,
            "confidence_status": conf_eval.status,
            "exclude_from_stats": conf_eval.exclude_from_stats,
            "needs_review": conf_eval.needs_review,
        })

    # If emergency events were triggered, execute conflict resolution & preemption
    if emergency_events:
        signal_manager.resolve_conflicts_and_apply_preemption(emergency_events)

    # 4. Traffic Status AI scoring
    ai_badge = ai_scorer.score(analytics_inputs)

    corridor_count = sum(1 for v in evaluated_vehicles if v["zone_status"] == "IN_CORRIDOR")

    return FrameEvaluationResponse(
        camera_id=req.camera_id,
        timestamp=ts,
        total_detections=len(req.detections),
        corridor_active_count=corridor_count,
        vehicles=evaluated_vehicles,
        ai_recommendation=ai_badge,
        emergency_events=emergency_events,
    )
