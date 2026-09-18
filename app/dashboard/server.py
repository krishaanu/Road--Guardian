import os
import sys
import time
import json
import uuid
import asyncio
import sqlite3
import cv2
import numpy as np
import logging
from typing import Dict, List, Any, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from app.database.database import DatabaseManager
from app.dashboard.stream_broadcaster import broadcaster
from app.traffic import traffic_router, v1_router
from app.traffic.generic_signal import GenericJunctionController
from app.behavior.offense_pipeline import ConfidenceGatedOffensePipeline
from app.core.source_resolver import resolve_camera_source, open_capture_handle, release_capture_handle
from app.analytics.signal_engine import calculate_adaptive_signal

logger = logging.getLogger("DashboardServer")
logging.basicConfig(level=logging.INFO)

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/roadguardian.db"))
UPLOADS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/uploads"))
CROPS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/crops"))
os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(CROPS_DIR, exist_ok=True)

# Initialize controllers
junction_controller = GenericJunctionController()
offense_pipeline = ConfidenceGatedOffensePipeline()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Initializing RoadGuardian ITS Dashboard Server...")
    broadcaster.loop = asyncio.get_running_loop()
    broadcaster.start()
    yield
    # Shutdown
    logger.info("Shutting down RoadGuardian ITS Dashboard Server...")
    broadcaster.stop()


app = FastAPI(
    title="RoadGuardian ITS Control-Room API",
    description="Intelligent Transportation System backend API for live video feeds, emergency alerts, vehicle registry, and AI traffic signal control.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static file directories for video uploads and offense crops
app.mount("/data/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")
app.mount("/data/crops", StaticFiles(directory=CROPS_DIR), name="crops")

# Mount Traffic Analytics, Zone Calibration, and Signal Management router
app.include_router(traffic_router)
# Mount v1 Dynamic Camera Topology & Intersection Signals router
app.include_router(v1_router)


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# --- PYDANTIC SCHEMAS ---
class CameraCreate(BaseModel):
    id: str = Field(..., example="CAM_05_WEST")
    name: str = Field(..., example="Westbound Expressway KM 18")
    location_name: str = Field(..., example="NH-44 Western Bypass")
    source: str = Field(..., example="rtsp://192.168.1.150:554/stream")
    source_type: str = Field("rtsp", example="rtsp")
    gps: List[float] = Field(default_factory=lambda: [25.2914, 79.8713])
    direction_covered: str = Field("Westbound", example="Westbound")
    enabled: bool = True


# --- CAMERA ENDPOINTS (INGESTION ENGINE FOR USB & IP FEEDS) ---
class CameraAddPayload(BaseModel):
    id: Optional[str] = None
    camera_id: Optional[str] = None
    name: Optional[str] = None
    location_name: Optional[str] = None
    source: str = Field(..., example="0 or rtsp://192.168.1.100:554/stream1")
    source_type: Optional[str] = None
    gps: Optional[List[float]] = Field(default_factory=lambda: [25.2914, 79.8713])
    direction_covered: Optional[str] = Field("Northbound", example="Northbound")
    enabled: bool = True


@app.get("/api/cameras")
def list_cameras():
    """Returns all registered cameras with live status and traffic telemetry."""
    result = []
    with broadcaster.lock:
        for cid, cam in broadcaster.cameras.items():
            sig = broadcaster.signal_states.get(cid, {})
            is_open = cid in broadcaster.caps and broadcaster.caps[cid] is not None and broadcaster.caps[cid].isOpened()
            is_enabled = cam.get("enabled", True)
            status = "STREAMING" if (is_enabled and is_open) else "OFFLINE"
            result.append({
                "id": cid,
                "name": cam.get("name", cid),
                "location_name": cam.get("location_name", cid),
                "source": str(cam.get("source", "")),
                "source_type": cam.get("source_type", "video"),
                "gps": cam.get("gps", [25.2914, 79.8713]),
                "direction_covered": cam.get("direction_covered", "Northbound"),
                "enabled": is_enabled,
                "status": status,
                "density": sig.get("density", 0.0) if is_open else 0.0,
                "los": sig.get("los", "LOS A") if is_open else "OFFLINE",
                "vehicle_count": sig.get("vehicle_count", 0) if is_open else 0,
                "signal": {
                    "current_light": sig.get("current_light", "GREEN") if is_open else "OFFLINE",
                    "countdown": sig.get("countdown", 0) if is_open else 0,
                    "cycle_duration": sig.get("cycle_duration", 30),
                    "recommended_green": sig.get("recommended_green", 30),
                    "mode": sig.get("mode", "AI_ADAPTIVE"),
                },
            })
    return {"status": "success", "count": len(result), "data": result}


@app.post("/api/camera/add")
@app.post("/api/cameras/add")
def add_camera_endpoint(payload: CameraAddPayload):
    """Ingestion Engine: Dynamic camera stream registration supporting both
    hardware (USB webcam indices '0', '1') and network IP RTSP/HTTP feeds.
    """
    raw_id = (payload.camera_id or payload.id or "").strip()
    if not raw_id:
        cid = f"CAM_{int(time.time() * 10) % 10000:04d}"
    else:
        import re
        cid = re.sub(r'[^a-zA-Z0-9_-]', '_', raw_id).upper()

    res = resolve_camera_source(payload.source)
    source_type = payload.source_type or res["source_type"]

    cam_dict = {
        "id": cid,
        "name": payload.name or f"Camera {cid}",
        "location_name": payload.location_name or f"Sector Corridor ({cid})",
        "source": res["resolved_source"],
        "source_type": source_type,
        "gps": payload.gps or [25.2914, 79.8713],
        "direction_covered": payload.direction_covered or "Northbound",
        "enabled": payload.enabled,
    }

    broadcaster.add_camera(cam_dict)
    # Attempt immediate capture initialization so diagnostics are ready
    cap = broadcaster._get_or_open_cap(cid, cam_dict)
    diag = broadcaster.diagnostics.get(cid, {})

    return {
        "status": "success",
        "message": f"Camera '{cid}' registered successfully.",
        "data": cam_dict,
        "diagnostics": diag,
        "is_open": cap is not None and cap.isOpened()
    }


@app.delete("/api/camera/remove/{camera_id}")
@app.delete("/api/cameras/{camera_id}")
@app.post("/api/cameras/{camera_id}/delete")
def delete_camera(camera_id: str):
    """Releases video handle, drops camera instance, and purges from config/cameras.json."""
    if camera_id in broadcaster.cameras:
        broadcaster.remove_camera(camera_id)

    # Release any active capture handle directly
    with broadcaster.lock:
        if camera_id in broadcaster.caps:
            release_capture_handle(broadcaster.caps[camera_id])
            del broadcaster.caps[camera_id]
        broadcaster.latest_frames.pop(camera_id, None)
        broadcaster.camera_tracks.pop(camera_id, None)
        broadcaster.diagnostics.pop(camera_id, None)
        broadcaster.signal_states.pop(camera_id, None)

    # Update config/cameras.json if exists
    config_path = "config/cameras.json"
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                data = json.load(f)
            data["cameras"] = [c for c in data.get("cameras", []) if c.get("id") != camera_id]
            with open(config_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not update config/cameras.json: {e}")

    return {"status": "success", "message": f"Camera {camera_id} removed successfully"}


@app.post("/api/camera/retry/{camera_id}")
@app.post("/api/cameras/{camera_id}/retry")
def retry_camera_stream(camera_id: str):
    """Re-attempts VideoCapture initialization and returns detailed diagnostics."""
    res = broadcaster.retry_camera(camera_id)
    return res


@app.get("/api/cameras/{camera_id}/status")
def get_camera_status(camera_id: str):
    """Returns real-time status and failure diagnostics for a camera feed."""
    diag = broadcaster.diagnostics.get(camera_id)
    is_cap_open = camera_id in broadcaster.caps and broadcaster.caps[camera_id] is not None and broadcaster.caps[camera_id].isOpened()
    cam = broadcaster.cameras.get(camera_id)
    return {
        "camera_id": camera_id,
        "is_open": is_cap_open,
        "diagnostics": diag or {"state": "STREAMING" if is_cap_open else "UNKNOWN", "reason": None if is_cap_open else "No diagnostic data yet"},
        "source": cam.get("source") if cam else None,
    }


@app.get("/api/validate_source")
def validate_source_path(source: str = Query(..., description="File path, webcam index, or stream URL")):
    """Validates if a video file path, webcam index, or RTSP stream is accessible before adding."""
    res = resolve_camera_source(source)
    if not res["is_valid"]:
        return {
            "valid": False,
            "type": res["source_type"],
            "message": res["error"] or res["message"],
        }

    # Test opening the source handle
    test_cap, err = open_capture_handle(res["resolved_source"], res["source_type"])
    if not test_cap or not test_cap.isOpened():
        release_capture_handle(test_cap)
        return {
            "valid": False,
            "type": res["source_type"],
            "message": err or f"Cannot open {res['display_name']}",
        }

    w = int(test_cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(test_cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    fps = float(test_cap.get(cv2.CAP_PROP_FPS) or 0)
    release_capture_handle(test_cap)

    if res["source_type"] == "webcam":
        msg = f"Webcam #{res['resolved_source']} ready ({w}x{h})"
    elif res["source_type"] == "file":
        msg = f"Local file: {os.path.basename(str(res['resolved_source']))} ({w}x{h} @ {fps:.0f} FPS)"
    else:
        msg = f"Stream URL verified ({w}x{h})"

    return {
        "valid": True,
        "type": res["source_type"],
        "message": msg,
        "resolution": f"{w}x{h}",
        "fps": fps,
    }


# --- VEHICLE REGISTRY & DEDUPLICATED INVENTORY PER CAMERA ---
@app.get("/api/vehicles/{camera_id}")
def get_camera_vehicles(
    camera_id: str,
    search: Optional[str] = Query(None, description="Search by plate text or GVID"),
    vehicle_type: Optional[str] = Query(None, description="Filter by vehicle type"),
    color: Optional[str] = Query(None, description="Filter by vehicle color"),
    limit: int = Query(50, le=100),
):
    """Retrieves deduplicated vehicle inventory for a specific camera from vehicle_inventory table."""
    # Fetch directly from vehicle_inventory using UPSERT storage
    rows = broadcaster.db.get_vehicle_inventory(camera_id=camera_id, search=search, limit=limit)
    vehicles = []

    for r in rows:
        spd = round(float(r.get("speed") or 0.0), 1)
        vehicles.append({
            "gvid": r["gvid"],
            "plate_text": r["license_plate"] or "UNREAD",
            "vehicle_type": (r["category"] or "Car").title(),
            "vehicle_subtype": (r["category"] or "Car").title(),
            "color": (r["color"] or "Silver").title(),
            "estimated_speed_kmh": spd,
            "speed_status": "OVERSPEED" if spd > 80.0 else "NORMAL",
            "detected_view": r["view_angle"] or "Front",
            "confidence": 0.94,
            "last_seen": str(r["last_seen"]),
        })

    # If camera has not logged enough frames yet, query all inventory or seed initial records
    if len(vehicles) == 0:
        all_rows = broadcaster.db.get_vehicle_inventory(limit=10)
        for r in all_rows:
            spd = round(float(r.get("speed") or 0.0), 1)
            vehicles.append({
                "gvid": r["gvid"],
                "plate_text": r["license_plate"] or "UNREAD",
                "vehicle_type": (r["category"] or "Car").title(),
                "vehicle_subtype": (r["category"] or "Car").title(),
                "color": (r["color"] or "Silver").title(),
                "estimated_speed_kmh": spd,
                "speed_status": "OVERSPEED" if spd > 80.0 else "NORMAL",
                "detected_view": r["view_angle"] or "Front",
                "confidence": 0.94,
                "last_seen": str(r["last_seen"]),
            })

    # Apply search & filters
    filtered = vehicles
    if search:
        s = search.lower().strip()
        filtered = [v for v in filtered if s in v["plate_text"].lower() or s in v["gvid"].lower()]
    if vehicle_type:
        vt = vehicle_type.lower().strip()
        filtered = [v for v in filtered if vt in v["vehicle_type"].lower() or vt in v["vehicle_subtype"].lower()]
    if color:
        c = color.lower().strip()
        filtered = [v for v in filtered if c in v["color"].lower()]

    return {
        "status": "success",
        "camera_id": camera_id,
        "total_count": len(filtered),
        "data": filtered,
    }


@app.get("/api/inventory/{camera_id}")
def get_camera_inventory_alias(
    camera_id: str,
    search: Optional[str] = Query(None, description="Search by plate text or GVID"),
    vehicle_type: Optional[str] = Query(None, description="Filter by vehicle type"),
    color: Optional[str] = Query(None, description="Filter by vehicle color"),
    limit: int = Query(50, le=100),
):
    """Direct alias to /api/vehicles/{camera_id} for frontend vehicle modal."""
    return get_camera_vehicles(camera_id, search, vehicle_type, color, limit)


# --- VIDEO UPLOAD & DRAG-AND-DROP INGESTION (SECTION 1) ---
@app.post("/api/upload_video")
async def upload_video(file: UploadFile = File(...), camera_id: Optional[str] = Query(None)):
    """Uploads video file (.mp4, .avi, .mkv, .mov) and registers it as a playable camera stream."""
    ext = os.path.splitext(file.filename)[1].lower()
    allowed_exts = [".mp4", ".avi", ".mkv", ".mov"]
    if ext not in allowed_exts:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid video format '{ext}'. Supported formats: {', '.join(allowed_exts)}",
        )

    cid = camera_id.strip() if camera_id else None
    if cid:
        target_dir = os.path.join(UPLOADS_DIR, cid)
    else:
        target_dir = UPLOADS_DIR
    os.makedirs(target_dir, exist_ok=True)

    # Sanitize and create destination path
    safe_filename = f"{uuid.uuid4().hex[:8]}_{os.path.basename(file.filename).replace(' ', '_')}"
    target_path = os.path.join(target_dir, safe_filename).replace("\\", "/")

    with open(target_path, "wb") as buffer:
        content = await file.read()
        buffer.write(content)

    assigned_cam_id = cid or f"CAM_UP_{int(time.time()) % 10000:04d}"
    
    # Release any existing capture handles on this camera or its simulation alias
    alias_ids = [assigned_cam_id]
    if assigned_cam_id.startswith("SIM_JUNCTION_01_"):
        alias_ids.append(assigned_cam_id.replace("SIM_JUNCTION_01_", ""))
    else:
        alias_ids.append(f"SIM_JUNCTION_01_{assigned_cam_id}")

    with broadcaster.lock:
        for aid in alias_ids:
            if aid in broadcaster.caps:
                release_capture_handle(broadcaster.caps[aid])
                del broadcaster.caps[aid]
            broadcaster.latest_frames.pop(aid, None)
            broadcaster.camera_tracks.pop(aid, None)
            broadcaster.frame_counters.pop(aid, None)
            broadcaster.diagnostics.pop(aid, None)

    # Validate video with OpenCV
    test_cap, err = open_capture_handle(target_path, "file")
    width, height, fps = 640, 360, 25.0
    if test_cap and test_cap.isOpened():
        width = int(test_cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 640)
        height = int(test_cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 360)
        fps = float(test_cap.get(cv2.CAP_PROP_FPS) or 25.0)
        release_capture_handle(test_cap)
    else:
        logger.warning(f"Uploaded video validation warning for {assigned_cam_id}: {err}")

    cam_data = {
        "id": assigned_cam_id,
        "name": f"Uploaded Video ({file.filename})",
        "location_name": f"Upload: {file.filename}",
        "source": target_path,
        "source_type": "file",
        "gps": [25.2914, 79.8713],
        "direction_covered": "Bidirectional",
        "enabled": True,
    }
    
    # Register under both assigned_cam_id and alias so simulator & grid access same feed
    for aid in alias_ids:
        cam_entry = dict(cam_data)
        cam_entry["id"] = aid
        broadcaster.add_camera(cam_entry)

    logger.info(f"[UPLOAD_SUCCESS] camera_id='{assigned_cam_id}' file='{safe_filename}' ({width}x{height} @ {fps:.0f}fps)")

    return {
        "status": "success",
        "camera_id": assigned_cam_id,
        "file_path": target_path,
        "filename": safe_filename,
        "resolution": f"{width}x{height}",
        "fps": fps,
        "camera": cam_data,
    }


# --- GENERIC ADAPTIVE TRAFFIC SIGNALS (SECTION 2) ---
class JunctionEvalRequest(BaseModel):
    count_a: int = 10
    count_b: int = 10
    emergency_cam_id: Optional[str] = None
    emergency_type: str = "AMBULANCE"


@app.get("/api/junctions")
def list_junctions():
    """Lists all configured 2-phase junctions with active timing and density states."""
    return {"status": "success", "data": junction_controller.get_all_junctions()}


@app.post("/api/junctions/{junction_id}/evaluate")
def evaluate_junction_timing(junction_id: str, payload: JunctionEvalRequest):
    """Calculates proportional green splits and processes emergency clearance preemption."""
    try:
        result = junction_controller.evaluate_junction(
            junction_id=junction_id,
            count_a=payload.count_a,
            count_b=payload.count_b,
            emergency_cam_id=payload.emergency_cam_id,
            emergency_type=payload.emergency_type,
        )
        return {"status": "success", "data": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/junctions/adaptive-signal")
@app.post("/api/junctions/{junction_id}/adaptive-signal")
def post_adaptive_signal(payload: Dict[str, Any], junction_id: Optional[str] = None):
    """Calculates adaptive signal using signal_engine.py logic:
    7s minimum hold timer, 2s yellow transition, density-based green allocation,
    and immediate emergency preemption override.
    """
    junc_id = junction_id or payload.get("junction_id", "Junction_1")
    junc_cfg = payload.get("junction_config", {"junction_id": junc_id})
    densities = payload.get("lane_densities", {})
    emergency_events = payload.get("emergency_events", {})
    result = calculate_adaptive_signal(junc_cfg, densities, emergency_events=emergency_events)
    return result


@app.get("/api/junctions/preemption-logs")
def get_junction_preemption_logs(junction_id: Optional[str] = None, limit: int = 50):
    """Returns emergency vehicle preemption audit records."""
    logs = broadcaster.db.get_preemption_logs(junction_id=junction_id, limit=limit)
    return {"status": "success", "data": logs}


@app.get("/api/simulator/densities")
def get_simulator_densities(junction_id: str = "JUNCTION_01"):
    """Returns real-time YOLO vehicle detection counts and signal states per lane for the junction simulator."""
    lane_densities = {}
    with broadcaster.lock:
        for cid, state in broadcaster.signal_states.items():
            if cid.startswith("SIM_"):
                parts = cid.split("_")
                if len(parts) >= 4:
                    j_id = f"{parts[1]}_{parts[2]}"
                    lane_id = "_".join(parts[3:])
                    if j_id.upper() == junction_id.upper() or junction_id.upper() == "ALL":
                        cnt = state.get("vehicle_count", 0)
                        lane_densities[lane_id] = cnt
                        lane_densities[lane_id.lower()] = cnt
                        lane_densities[cid] = cnt
                else:
                    cnt = state.get("vehicle_count", 0)
                    lane_densities[cid] = cnt
            else:
                lane_densities[cid] = state.get("vehicle_count", 0)

    sig_res = calculate_adaptive_signal({"junction_id": junction_id}, lane_densities)
    return {
        "status": "success",
        "junction_id": junction_id,
        "lane_densities": lane_densities,
        "signals": sig_res.get("signals", {}),
        "active_phase": sig_res.get("active_phase"),
        "emergency_preemption": sig_res.get("emergency_preemption", False),
    }


# --- CONFIDENCE-GATED OFFENSE DETECTION PIPELINE (SECTION 3) ---
class OffenseEvalRequest(BaseModel):
    camera_id: str
    motorcycle_conf: float = 0.85
    rider_conf: float = 0.80
    helmet_conf: float = 0.40
    ocr_conf: float = 0.90
    license_plate: str = "DL03XY1234"
    offense_type: str = "NO_HELMET"


class ReviewActionPayload(BaseModel):
    status: str = Field(..., example="APPROVED or DISMISSED")
    reviewer_notes: Optional[str] = None


@app.get("/api/offenses/needs-review")
def get_needs_review_queue(
    camera_id: Optional[str] = None,
    status: str = "PENDING_REVIEW",
    limit: int = 50,
):
    """Retrieves offenses requiring human verification with crop imagery and gate failures."""
    queue = broadcaster.db.get_needs_review(camera_id=camera_id, status=status, limit=limit)
    return {"status": "success", "total": len(queue), "data": queue}


@app.post("/api/offenses/review/{review_id}")
def process_review_action(review_id: int, payload: ReviewActionPayload):
    """Confirms (APPROVED) or rejects (DISMISSED) an item in the human review queue."""
    valid_statuses = ["APPROVED", "DISMISSED"]
    if payload.status.upper() not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{payload.status}'. Must be one of: {valid_statuses}",
        )
    ok = broadcaster.db.update_review_status(
        review_id=review_id,
        status=payload.status.upper(),
        reviewer_notes=payload.reviewer_notes,
    )
    if not ok:
        raise HTTPException(status_code=404, detail=f"Review record {review_id} not found.")
    return {"status": "success", "message": f"Review item {review_id} set to {payload.status.upper()}."}


@app.get("/api/offenses/infractions")
def list_confirmed_infractions(camera_id: Optional[str] = None, limit: int = 50):
    """Lists high-confidence confirmed infractions."""
    records = broadcaster.db.get_infractions(camera_id=camera_id, limit=limit)
    return {"status": "success", "total": len(records), "data": records}


@app.post("/api/offenses/evaluate")
def evaluate_offense_gates(payload: OffenseEvalRequest):
    """Evaluates candidate offense against 4 confidence gates:
    Gate 1: Motorcycle (>=0.60)
    Gate 2: Rider (>=0.60)
    Gate 3: Helmet (>=0.70)
    Gate 4: OCR (>=0.85)
    Routes to infractions (if all pass) or needs_review_queue (if any fail).
    """
    res = offense_pipeline.evaluate_candidate(
        camera_id=payload.camera_id,
        frame=None,
        bbox=None,
        motorcycle_conf=payload.motorcycle_conf,
        rider_conf=payload.rider_conf,
        helmet_conf=payload.helmet_conf,
        ocr_conf=payload.ocr_conf,
        license_plate=payload.license_plate,
        offense_type=payload.offense_type,
    )
    return {"status": "success", "data": res.model_dump()}


# --- EMERGENCY ALERTS & INCIDENTS ---
@app.get("/api/alerts")
def get_alerts(limit: int = 20):
    """Returns active emergency alerts and historic incident records."""
    active = list(broadcaster.active_alerts)
    history = []
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,))
        for row in cursor.fetchall():
            history.append({
                "alert_id": f"HIST_{row['id']}",
                "event_type": row["alert_type"],
                "camera_id": row["camera_id"],
                "severity": row["severity"],
                "timestamp": row["timestamp"],
            })
    except Exception as e:
        logger.warning(f"Error reading alerts from db: {e}")
    finally:
        conn.close()

    return {
        "status": "success",
        "active_emergency_alerts": active,
        "historic_alerts": history,
    }


@app.post("/api/alerts/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: str):
    """Acknowledges an active incident alert."""
    ok = broadcaster.acknowledge_alert(alert_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Alert ID not found or already closed.")
    return {"status": "success", "message": f"Alert {alert_id} acknowledged by operator."}


@app.post("/api/alerts/{alert_id}/dispatch-ambulance")
def dispatch_ambulance(alert_id: str):
    """Triggers emergency ambulance dispatch for confirmed crash incident."""
    ok = broadcaster.dispatch_ambulance(alert_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Alert ID not found.")
    return {
        "status": "success",
        "alert_id": alert_id,
        "message": "Ambulance dispatched. Emergency medical services notified.",
        "dispatch_timestamp": time.time(),
    }


@app.post("/api/alerts/simulate")
def simulate_confirmed_incident(camera_id: str = "CAMERA_01"):
    """Trigger endpoint to simulate a CONFIRMED_INCIDENT from IncidentFusionEngine."""
    with broadcaster.lock:
        cam_info = broadcaster.cameras.get(camera_id, {
            "id": camera_id,
            "location_name": "NH-44 KM 212 Northbound",
            "gps": [25.2914, 79.8713],
        })

    payload = {
        "event_type": "CONFIRMED_INCIDENT",
        "status": "PENDING_OPERATOR_CONFIRMATION",
        "track_id": 104,
        "fusion_score": 4,
        "detected_signals": [
            "SUDDEN_DECELERATION",
            "TRAJECTORY_ZIGZAG",
            "BBOX_DEFORMATION",
            "STATIONARY_IN_LIVE_LANE",
        ],
        "direction_zone": "DIRECTION_FORWARD",
        "timestamp": time.time(),
        "camera_metadata": {
            "camera_id": camera_id,
            "location_name": cam_info.get("location_name", "Expressway Corridor"),
            "gps": cam_info.get("gps", [25.2914, 79.8713]),
            "direction_covered": cam_info.get("direction_covered", "Northbound"),
        },
    }
    broadcaster.broadcast_incident(payload)
    return {"status": "success", "message": f"Confirmed incident simulated on {camera_id}.", "payload": payload}


# --- HIGH CONGESTION ZONE & METRICS SUMMARY ---
@app.get("/api/metrics/summary")
def get_metrics_summary():
    """Returns top-level ITS metrics including the highest congestion zone."""
    cameras = list(broadcaster.cameras.keys())
    max_density = -1.0
    highest_zone = cameras[0] if cameras else "CAMERA_01"

    with broadcaster.lock:
        for cid, state in broadcaster.signal_states.items():
            d = state.get("density", 0.0)
            if d > max_density:
                max_density = d
                highest_zone = cid

        cam_info = broadcaster.cameras.get(highest_zone, {})
        loc_name = cam_info.get("location_name", highest_zone)

    # Count total vehicles from db
    conn = get_db_connection()
    cursor = conn.cursor()
    total_vehicles = 0
    total_alerts = 0
    try:
        cursor.execute("SELECT COUNT(DISTINCT global_vehicle_id) FROM observations")
        row = cursor.fetchone()
        total_vehicles = row[0] if row else 0

        cursor.execute("SELECT COUNT(*) FROM alerts")
        row_al = cursor.fetchone()
        total_alerts = row_al[0] if row_al else 0
    except Exception:
        pass
    finally:
        conn.close()

    active_cams = [
        cid for cid, cam in broadcaster.cameras.items()
        if cam.get("enabled", True) and cid in broadcaster.caps and broadcaster.caps[cid] is not None and broadcaster.caps[cid].isOpened()
    ]

    return {
        "status": "success",
        "high_congestion_zone": {
            "camera_id": highest_zone,
            "location_name": loc_name,
            "density": round(max_density, 1) if max_density >= 0 else 0.0,
        },
        "active_cameras_count": len(cameras),
        "online_cameras_count": len(active_cams),
        "total_vehicles_monitored": total_vehicles,
        "total_alerts_logged": total_alerts,
        "active_emergency_alerts": len([a for a in broadcaster.active_alerts if not a.get("acknowledged", False)]),
    }


@app.get("/api/stats")
def get_stats_alias():
    """Backwards-compatible alias for /api/metrics/summary."""
    return get_metrics_summary()


@app.get("/api/observations/latest")
def get_latest_observations(limit: int = Query(20, ge=1, le=100)):
    """Returns latest observed vehicle inventory."""
    rows = broadcaster.db.get_vehicle_inventory(limit=limit)
    return {"status": "success", "data": rows}


# --- AI TRAFFIC SIGNAL CONTROLLER API ---
@app.get("/api/signals/{camera_id}")
def get_traffic_signal_ai(camera_id: str):
    """Returns real-time HCM density, Level of Service (LOS), and AI green light timing."""
    with broadcaster.lock:
        sig = broadcaster.signal_states.get(camera_id, {
            "camera_id": camera_id,
            "current_light": "OFFLINE",
            "countdown": 0,
            "cycle_duration": 30,
            "recommended_green": 30,
            "mode": "AI_ADAPTIVE",
            "los": "OFFLINE",
            "density": 0.0,
        })
        is_open = camera_id in broadcaster.caps and broadcaster.caps[camera_id] is not None and broadcaster.caps[camera_id].isOpened()
        density = sig.get("density", 0.0) if is_open else 0.0

    ai_timing = broadcaster.calculate_traffic_signal_ai(density)

    return {
        "status": "success",
        "camera_id": camera_id,
        "current_light": sig.get("current_light", "GREEN") if is_open else "OFFLINE",
        "countdown_seconds": sig.get("countdown", 0),
        "cycle_duration_seconds": sig.get("cycle_duration", 30),
        "recommended_green_seconds": ai_timing["recommended_green"],
        "density_veh_km_lane": ai_timing["density"],
        "level_of_service": ai_timing["los"] if is_open else "OFFLINE",
        "policy_note": ai_timing["policy_note"],
        "mode": sig.get("mode", "AI_ADAPTIVE"),
    }


def generate_error_image_bytes(camera_id: str, error_text: str) -> bytes:
    """Generates a terminal 640x360 error frame JPEG byte string."""
    blank = np.zeros((360, 640, 3), dtype=np.uint8)
    cv2.rectangle(blank, (10, 10), (630, 350), (0, 0, 180), 2)
    cv2.putText(blank, f"STREAM ERROR [{camera_id}]", (30, 160), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 100, 255), 2)
    cv2.putText(blank, str(error_text)[:45], (30, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    _, encoded = cv2.imencode(".jpg", blank, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
    return encoded.tobytes()


# --- MJPEG LIVE VIDEO STREAMING ---
async def generate_mjpeg_stream(camera_id: str):
    """Generator streaming multipart MJPEG frames to browser asynchronously with bounded connection timeout."""
    start_time = time.time()
    MAX_CONNECT_TIMEOUT_SEC = 5.0
    first_frame_sent = False

    try:
        while True:
            frame_bytes = broadcaster.get_latest_frame_jpeg(camera_id)
            if frame_bytes is not None:
                if not first_frame_sent:
                    first_frame_sent = True
                    logger.info(f"[STREAM_FIRST_FRAME] camera_id='{camera_id}' first frame yielded at {time.strftime('%Y-%m-%d %H:%M:%S')}")
                header = (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(frame_bytes)).encode() + b"\r\n\r\n"
                )
                yield header + frame_bytes + b"\r\n"
                await asyncio.sleep(0.04)  # ~25 FPS stream
                continue

            # Check if camera has explicit error or timed out
            diag = broadcaster.diagnostics.get(camera_id, {})
            if diag.get("state") == "ERROR":
                err_msg = diag.get("reason", "Stream connection failed.")
                logger.warning(f"[STREAM_ERROR_FRAME] camera_id='{camera_id}' yielding error frame: {err_msg}")
                err_bytes = generate_error_image_bytes(camera_id, f"ERROR: {err_msg}")
                header = (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(err_bytes)).encode() + b"\r\n\r\n"
                )
                yield header + err_bytes + b"\r\n"
                break

            if time.time() - start_time > MAX_CONNECT_TIMEOUT_SEC:
                logger.warning(f"[STREAM_TIMEOUT] camera_id='{camera_id}' timed out after {MAX_CONNECT_TIMEOUT_SEC}s")
                err_bytes = generate_error_image_bytes(camera_id, "CONNECT TIMEOUT: Source Unreachable")
                header = (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(err_bytes)).encode() + b"\r\n\r\n"
                )
                yield header + err_bytes + b"\r\n"
                break

            await asyncio.sleep(0.1)
    except (asyncio.CancelledError, GeneratorExit):
        logger.info(f"[STREAM_CLIENT_DISCONNECT] camera_id='{camera_id}' client disconnected.")
    except Exception as e:
        logger.error(f"[STREAM_EXCEPTION] camera_id='{camera_id}': {e}", exc_info=True)
    finally:
        logger.info(f"[STREAM_END] camera_id='{camera_id}' stream generator completed.")


@app.get("/api/stream/{camera_id}")
async def stream_video(camera_id: str):
    """Streams live camera video as an MJPEG multipart response for HTML <img> tags."""
    ts_now = time.strftime('%Y-%m-%d %H:%M:%S')
    logger.info(f"[STREAM_REQ] camera_id='{camera_id}' received at {ts_now}")

    with broadcaster.lock:
        cam_info = broadcaster.cameras.get(camera_id)

    if not cam_info:
        logger.warning(f"[STREAM_NOT_FOUND] camera_id='{camera_id}' is not in registry.")
        raise HTTPException(
            status_code=404,
            detail=f"Camera ID '{camera_id}' is not registered."
        )

    # Check / attempt opening the stream
    cap = broadcaster._get_or_open_cap(camera_id, cam_info)
    if not cap or not cap.isOpened():
        diag = broadcaster.diagnostics.get(camera_id, {})
        reason = diag.get("reason", "Camera stream cannot be decoded or device is unavailable.")
        logger.warning(f"[STREAM_UNAVAILABLE] camera_id='{camera_id}', reason={reason}")
        raise HTTPException(
            status_code=503,
            detail=f"Stream unavailable for {camera_id}: {reason}"
        )

    logger.info(f"[STREAM_OPENED] camera_id='{camera_id}' streaming starting...")
    return StreamingResponse(
        generate_mjpeg_stream(camera_id),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/api/video_feed/{camera_id}")
async def video_feed(camera_id: str):
    """Backwards-compatible alias for live camera video streaming."""
    return await stream_video(camera_id)


# --- WEBSOCKET FOR REAL-TIME TELEMETRY & ALERTS ---
@app.websocket("/ws/traffic-stream")
async def websocket_traffic_stream(websocket: WebSocket):
    """High-speed real-time WebSocket connection for ITS dashboard operators."""
    await websocket.accept()
    broadcaster.register_websocket(websocket)
    logger.info("New WebSocket client connected to /ws/traffic-stream")

    try:
        # Send initial snapshot immediately
        with broadcaster.lock:
            active_alerts = list(broadcaster.active_alerts)
            cams_list = list(broadcaster.cameras.keys())
            highest_cam = cams_list[0] if cams_list else "CAMERA_01"

        await websocket.send_json({
            "type": "INITIAL_STATE",
            "timestamp": time.time(),
            "high_congestion_zone": highest_cam,
            "active_alerts": active_alerts,
        })

        while True:
            # Handle incoming client commands / heartbeats
            data = await websocket.receive_text()
            # Client can send ping or custom actions
            try:
                import json
                msg = json.loads(data)
                if msg.get("action") == "PING":
                    await websocket.send_json({"type": "PONG", "timestamp": time.time()})
            except Exception:
                pass

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected.")
    finally:
        broadcaster.unregister_websocket(websocket)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.dashboard.server:app", host="0.0.0.0", port=8000, reload=False)
