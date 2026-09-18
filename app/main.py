import sys
import os
from pathlib import Path

# Dynamically resolve project root directory and add to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Change current working directory to project root for consistent relative paths
os.chdir(PROJECT_ROOT)
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "threads;1"
os.environ["OPENCV_VIDEOIO_PRIORITY_MSMF"] = "0"

import time
import json
import logging
import uuid
import shutil
import threading
from contextlib import asynccontextmanager
from typing import Dict, List, Optional, Tuple, Any

import cv2
cv2.setNumThreads(1)
import numpy as np
import torch
from ultralytics import YOLO
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Internal module imports (now resolved cleanly from PROJECT_ROOT)
from app.analytics.collision_detector import evaluate_impact_event
from app.analytics.incident_fusion import IncidentFusionEngine
from app.analytics.long_term_aggregator import LongTermTrafficAggregator
from app.analytics.road_classifier import RoadLayoutClassifier
from app.analytics.signal_engine import calculate_adaptive_signal
from app.analytics.signal_allocator import SignalAllocator, format_speed
from app.analytics.speed_estimator import HomographySpeedEstimator, evaluate_multi_signal_crash
from app.analytics.traffic_analyzer import LocalTrafficAnalyzer
from app.audio.speaker import AudioSpeaker
import torch
from app.behavior.advanced_behavior import AdvancedBehaviorEngine
from app.behavior.kalman_tracker import VehicleStateFilter
from app.models.vehicle_view import VehicleViewClassifier
from app.core.live_state import live_state_manager
from app.core.camera_manager import CameraManager
from app.core.source_resolver import resolve_camera_source, open_capture_handle, release_capture_handle
from app.core.incident_manager import create_and_log_incident
from app.core.tracker import CrossCameraTracker, TrackedVehicle
from app.database.database import DatabaseManager
from app.utils.draw import draw_trajectories_and_events

# Module-level Cross-Camera Tracker Singleton (Single Source of Truth)
global_cross_camera_tracker = CrossCameraTracker()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("RoadGuardianMain")

# Global Signal Allocator State Machine
allocator = SignalAllocator(green_hold_seconds=20.0, yellow_hold_seconds=2.0, min_safety_hold_seconds=5.0)
SPECIAL_VEHICLE_CLASSES = [5, 7]  # bus, truck

def signal_tick_worker():
    while True:
        try:
            allocator.tick()
        except Exception as e:
            logger.error(f"Signal tick error: {e}")
        time.sleep(1.0)

threading.Thread(target=signal_tick_worker, daemon=True).start()

# Global Active Streams & Buffers
active_captures: Dict[str, cv2.VideoCapture] = {}
latest_frames: Dict[str, bytes] = {}
active_camera_threads: Dict[str, bool] = {}
active_camera_counts: Dict[str, int] = {}
lane_vehicle_counts: Dict[str, int] = {}
lane_special_vehicles: Dict[str, bool] = {}
stream_errors: Dict[str, str] = {}
active_alerts: List[Dict[str, Any]] = []

# Shared YOLO Model Singleton
_yolo_model = None
_yolo_lock = threading.Lock()

def get_shared_yolo():
    global _yolo_model
    if _yolo_model is None:
        with _yolo_lock:
            if _yolo_model is None:
                try:
                    logger.info("Initializing shared YOLO11n model...")
                    _yolo_model = YOLO("yolo11n.pt")
                    logger.info("Shared YOLO11n model loaded successfully.")
                except Exception as e:
                    logger.error(f"Failed to load shared YOLO model: {e}")
                    _yolo_model = None
    return _yolo_model

CLASS_NAME_MAP = {
    0: "person",
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
}

class AddCameraPayload(BaseModel):
    camera_id: str
    name: Optional[str] = None
    location_name: Optional[str] = None
    source: str
    source_type: Optional[str] = "video"
    direction_covered: Optional[str] = "Northbound"
    gps: Optional[List[float]] = [25.2914, 79.8713]


def process_camera_stream(camera_id: str, raw_source: str):
    """
    Dedicated background processing thread per camera.
    Runs homography speed estimation, Kalman filtering, and trajectory rendering.
    """
    global latest_frames, active_camera_threads, active_captures, active_camera_counts, lane_vehicle_counts, stream_errors

    logger.info(f"Starting analytics pipeline thread for {camera_id} with source: {raw_source}...")
    active_camera_threads[camera_id] = True

    db = DatabaseManager("data/roadguardian.db")
    speed_calculator = HomographySpeedEstimator()
    kalman_filter = VehicleStateFilter(sigma_m=4.0, sigma_a=60.0, fps=30.0)
    behavior_engine = AdvancedBehaviorEngine(
        pixels_per_meter=15.0, speed_limit_kmh=100.0, drift_angle_threshold_deg=50.0, min_drift_speed_kmh=75.0
    )
    view_classifier = VehicleViewClassifier(device="cpu")
    torch_device = "cuda" if torch.cuda.is_available() else "cpu"

    try:
        speaker = AudioSpeaker()
    except Exception:
        speaker = None

    vehicle_model = get_shared_yolo()

    res_info = resolve_camera_source(raw_source)
    clean_src = res_info.get("resolved_source", raw_source)
    src_type = res_info.get("source_type", "video")

    cap, err = open_capture_handle(clean_src, src_type)
    if not cap or not cap.isOpened():
        logger.error(f"Failed to open stream for {camera_id}: {err}")
        stream_errors[camera_id] = str(err)
        active_camera_threads[camera_id] = False
        return

    active_captures[camera_id] = cap
    stream_errors.pop(camera_id, None)

    trajectories: Dict[Tuple[str, int], List[Tuple[int, int]]] = {}
    speeds: Dict[Tuple[str, int], float] = {}
    speed_histories: Dict[Tuple[str, int], List[float]] = {}
    stationary_timers: Dict[Tuple[str, int], float] = {}
    track_attributes: Dict[Tuple[str, int], Dict[str, Any]] = {}
    frame_counts: Dict[int, int] = {}

    last_frame_time = time.time()

    consecutive_read_failures = 0
    try:
        while active_camera_threads.get(camera_id, False):
            current_time = time.time()
            dt = current_time - last_frame_time
            last_frame_time = current_time

            try:
                ret, frame = cap.read()
            except (cv2.error, Exception) as read_exc:
                logger.warning(f"cap.read() exception on {camera_id}: {read_exc}")
                ret, frame = False, None

            if not ret or frame is None:
                consecutive_read_failures += 1
                if src_type in ("video", "file"):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    kalman_filter.reset()
                    trajectories.clear()
                    speeds.clear()
                    track_attributes.clear()
                    frame_counts.clear()
                    time.sleep(0.033)
                    continue
                else:
                    time.sleep(0.1)
                    if consecutive_read_failures > 30:
                        logger.error(f"Live camera stream {camera_id} disconnected after 30 read failures.")
                        stream_errors[camera_id] = "Camera stream disconnected or source unavailable"
                        break
                    continue

            consecutive_read_failures = 0

            h, w = frame.shape[:2]

            active_scoped_keys: List[Tuple[str, int]] = []
            active_tracks_summary: Dict[int, Dict[str, Any]] = {}
            drifting_ids: List[int] = []
            alerts: List[str] = []
            has_special = False

            if vehicle_model is not None:
                try:
                    with _yolo_lock:
                        results = vehicle_model.track(
                            frame,
                            persist=True,
                            tracker="bytetrack.yaml",
                            classes=[1, 2, 3, 5, 7],
                            conf=0.20,
                            iou=0.50,
                            imgsz=640,
                            agnostic_nms=True,
                            verbose=False,
                            device=torch_device,
                        )
                except Exception as inference_err:
                    logger.error(f"YOLO Track Error on [{camera_id}]: {inference_err}")
                    if torch_device != "cpu":
                        torch_device = "cpu"
                    preview_frame = cv2.resize(frame, (640, 360))
                    _, jpeg = cv2.imencode(".jpg", preview_frame)
                    latest_frames[camera_id] = jpeg.tobytes()
                    time.sleep(0.033)
                    continue

                if results and len(results) > 0 and results[0].boxes is not None and len(results[0].boxes) > 0:
                        boxes = results[0].boxes.xyxy.cpu().numpy()
                        track_ids = (
                            results[0].boxes.id.cpu().numpy().astype(int)
                            if results[0].boxes.id is not None
                            else np.arange(1, len(boxes) + 1)
                        )
                        class_ids = results[0].boxes.cls.cpu().numpy().astype(int)
                        confs = (
                            results[0].boxes.conf.cpu().numpy()
                            if results[0].boxes.conf is not None
                            else np.ones(len(boxes)) * 0.8
                        )

                        for box, track_id, cls_id, conf_val in zip(boxes, track_ids, class_ids, confs):
                            cls_id = int(cls_id)
                            track_id = int(track_id)
                            scoped_key = (camera_id, track_id)
                            active_scoped_keys.append(scoped_key)
                            x1, y1, x2, y2 = map(int, box)

                            if cls_id in SPECIAL_VEHICLE_CLASSES:
                                has_special = True

                            frame_counts[track_id] = frame_counts.get(track_id, 0) + 1

                            # Kalman Filter & Speed Estimation
                            anchor_pt = (int((x1 + x2) / 2), int(y2))
                            sx, sy, vx, vy = kalman_filter.update(track_id, anchor_pt, dt=dt if dt > 0 else 1.0 / 30.0)
                            smoothed_anchor_pt = (int(round(sx)), int(round(sy)))

                            speed_kmh = speed_calculator.compute_speed(track_id, (x1, y1, x2, y2), current_time)
                            speeds[scoped_key] = speed_kmh
                            speed_display = format_speed(speed_kmh, frame_counts[track_id])

                            if scoped_key not in trajectories:
                                trajectories[scoped_key] = []
                            trajectories[scoped_key].append(smoothed_anchor_pt)
                            if len(trajectories[scoped_key]) > 16:
                                trajectories[scoped_key].pop(0)

                            base_type = CLASS_NAME_MAP.get(cls_id, "car")

                            # Extract real vehicle subtype and dominant color
                            if scoped_key not in track_attributes:
                                crop = frame[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
                                subtype, color = behavior_engine.classify_vehicle(crop, base_type)
                                view_enum = view_classifier.predict(crop)
                                view_str = view_enum.value.capitalize()
                                track_attributes[scoped_key] = {
                                    "subtype": subtype,
                                    "color": color,
                                    "view_angle": view_str,
                                    "first_seen": current_time,
                                    "last_seen": current_time,
                                    "last_db_sync": 0.0,
                                }

                            attr = track_attributes[scoped_key]
                            attr["last_seen"] = current_time

                            tracked_obj = global_cross_camera_tracker.update_track(
                                camera_id=camera_id,
                                local_track_id=track_id,
                                bbox=(x1, y1, x2, y2),
                                category=attr["subtype"]
                            )
                            gvid = tracked_obj.gvid

                            # Draw Green Bounding Box & Unified Tag directly on frame
                            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                            tag_label = f"{gvid} | {speed_display}"
                            cv2.putText(frame, tag_label, (x1, max(y1 - 8, 15)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 2, cv2.LINE_AA)

                            # Deduplicated Database Upsert (rate-limited to 2.0s per vehicle)
                            if current_time - attr["last_db_sync"] >= 2.0:
                                attr["last_db_sync"] = current_time
                                try:
                                    db.upsert_vehicle(
                                        gvid=gvid,
                                        camera_id=camera_id,
                                        license_plate="UNREAD",
                                        category=attr["subtype"],
                                        color=attr["color"],
                                        speed=round(speed_kmh, 1),
                                        view_angle=attr["view_angle"]
                                    )
                                except Exception as db_err:
                                    logger.debug(f"DB upsert error on {camera_id}: {db_err}")

                            active_tracks_summary[track_id] = {
                                "track_id": track_id,
                                "gvid": gvid,
                                "class": attr["subtype"].lower(),
                                "category": attr["subtype"],
                                "color": attr["color"],
                                "view_angle": attr["view_angle"],
                                "confidence": round(float(conf_val), 2),
                                "bbox": [x1, y1, x2, y2],
                                "center": anchor_pt,
                                "speed_kmh": round(speed_kmh, 1),
                                "speed_display": speed_display,
                                "timestamp": current_time,
                            }
                except Exception as track_err:
                    logger.warning(f"Tracking error on {camera_id}: {track_err}")

            # Prune stale tracks from attributes cache (> 6.0s lost)
            stale_attr_keys = [k for k, v in track_attributes.items() if (current_time - v.get("last_seen", 0.0)) > 6.0]
            for sk in stale_attr_keys:
                track_attributes.pop(sk, None)
                trajectories.pop(sk, None)
                speeds.pop(sk, None)
                frame_counts.pop(sk[1], None)

            # Draw visual trajectories and dynamic overlay text
            active_trajectories = {k[1]: v for k, v in trajectories.items() if k in active_scoped_keys}
            active_speeds = {k[1]: v for k, v in speeds.items() if k in active_scoped_keys}
            frame = draw_trajectories_and_events(frame, active_trajectories, active_speeds, drifting_ids, alerts)

            # Update vehicle count telemetry and live state manager
            detected_count = len(active_scoped_keys)
            active_camera_counts[camera_id] = detected_count
            lane_vehicle_counts[camera_id] = detected_count
            lane_special_vehicles[camera_id] = has_special
            allocator.update_density(camera_id, detected_count, has_special)

            if camera_id in ("CAMERA_01", "CAM_01"):
                lane_vehicle_counts["LANE_1"] = detected_count
                lane_special_vehicles["LANE_1"] = has_special
                allocator.update_density("LANE_1", detected_count, has_special)
            elif camera_id.startswith("SIM_"):
                parts = camera_id.split("_")
                if len(parts) >= 4:
                    lane_id = "_".join(parts[3:])
                    lane_vehicle_counts[lane_id] = detected_count
                    lane_special_vehicles[lane_id] = has_special
                    allocator.update_density(lane_id, detected_count, has_special)

            live_state_manager.update_camera(
                camera_id=camera_id,
                vehicle_count=detected_count,
                active_tracks=active_tracks_summary,
                trajectories=active_trajectories,
                speeds=active_speeds,
                emergency_active=has_special,
            )

            # Encode ANNOTATED frame for streaming (DO NOT ENCODE RAW UNANNOTATED FRAME)
            preview_frame = cv2.resize(frame, (640, 360))
            success, encoded_img = cv2.imencode(".jpg", preview_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
            if success:
                latest_frames[camera_id] = encoded_img.tobytes()
                if camera_id == "CAMERA_01" or len(latest_frames) == 1:
                    latest_frames["DEFAULT"] = encoded_img.tobytes()

            time.sleep(0.033)

    except Exception as err:
        logger.error(f"Camera stream thread {camera_id} encountered exception: {err}", exc_info=True)
    finally:
        release_capture_handle(cap)
        active_captures.pop(camera_id, None)
        active_camera_counts.pop(camera_id, None)
        lane_vehicle_counts.pop(camera_id, None)
        lane_special_vehicles.pop(camera_id, None)
        allocator.update_density(camera_id, 0, False)
        if camera_id in ("CAMERA_01", "CAM_01"):
            lane_vehicle_counts.pop("LANE_1", None)
            lane_special_vehicles.pop("LANE_1", None)
            allocator.update_density("LANE_1", 0, False)
        elif camera_id.startswith("SIM_"):
            parts = camera_id.split("_")
            if len(parts) >= 4:
                lane_id = "_".join(parts[3:])
                lane_vehicle_counts.pop(lane_id, None)
                lane_special_vehicles.pop(lane_id, None)
                allocator.update_density(lane_id, 0, False)
        active_camera_threads[camera_id] = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs("static/incidents", exist_ok=True)
    os.makedirs("uploads", exist_ok=True)
    os.makedirs("config", exist_ok=True)
    os.makedirs("data", exist_ok=True)

    # Initialize cameras from config/cameras.json on startup
    config_path = "config/cameras.json"
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                cfg = json.load(f)
            for c in cfg.get("cameras", []):
                cid = c.get("id")
                src = c.get("source")
                if cid and src and c.get("enabled", True):
                    t = threading.Thread(target=process_camera_stream, args=(cid, src), daemon=True)
                    t.start()
        except Exception as e:
            logger.warning(f"Could not load initial cameras: {e}")

    yield

    # Shutdown all stream threads
    for cid in list(active_camera_threads.keys()):
        active_camera_threads[cid] = False


app = FastAPI(
    title="RoadGuardian ITS Engine API",
    description="Backend engine for traffic surveillance, homography speed estimation, and multi-camera ingestion.",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root_healthcheck():
    return {"system": "RoadGuardian ITS Engine", "status": "online", "active_feeds": list(latest_frames.keys())}


@app.post("/api/upload_video")
async def upload_video_file(
    camera_id: Optional[str] = Query("LANE_1"),
    file: UploadFile = File(...)
):
    """Saves uploaded files to camera-specific disk path and returns file path for thread binding."""
    try:
        cam_id = (camera_id or "LANE_1").strip()
        target_dir = os.path.join("uploads", cam_id).replace("\\", "/")
        os.makedirs(target_dir, exist_ok=True)
        clean_name = file.filename.replace(" ", "_").replace("\\", "/")
        unique_filename = f"{uuid.uuid4().hex[:8]}_{clean_name}"
        destination_path = os.path.join(target_dir, unique_filename).replace("\\", "/")
        
        with open(destination_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        res_info = resolve_camera_source(destination_path)
        return {
            "status": "success",
            "file_path": destination_path,
            "camera_id": cam_id,
            "source_type": res_info.get("source_type", "file")
        }
    except Exception as e:
        logger.error(f"Upload error: {e}")
        raise HTTPException(status_code=500, detail=f"Upload error: {str(e)}")


@app.get("/api/cameras")
async def list_registered_cameras():
    """Returns list of registered cameras enriched with real-time status and metrics."""
    config_path = "config/cameras.json"
    cameras = []
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                cfg = json.load(f)
            cameras = cfg.get("cameras", [])
        except Exception as e:
            logger.warning(f"Could not read config/cameras.json: {e}")

    # Compute dynamic adaptive signal states
    active_counts_snapshot = dict(active_camera_counts)
    sig_res = calculate_adaptive_signal({"junction_id": "MAIN_JUNCTION"}, active_counts_snapshot)
    signals_dict = sig_res.get("signals", {})

    enriched = []
    for c in cameras:
        cid = c.get("id", "")
        is_active = active_camera_threads.get(cid, False)
        cnt = active_camera_counts.get(cid, 0)
        sig_info = signals_dict.get(cid, {})
        current_light = sig_info.get("state", "GREEN")
        countdown = sig_info.get("timer", 15)
        rec_green = max(8, min(60, int(round(cnt * 2.5)))) if cnt > 0 else 10

        if cnt < 5:
            los = "LOS A"
        elif cnt < 10:
            los = "LOS B"
        elif cnt < 18:
            los = "LOS C"
        elif cnt < 25:
            los = "LOS D"
        else:
            los = "LOS E/F"

        enriched.append({
            "id": cid,
            "name": c.get("name", cid),
            "location_name": c.get("location_name", "Sector Corridor"),
            "source": c.get("source", ""),
            "source_type": c.get("source_type", "video"),
            "gps": c.get("gps", [25.2914, 79.8713]),
            "direction_covered": c.get("direction_covered", "Northbound"),
            "enabled": c.get("enabled", True),
            "status": "STREAMING" if is_active else "STANDBY",
            "vehicle_count": cnt,
            "density": round(cnt * 1.5, 1),
            "los": los,
            "signal": {
                "current_light": current_light,
                "countdown": countdown,
                "cycle_duration": 30,
                "recommended_green": rec_green,
                "mode": "AI_ADAPTIVE"
            }
        })
    return {"status": "success", "data": enriched, "cameras": enriched}


@app.post("/api/cameras/add")
async def register_camera_stream(payload: AddCameraPayload):
    """Registers camera feed and launches independent analytics thread."""
    cam_id = payload.camera_id.strip()
    src = payload.source.strip()

    if not cam_id or not src:
        raise HTTPException(status_code=400, detail="Camera ID and source path required.")

    if cam_id in active_camera_threads:
        active_camera_threads[cam_id] = False
        time.sleep(0.1)

    # Save to config/cameras.json
    config_path = "config/cameras.json"
    os.makedirs("config", exist_ok=True)
    data = {"cameras": []}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                data = json.load(f)
        except Exception:
            data = {"cameras": []}

    res_info = resolve_camera_source(src)
    clean_source = res_info.get("resolved_source", src)

    new_cam = {
        "id": cam_id,
        "name": payload.name or cam_id,
        "location_name": payload.location_name or "Sector Corridor",
        "source": clean_source,
        "source_type": res_info.get("source_type", "video"),
        "gps": payload.gps or [25.2914, 79.8713],
        "direction_covered": payload.direction_covered or "Northbound",
        "enabled": True
    }
    data["cameras"] = [c for c in data.get("cameras", []) if c.get("id") != cam_id]
    data["cameras"].append(new_cam)
    with open(config_path, "w") as f:
        json.dump(data, f, indent=2)

    t = threading.Thread(target=process_camera_stream, args=(cam_id, src), daemon=True)
    t.start()

    enriched_cam = {
        **new_cam,
        "status": "STREAMING",
        "vehicle_count": 0,
        "density": 0.0,
        "los": "LOS A",
        "signal": {
            "current_light": "GREEN",
            "countdown": 25,
            "cycle_duration": 60,
            "recommended_green": 30,
            "mode": "AI_ADAPTIVE"
        }
    }

    return {"status": "success", "message": f"Analytics pipeline active for {cam_id}", "data": enriched_cam, "camera": enriched_cam}


@app.delete("/api/cameras/{camera_id}")
@app.post("/api/cameras/{camera_id}/delete")
async def remove_camera_stream(camera_id: str):
    """Stops analytics thread and clears feed buffer."""
    if camera_id in active_camera_threads:
        active_camera_threads[camera_id] = False
        latest_frames.pop(camera_id, None)

    if camera_id in active_captures:
        release_capture_handle(active_captures[camera_id])
        active_captures.pop(camera_id, None)

    config_path = "config/cameras.json"
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                data = json.load(f)
            data["cameras"] = [c for c in data.get("cameras", []) if c.get("id") != camera_id]
            with open(config_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    return {"status": "success", "message": f"Camera {camera_id} removed"}


@app.post("/api/cameras/{camera_id}/retry")
async def retry_camera_stream(camera_id: str):
    """Restarts analytics thread for camera."""
    config_path = "config/cameras.json"
    if not os.path.exists(config_path):
        raise HTTPException(status_code=404, detail="No camera configuration file found")

    with open(config_path, "r") as f:
        data = json.load(f)

    cam_info = next((c for c in data.get("cameras", []) if c["id"] == camera_id), None)
    if not cam_info:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} not registered")

    if camera_id in active_camera_threads:
        active_camera_threads[camera_id] = False
        time.sleep(0.1)

    t = threading.Thread(target=process_camera_stream, args=(camera_id, cam_info["source"]), daemon=True)
    t.start()
    return {"status": "success", "message": f"Stream re-initialized for {camera_id}"}


@app.get("/api/cameras/{camera_id}/status")
async def fetch_camera_status(camera_id: str):
    """Status diagnostics for CameraCard.tsx error handling."""
    is_active = active_camera_threads.get(camera_id, False)
    return {
        "status": "online" if is_active else "offline",
        "camera_id": camera_id,
        "diagnostics": {"reason": stream_errors.get(camera_id) if not is_active else None}
    }


@app.get("/api/validate_source")
async def validate_camera_source(source: str = Query(...)):
    """Validates USB webcams, RTSP network feeds, and video files for front-end ingestion."""
    clean = source.strip().replace("\\", "/").strip('"\'')
    if clean.isdigit():
        idx = int(clean)
        cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW) if os.name == "nt" else cv2.VideoCapture(idx)
        is_ok = cap.isOpened()
        if is_ok:
            cap.release()
            return {"valid": True, "type": "webcam", "message": f"Webcam device {idx} verified online."}
        return {"valid": False, "type": "webcam", "message": f"Webcam device {idx} is unavailable or locked."}
    elif clean.startswith(("rtsp://", "http://", "https://")):
        return {"valid": True, "type": "network", "message": "Network stream URL validated."}
    else:
        if os.path.exists(clean):
            return {"valid": True, "type": "file", "message": "Video file verified on disk."}
        return {"valid": False, "type": "file", "message": f"File not found on server disk: '{clean}'"}


@app.get("/api/observations/latest")
async def fetch_latest_observations(
    camera_id: Optional[str] = None,
    search: Optional[str] = None,
    vehicle_type: Optional[str] = None,
    color: Optional[str] = None,
    limit: int = 50
):
    try:
        db = DatabaseManager("data/roadguardian.db")
        records = db.get_vehicle_inventory(
            camera_id=camera_id,
            search=search,
            vehicle_type=vehicle_type,
            color=color,
            limit=limit
        )

        formatted_records = []
        for r in records:
            raw_speed = float(r.get("speed") or 0.0)
            formatted_records.append({
                "gvid": r.get("gvid"),
                "camera_id": r.get("camera_id"),
                "vehicle_type": (r.get("category") or "Car").lower(),
                "category": r.get("category") or "Car",
                "vehicle_subtype": r.get("category") or "Car",
                "speed": round(raw_speed, 1),
                "speed_kmh": round(raw_speed, 1),
                "license_plate": r.get("license_plate") or "UNREAD",
                "view_angle": r.get("view_angle") or "Front",
                "color": r.get("color") or "UNKNOWN",
                "last_seen": r.get("last_seen"),
                "timestamp": r.get("last_seen")
            })
        return {"status": "success", "data": formatted_records}
    except Exception as e:
        logger.error(f"Error fetching vehicle observations: {e}")
        return {"status": "success", "data": []}


@app.get("/api/stream/{camera_id}")
async def stream_feed(camera_id: str):
    """
    Zero-Crash Stream Handler.
    Catches ALL runtime errors and guarantees continuous JPEG bytes (No HTTP 500s).
    """
    def generate_frames():
        # Pre-encoded 640x360 Fallback JPEG Buffer
        blank = np.zeros((360, 640, 3), dtype=np.uint8)
        cv2.rectangle(blank, (10, 10), (630, 350), (0, 0, 150), 2)
        cv2.putText(blank, f"RECONNECTING STREAM: {camera_id}", (40, 170), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 2)
        cv2.putText(blank, "Initializing analytics processing handle...", (40, 210), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
        _, default_jpeg = cv2.imencode(".jpg", blank)
        fallback_bytes = default_jpeg.tobytes()

        while True:
            try:
                # Safely grab frame buffer without throwing KeyError
                frame_bytes = latest_frames.get(camera_id) if isinstance(latest_frames, dict) else None
                out_bytes = frame_bytes if frame_bytes is not None else fallback_bytes

                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + out_bytes + b'\r\n')
                time.sleep(0.033)
            except Exception as stream_err:
                logger.error(f"Stream error on {camera_id}: {stream_err}")
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + fallback_bytes + b'\r\n')
                time.sleep(0.1)

    return StreamingResponse(generate_frames(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/api/videos/serve")
async def serve_local_video(path: str):
    """Bypasses browser file:/// security blocks by serving local disk MP4 files over HTTP."""
    clean_path = path.replace("\\", "/").strip('"\'')
    if os.path.exists(clean_path):
        return FileResponse(clean_path, media_type="video/mp4")
    raise HTTPException(status_code=404, detail="Local video file not found on disk")


@app.post("/api/junctions/adaptive-signal")
@app.post("/api/junctions/{junction_id}/adaptive-signal")
async def post_adaptive_signal_main(payload: Dict[str, Any], junction_id: Optional[str] = None):
    """Calculates adaptive signal using signal_engine.py logic."""
    junc_id = junction_id or payload.get("junction_id", "Junction_1")
    junc_cfg = payload.get("junction_config", {"junction_id": junc_id})
    densities = payload.get("lane_densities", {})
    emergency_events = payload.get("emergency_events", {})
    return calculate_adaptive_signal(junc_cfg, densities, emergency_events=emergency_events)


@app.get("/api/simulator/densities")
async def get_simulator_densities(junction_id: str = "JUNCTION_01"):
    """Returns live lane vehicle counts derived from active camera streams."""
    lane_densities = {}
    for cid, cnt in active_camera_counts.items():
        if cid.startswith("SIM_"):
            parts = cid.split("_")
            if len(parts) >= 4:
                j_id = f"{parts[1]}_{parts[2]}"
                lane_id = "_".join(parts[3:])
                if j_id.upper() == junction_id.upper() or junction_id.upper() == "ALL":
                    lane_densities[lane_id] = cnt
                    lane_densities[lane_id.lower()] = cnt
                    lane_densities[cid] = cnt
            else:
                lane_densities[cid] = cnt
        else:
            lane_densities[cid] = cnt

    sig_res = calculate_adaptive_signal({"junction_id": junction_id}, lane_densities)
    return {
        "status": "success",
        "junction_id": junction_id,
        "lane_densities": lane_densities,
        "signals": sig_res.get("signals", {}),
        "active_phase": sig_res.get("active_phase"),
        "emergency_preemption": sig_res.get("emergency_preemption", False),
    }


@app.get("/api/alerts")
async def fetch_alerts_endpoint(limit: int = 20):
    """Returns active emergency alerts."""
    return {
        "status": "success",
        "active_emergency_alerts": active_alerts[:limit],
        "data": active_alerts[:limit]
    }


@app.post("/api/alerts/simulate")
async def simulate_incident_endpoint(camera_id: str = "CAMERA_01"):
    """Simulates a confirmed emergency crash incident."""
    alert_id = f"SIM_{uuid.uuid4().hex[:8].upper()}"
    new_alert = {
        "alert_id": alert_id,
        "event_type": "CONFIRMED_INCIDENT",
        "status": "AMBULANCE_REQUIRED",
        "camera_id": camera_id,
        "location_name": "NH-44 Mahoba Corridor",
        "gps": [25.2914, 79.8713],
        "timestamp": time.time(),
        "fusion_score": 4,
        "detected_signals": [
            "SUDDEN_DECELERATION",
            "TRAJECTORY_ZIGZAG",
            "BBOX_DEFORMATION",
            "STATIONARY_IN_LIVE_LANE",
        ],
        "track_id": 104,
        "direction_zone": "Northbound",
        "acknowledged": False
    }
    active_alerts.insert(0, new_alert)
    return {"status": "success", "message": f"Incident simulated on {camera_id}", "payload": new_alert}


@app.post("/api/incidents/{incident_id}/acknowledge")
@app.post("/api/alerts/{incident_id}/acknowledge")
async def acknowledge_incident_endpoint(incident_id: str):
    """Updates incident status to ACKNOWLEDGED in SQLite database and in-memory active list."""
    try:
        db = DatabaseManager("data/roadguardian.db")
        db.acknowledge_incident(incident_id)

        for a in active_alerts:
            if a.get("alert_id") == incident_id:
                a["acknowledged"] = True
                a["status"] = "ACKNOWLEDGED_BY_OPERATOR"

        return {"status": "success", "message": f"Incident {incident_id} acknowledged."}
    except Exception as e:
        logger.error(f"Error acknowledging incident {incident_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to acknowledge incident.")


@app.get("/api/signals/telemetry")
async def get_signal_telemetry():
    """Returns current vehicle counts, state machine snapshot, and dynamic recommendations."""
    snap = allocator.snapshot()
    densities = {k: v.get("density", 0) for k, v in snap.get("signals", {}).items()}
    return {
        "status": "success",
        "snapshot": snap,
        "densities": densities,
        "active_phase": snap.get("active_phase"),
        "current_state": snap.get("current_state"),
        "signals": snap.get("signals", {}),
        "emergency_preemption": any(v.get("special_vehicle", False) for v in snap.get("signals", {}).values())
    }


@app.post("/api/alerts/{alert_id}/dispatch-ambulance")
async def dispatch_ambulance_endpoint(alert_id: str):
    """Marks emergency ambulance as dispatched."""
    for a in active_alerts:
        if a.get("alert_id") == alert_id:
            a["status"] = "AMBULANCE_DISPATCHED"
            return {"status": "success", "alert_id": alert_id, "message": "Ambulance dispatched."}
    return {"status": "success", "alert_id": alert_id, "message": "Ambulance dispatched."}


@app.get("/api/metrics/summary")
@app.get("/api/stats")
async def get_metrics_summary():
    cams = list(active_captures.keys())
    active_emergency_count = len([a for a in active_alerts if a.get("status") == "AMBULANCE_REQUIRED"])
    return {
        "status": "success",
        "high_congestion_zone": {"camera_id": cams[0] if cams else "CAMERA_01", "density": 0.0},
        "active_cameras_count": len(cams),
        "online_cameras_count": len([c for c, act in active_camera_threads.items() if act]),
        "total_vehicles_monitored": sum(active_camera_counts.values()),
        "total_alerts_logged": len(active_alerts),
        "active_emergency_alerts": active_emergency_count,
    }


@app.websocket("/ws/traffic-stream")
async def websocket_traffic_stream(websocket: WebSocket):
    """Real-time WebSocket endpoint for telemetry and density broadcasting."""
    await websocket.accept()
    logger.info("Client connected to /ws/traffic-stream")
    try:
        await websocket.send_json({
            "type": "INITIAL_STATE",
            "active_alerts": active_alerts,
            "high_congestion_zone": list(active_camera_threads.keys())[0] if active_camera_threads else "CAMERA_01"
        })

        while True:
            # Calculate dynamic signal engine states for all active cameras
            active_counts_snapshot = dict(active_camera_counts)
            sig_res = calculate_adaptive_signal({"junction_id": "MAIN_JUNCTION"}, active_counts_snapshot)
            signals_dict = sig_res.get("signals", {})

            cams_telemetry = []
            for cid in list(active_camera_threads.keys()):
                cnt = active_counts_snapshot.get(cid, 0)
                sig_info = signals_dict.get(cid, {})
                current_light = sig_info.get("state", "GREEN")
                countdown = sig_info.get("timer", 15)
                rec_green = max(8, min(60, int(round(cnt * 2.5)))) if cnt > 0 else 10

                if cnt < 5:
                    los = "LOS A"
                elif cnt < 10:
                    los = "LOS B"
                elif cnt < 18:
                    los = "LOS C"
                elif cnt < 25:
                    los = "LOS D"
                else:
                    los = "LOS E/F"

                cams_telemetry.append({
                    "camera_id": cid,
                    "vehicle_count": cnt,
                    "density": round(cnt * 1.5, 1),
                    "los": los,
                    "signal": {
                        "current_light": current_light,
                        "countdown": countdown,
                        "cycle_duration": 30,
                        "recommended_green": rec_green,
                        "mode": "AI_ADAPTIVE"
                    }
                })

            high_cong_zone = (
                max(active_counts_snapshot, key=active_counts_snapshot.get)
                if active_counts_snapshot
                else (list(active_camera_threads.keys())[0] if active_camera_threads else "CAMERA_01")
            )

            await websocket.send_json({
                "type": "TELEMETRY_UPDATE",
                "timestamp": time.time(),
                "cameras": cams_telemetry,
                "high_congestion_zone": high_cong_zone
            })

            sim_densities = {}
            for cid, cnt in active_camera_counts.items():
                if cid.startswith("SIM_"):
                    parts = cid.split("_")
                    if len(parts) >= 4:
                        lane_id = "_".join(parts[3:])
                        sim_densities[lane_id] = cnt
                        sim_densities[lane_id.lower()] = cnt
                    sim_densities[cid] = cnt

            if sim_densities:
                sig_res = calculate_adaptive_signal({"junction_id": "JUNCTION_01"}, sim_densities)
                await websocket.send_json({
                    "type": "SIMULATOR_DENSITY_UPDATE",
                    "junction_id": "JUNCTION_01",
                    "lane_densities": sim_densities,
                    "signals": sig_res.get("signals", {}),
                    "active_phase": sig_res.get("active_phase"),
                    "emergency_preemption": sig_res.get("emergency_preemption", False)
                })

            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected gracefully.")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)