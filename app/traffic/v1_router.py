"""
FastAPI v1 Router for RoadGuardian Camera Dynamic Topology,
Multi-Camera Intersection Complementary Signal Logic (MUTEX_SIGNAL), and Linked Emergency Preemption.
"""

from typing import List, Dict, Any, Optional, Literal
import time
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.traffic.database_store import (
    CameraMetadata,
    CameraPairLink,
    traffic_store,
)
from app.traffic.intersection_manager import (
    intersection_manager,
    SignalTransitionResult,
)
from app.traffic.zone_calibration import (
    ZoneCalibrationConfig,
    zone_registry,
)
from app.traffic.corridor_speed import (
    multi_camera_speed_detector,
)
from app.dashboard.stream_broadcaster import broadcaster

v1_router = APIRouter(prefix="/api/v1", tags=["RoadGuardian v1 - Camera Topology & Intersection Signals"])


# --- PYDANTIC SCHEMAS ---
class CameraCreateRequest(BaseModel):
    id: str = Field(..., example="CAM_05_NORTH")
    name: str = Field(..., example="North-South Expressway Way 1")
    location_name: str = Field(..., example="Junction Sector 9 Interchange")
    source: str = Field(..., example="C:\\Users\\krish\\Downloads\\demo.mp4")
    source_type: str = Field("video", example="video")
    gps: List[float] = Field(default_factory=lambda: [25.2914, 79.8713])
    direction_covered: str = Field("Northbound", example="Northbound")
    enabled: bool = True


class CameraResponse(BaseModel):
    id: str
    name: str
    location_name: str
    source: str
    source_type: str
    gps: List[float]
    direction_covered: str
    enabled: bool
    current_signal_state: str
    linked_pairs_count: int
    created_at: float


class CameraPairLinkCreate(BaseModel):
    link_id: str = Field(..., example="LINK_CAM01_CAM02_MUTEX")
    primary_cam_id: str = Field(..., example="CAMERA_01")
    secondary_cam_id: str = Field(..., example="CAMERA_02")
    relationship: Literal["MUTEX_SIGNAL"] = "MUTEX_SIGNAL"
    yellow_clearance_sec: float = Field(3.0, ge=1.0, le=10.0)
    enabled: bool = True


class SignalStateUpdateRequest(BaseModel):
    target_state: Literal["RED", "YELLOW", "GREEN"]
    reason: Optional[str] = "Manual operator or AI transition"
    trigger_source: Optional[str] = "OPERATOR"


class EmergencyOverrideRequest(BaseModel):
    emergency_camera_id: str
    vehicle_type: Literal["AMBULANCE", "POLICE", "FIRE_TRUCK"] = "AMBULANCE"
    track_id: int = 101
    lane_id: int = 1


# --- 1. CAMERA MANAGEMENT & DYNAMIC TOPOLOGY (CRUD) ---
@v1_router.get("/cameras", response_model=List[CameraResponse])
def list_cameras():
    """Returns all active camera streams with topology metadata and current signal states."""
    result = []
    for cam in traffic_store.cameras.values():
        current_state = intersection_manager.get_signal_state(cam.id)
        links = traffic_store.get_links_for_camera(cam.id)
        result.append(CameraResponse(
            id=cam.id,
            name=cam.name,
            location_name=cam.location_name,
            source=cam.source,
            source_type=cam.source_type,
            gps=cam.gps,
            direction_covered=cam.direction_covered,
            enabled=cam.enabled,
            current_signal_state=current_state,
            linked_pairs_count=len(links),
            created_at=cam.created_at,
        ))
    return result


@v1_router.post("/cameras", response_model=CameraResponse)
def add_camera(payload: CameraCreateRequest):
    """
    Dynamically registers a new camera stream into the ITS network:
    - Persists topology to SQLite/in-memory store.
    - Registers live capture and telemetry in StreamBroadcaster.
    - Initializes default calibration zones in zone_registry.
    """
    if payload.id in traffic_store.cameras:
        raise HTTPException(status_code=400, detail=f"Camera with ID '{payload.id}' already exists.")

    cam_meta = CameraMetadata(**payload.model_dump())
    traffic_store.save_camera(cam_meta)

    # Initialize live broadcaster capture
    broadcaster.add_camera(cam_meta.model_dump())

    # Pre-seed calibration zones
    zone_registry.get_calibration(cam_meta.id)

    return CameraResponse(
        id=cam_meta.id,
        name=cam_meta.name,
        location_name=cam_meta.location_name,
        source=cam_meta.source,
        source_type=cam_meta.source_type,
        gps=cam_meta.gps,
        direction_covered=cam_meta.direction_covered,
        enabled=cam_meta.enabled,
        current_signal_state="GREEN",
        linked_pairs_count=0,
        created_at=cam_meta.created_at,
    )


@v1_router.delete("/cameras/{camera_id}")
def delete_camera(camera_id: str):
    """
    Gracefully deletes a camera:
    - Removes active tracking pipeline from MultiCameraCorridorSpeedDetector.
    - Clears cached calibration zones and corridor polygons from CameraZoneRegistry.
    - Decouples and removes any active CameraPairLinks where it was primary or secondary.
    - Releases OpenCV capture and removes from StreamBroadcaster.
    """
    if camera_id not in traffic_store.cameras:
        raise HTTPException(status_code=404, detail=f"Camera with ID '{camera_id}' not found.")

    # 1. Gracefully remove tracking pipeline
    multi_camera_speed_detector.clear_camera(camera_id)

    # 2. Clear cached calibration zones
    zone_registry.clear_calibration(camera_id)

    # 3. Decouple active pair links and remove from persistent store
    decoupled_info = traffic_store.delete_camera(camera_id)

    # 4. Release video capture from broadcaster
    broadcaster.remove_camera(camera_id)

    return {
        "status": "success",
        "message": f"Camera '{camera_id}' deleted. Active tracking pipeline removed, calibration purged, and links decoupled.",
        "details": decoupled_info,
    }


# --- 2. MULTI-CAMERA INTERSECTION & COMPLEMENTARY SIGNAL LOGIC ---
@v1_router.get("/intersection/links", response_model=List[CameraPairLink])
def list_pair_links():
    """Returns all configured complementary intersection camera pairs (MUTEX_SIGNAL)."""
    return list(traffic_store.pair_links.values())


@v1_router.post("/intersection/links", response_model=CameraPairLink)
def configure_camera_pair_link(link: CameraPairLinkCreate):
    """
    Configures a complementary camera pair (e.g. North-South vs. East-West)
    with mutually exclusive signal logic and yellow clearance buffer.
    """
    if link.primary_cam_id == link.secondary_cam_id:
        raise HTTPException(status_code=400, detail="Primary and secondary cameras must be distinct.")

    for cid in [link.primary_cam_id, link.secondary_cam_id]:
        if cid not in traffic_store.cameras:
            raise HTTPException(status_code=404, detail=f"Camera ID '{cid}' does not exist.")

    pair = CameraPairLink(**link.model_dump())
    traffic_store.save_pair_link(pair)

    # Enforce initial safety state: If primary is GREEN, secondary must be RED
    if intersection_manager.get_signal_state(pair.primary_cam_id) == "GREEN":
        intersection_manager._apply_direct_state(pair.secondary_cam_id, "RED")

    return pair


@v1_router.delete("/intersection/links/{link_id}")
def remove_camera_pair_link(link_id: str):
    """Removes a camera pair link, decoupling the MUTEX relationship."""
    ok = traffic_store.delete_pair_link(link_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Camera pair link '{link_id}' not found.")
    return {"status": "success", "message": f"Camera pair link '{link_id}' removed and decoupled."}


@v1_router.get("/intersection/signals/{camera_id}/state")
def get_signal_state(camera_id: str):
    """Returns the current signal state ('RED', 'YELLOW', 'GREEN') for a camera."""
    if camera_id not in traffic_store.cameras:
        raise HTTPException(status_code=404, detail=f"Camera '{camera_id}' does not exist.")
    state = intersection_manager.get_signal_state(camera_id)
    return {"camera_id": camera_id, "current_state": state}


@v1_router.post("/intersection/signals/{camera_id}/state", response_model=SignalTransitionResult)
def update_signal_state(camera_id: str, request: SignalStateUpdateRequest):
    """
    Coordinated signal state transition adhering to MUTEX_SIGNAL logic:
    - If transitioning to GREEN while linked opposing camera is GREEN:
      Opposing camera enters YELLOW clearance (3s) -> RED before target receives GREEN.
    - If transitioning to RED: Opposing linked camera automatically receives GREEN.
    - Conflicting simultaneous GREEN states are strictly prevented.
    """
    if camera_id not in traffic_store.cameras:
        raise HTTPException(status_code=404, detail=f"Camera '{camera_id}' does not exist.")

    return intersection_manager.set_camera_signal_state(
        camera_id=camera_id,
        desired_state=request.target_state,
        reason=request.reason or "Manual / AI controller request",
        trigger_source=request.trigger_source or "OPERATOR",
    )


@v1_router.post("/intersection/emergency-override")
def trigger_emergency_override(request: EmergencyOverrideRequest):
    """
    Linked Emergency Vehicle Priority Override:
    - Identifies emergency vehicle (AMBULANCE, POLICE).
    - Opposing linked camera is immediately shut down to RED (after yellow buffer).
    - Emergency camera lane is granted immediate priority GREEN.
    - Logged to audit log.
    """
    if request.emergency_camera_id not in traffic_store.cameras:
        raise HTTPException(status_code=404, detail=f"Camera '{request.emergency_camera_id}' not found.")

    res = intersection_manager.trigger_linked_emergency_override(
        emergency_cam_id=request.emergency_camera_id,
        vehicle_type=request.vehicle_type,
        track_id=request.track_id,
        lane_id=request.lane_id,
    )
    return res


@v1_router.get("/intersection/audit-logs")
def get_intersection_audit_logs(limit: int = Query(50, le=200)):
    """Retrieves chronological audit logs of all intersection transitions and overrides."""
    return traffic_store.get_audit_logs(limit=limit)


# --- 3. PER-CAMERA ZONE CALIBRATION ENDPOINTS ---
@v1_router.get("/cameras/{camera_id}/calibration", response_model=ZoneCalibrationConfig)
def get_camera_calibration(camera_id: str):
    """Retrieves the calibrated reference line anchors and corridor for a specific camera."""
    homography = zone_registry.get_calibration(camera_id)
    return homography.config


@v1_router.post("/cameras/{camera_id}/calibration")
def update_camera_calibration(camera_id: str, config: ZoneCalibrationConfig):
    """Updates the reference line anchors and homography corridor for a specific camera."""
    zone_registry.register_calibration(camera_id, config)
    return {"status": "success", "message": f"Calibration for camera '{camera_id}' updated."}
