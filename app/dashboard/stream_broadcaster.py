import os
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "threads;1"
os.environ["OPENCV_VIDEOIO_PRIORITY_MSMF"] = "0"

import json
import time
import threading
import cv2
cv2.setNumThreads(1)
import numpy as np
import logging
from typing import Dict, List, Any, Optional, Set
from fastapi import WebSocket

from app.database.database import DatabaseManager
from app.analytics.speed_estimator import HomographySpeedEstimator
from app.analytics.vehicle_safety import SpeedEstimator, CrashDetector, format_speed
from app.core.source_resolver import resolve_camera_source, open_capture_handle, release_capture_handle
from app.analytics.signal_engine import calculate_adaptive_signal

logger = logging.getLogger("StreamBroadcaster")

CONFIG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../config/cameras.json"))


class StreamBroadcaster:
    """Manages video frame ingestion, telemetry aggregation, AI signal predictions,
    and real-time WebSocket broadcasting for the RoadGuardian ITS Control Room.
    """

    def __init__(self, config_path: str = CONFIG_PATH):
        self.config_path = config_path
        self.cameras: Dict[str, Dict[str, Any]] = {}
        self.caps: Dict[str, cv2.VideoCapture] = {}
        self.latest_frames: Dict[str, bytes] = {}
        self.diagnostics: Dict[str, Dict[str, Any]] = {}
        self.homography_calibrators: Dict[str, HomographySpeedEstimator] = {}
        self.speed_estimators: Dict[str, SpeedEstimator] = {}
        self.crash_detectors: Dict[str, CrashDetector] = {}
        self.active_websockets: Set[WebSocket] = set()
        self.running = False
        self.lock = threading.RLock()
        self.loop: Optional[Any] = None

        # Database manager for vehicle inventory upserts
        self.db = DatabaseManager()

        # Visual annotation & tracking state per camera
        self.subtractors: Dict[str, Any] = {}
        self.camera_tracks: Dict[str, Dict[int, Dict[str, Any]]] = {}
        self.next_track_ids: Dict[str, int] = {}
        self.frame_counters: Dict[str, int] = {}

        # YOLO Model for high-precision vehicle detection
        self.yolo_model = None
        yolo_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../yolo11n.pt"))
        if os.path.exists(yolo_path):
            try:
                from ultralytics import YOLO
                self.yolo_model = YOLO(yolo_path)
                logger.info(f"StreamBroadcaster loaded YOLO model from {yolo_path}")
            except Exception as e:
                logger.warning(f"Failed to load YOLO model: {e}")

        # Active emergency alerts
        self.active_alerts: List[Dict[str, Any]] = []

        # Signal states per camera
        self.signal_states: Dict[str, Dict[str, Any]] = {}
        self.pending_reloads: Set[str] = set()

        self.load_cameras()

    def load_cameras(self):
        """Loads camera configurations from JSON."""
        with self.lock:
            if os.path.exists(self.config_path):
                try:
                    with open(self.config_path, "r", encoding="utf-8-sig") as f:
                        data = json.load(f)
                    for cam in data.get("cameras", []):
                        cid = cam["id"]
                        self.cameras[cid] = cam
                        if cid not in self.signal_states:
                            self.signal_states[cid] = {
                                "camera_id": cid,
                                "current_light": "GREEN",
                                "countdown": 30,
                                "cycle_duration": 30,
                                "recommended_green": 30,
                                "mode": "AI_ADAPTIVE",
                                "los": "LOS A",
                                "density": 5.0,
                            }
                except Exception as e:
                    logger.error(f"Error loading cameras from {self.config_path}: {e}")

    def save_cameras(self):
        """Persists camera configurations to JSON."""
        with self.lock:
            try:
                data = {"cameras": list(self.cameras.values())}
                with open(self.config_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except Exception as e:
                logger.error(f"Error saving cameras to {self.config_path}: {e}")

    def add_camera(self, camera_data: Dict[str, Any]):
        """Registers and initializes a new camera source, queuing reload for capture thread."""
        cid = camera_data["id"]
        with self.lock:
            self.cameras[cid] = camera_data
            self.pending_reloads.add(cid)
            if cid not in self.signal_states:
                self.signal_states[cid] = {
                    "camera_id": cid,
                    "current_light": "GREEN",
                    "countdown": 30,
                    "cycle_duration": 30,
                    "recommended_green": 30,
                    "mode": "AI_ADAPTIVE",
                    "los": "LOS A",
                    "density": 6.0,
                }
        self.save_cameras()

    def is_camera_streaming(self, cid: str) -> bool:
        """Returns True if the camera capture handle is active and open."""
        with self.lock:
            return cid in self.caps and self.caps[cid] is not None and self.caps[cid].isOpened()

    def remove_camera(self, camera_id: str):
        """Removes a camera source and queues capture handle release on the worker thread."""
        with self.lock:
            self.cameras.pop(camera_id, None)
            self.pending_reloads.add(camera_id)
            self.signal_states.pop(camera_id, None)
            self.latest_frames.pop(camera_id, None)
            self.camera_tracks.pop(camera_id, None)
            self.next_track_ids.pop(camera_id, None)
            self.frame_counters.pop(camera_id, None)
            self.diagnostics.pop(camera_id, None)
        self.save_cameras()

    def start(self):
        """Starts background frame reader and telemetry broadcaster threads."""
        if not self.running:
            self.running = True
            self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
            self.capture_thread.start()
            self.telemetry_thread = threading.Thread(target=self._telemetry_loop, daemon=True)
            self.telemetry_thread.start()
            logger.info("StreamBroadcaster threads started.")

    def stop(self):
        """Stops background threads and releases video captures."""
        self.running = False
        with self.lock:
            for cap in self.caps.values():
                release_capture_handle(cap)
            self.caps.clear()
        logger.info("StreamBroadcaster stopped.")

    def _get_or_open_cap(self, cid: str, cam_info: Dict[str, Any]) -> Optional[cv2.VideoCapture]:
        """Opens or rewinds a VideoCapture source supporting local USB webcams (0, 1),
        IP network RTSP/HTTP streams, and local video files.
        """
        if cid in self.caps and self.caps[cid] is not None and self.caps[cid].isOpened():
            return self.caps[cid]

        raw_source = cam_info.get("source")
        res = resolve_camera_source(raw_source)
        if not res["is_valid"]:
            self.diagnostics[cid] = {
                "state": "ERROR",
                "reason": res["error"] or res["message"],
                "source": str(raw_source),
                "source_type": res["source_type"],
            }
            return None

        cap, err = open_capture_handle(res["resolved_source"], res["source_type"])
        if not cap or not cap.isOpened():
            self.diagnostics[cid] = {
                "state": "ERROR",
                "reason": err or f"Could not open source: {raw_source}",
                "source": str(res["resolved_source"]),
                "source_type": res["source_type"],
            }
            return None

        self.caps[cid] = cap
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 640)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 360)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
        self.diagnostics[cid] = {
            "state": "STREAMING",
            "reason": "Connected",
            "source": str(res["resolved_source"]),
            "source_type": res["source_type"],
            "resolution": f"{w}x{h}",
            "fps": fps,
        }

        # If it's a long video file, stagger initial offset safely
        if res["source_type"] == "file":
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if total_frames > 600:
                try:
                    cam_keys = list(self.cameras.keys())
                    idx = cam_keys.index(cid) if cid in cam_keys else 0
                    offset = (idx * 200) % max(1, total_frames - 200)
                    cap.set(cv2.CAP_PROP_POS_FRAMES, offset)
                except Exception:
                    pass

        return cap

    def retry_camera(self, cid: str) -> Dict[str, Any]:
        """Forcibly releases and re-initializes a camera capture source on request."""
        with self.lock:
            if cid in self.caps:
                release_capture_handle(self.caps[cid])
                del self.caps[cid]
            self.latest_frames.pop(cid, None)
            self.camera_tracks.pop(cid, None)
            self.frame_counters.pop(cid, None)

            # Reload config to pick up any external changes
            self.load_cameras()

            cam_info = self.cameras.get(cid)
            if not cam_info:
                if cid == "CAMERA_01" and self.cameras:
                    cam_info = self.cameras.get("DD", next(iter(self.cameras.values())))

            if not cam_info:
                diag = {"state": "ERROR", "reason": f"Camera '{cid}' is not registered."}
                self.diagnostics[cid] = diag
                return {"status": "error", "message": diag["reason"], "diagnostics": diag}

            cap = self._get_or_open_cap(cid, cam_info)
            diag = self.diagnostics.get(cid, {"state": "UNKNOWN", "reason": "Failed to open"})
            if cap and cap.isOpened():
                return {"status": "success", "message": "Stream recovered and initialized", "diagnostics": diag}
            else:
                return {"status": "error", "message": diag.get("reason", "Could not open stream"), "diagnostics": diag}

    def _annotate_frame(self, cid: str, frame: np.ndarray) -> np.ndarray:
        """Draws real-time detection overlays directly onto OpenCV frames:
        1. Bounding box around detected vehicles (cv2.rectangle in #00FF00 -> (0, 255, 0)).
        2. Centroid smoothing with EMA (alpha = 0.6) to eliminate trajectory jitter.
        3. Motion trajectory trail using last 15 smoothed center points (cv2.line in #00FFFF -> (0, 255, 255)).
        4. Real-time speed via pixel displacement over frame deltas: (Δpixels / Δt) * scale * 3.6.
        5. Text HUD above bounding box showing GVID, speed, and category.
        6. Purge tracks lost for > 0.8s or confidence < 0.45.
        7. Zero phantom fallback cars: when no vehicles are detected, no fake boxes are drawn.
        8. Deduplicated SQLite upsert into vehicle_inventory table.
        """
        h, w = frame.shape[:2]
        now = time.time()
        frame_area = float(w * h)

        if cid not in self.camera_tracks:
            self.camera_tracks[cid] = {}
        if cid not in self.next_track_ids:
            self.next_track_ids[cid] = 101
        if cid not in self.frame_counters:
            self.frame_counters[cid] = 0

        self.frame_counters[cid] += 1
        frame_idx = self.frame_counters[cid]

        tracks = self.camera_tracks[cid]
        detected_boxes = []  # list of (x1, y1, x2, y2, cx, cy, conf, category)

        # 1. VEHICLE DETECTION PIPELINE
        # Run YOLO every 2 frames for balanced performance and real-time response
        COCO_VEHICLES = {2: "Car", 3: "Motorcycle", 5: "Bus", 7: "Truck"}

        if self.yolo_model is not None and frame_idx % 2 == 0:
            try:
                results = self.yolo_model.predict(frame, conf=0.55, verbose=False)
                if results and len(results) > 0 and results[0].boxes is not None:
                    for box in results[0].boxes:
                        cls_id = int(box.cls[0].item())
                        conf = float(box.conf[0].item())
                        if cls_id not in COCO_VEHICLES or conf < 0.55:
                            continue
                        xy = box.xyxy[0].tolist()
                        x1, y1, x2, y2 = int(xy[0]), int(xy[1]), int(xy[2]), int(xy[3])
                        bw, bh = x2 - x1, y2 - y1
                        box_area = float(bw * bh)
                        # Reject noise: boxes < 1.5% or > 80% of frame area
                        if box_area < 0.015 * frame_area or box_area > 0.80 * frame_area:
                            continue
                        cx = (x1 + x2) / 2.0
                        cy = (y1 + y2) / 2.0
                        detected_boxes.append((x1, y1, x2, y2, cx, cy, conf, COCO_VEHICLES[cls_id]))
            except Exception as e:
                logger.error(f"YOLO detection error on {cid}: {e}")
        elif self.yolo_model is None:
            # Fallback to background subtractor only if YOLO is unavailable
            if cid not in self.subtractors:
                self.subtractors[cid] = cv2.createBackgroundSubtractorMOG2(
                    history=180, varThreshold=25, detectShadows=False
                )
            sub = self.subtractors[cid]
            fg_mask = sub.apply(frame)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
            fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, kernel)
            fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_DILATE, kernel, iterations=2)
            contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for c in contours:
                area = cv2.contourArea(c)
                if area > 1200:
                    x, y, bw, bh = cv2.boundingRect(c)
                    box_area = float(bw * bh)
                    if 0.015 * frame_area <= box_area <= 0.80 * frame_area:
                        detected_boxes.append((x, y, x + bw, y + bh, x + bw / 2.0, y + bh / 2.0, 0.65, "Car"))

        # 2. MATCH DETECTIONS TO TRACKS (Centroid distance + EMA smoothing)
        matched_track_ids = set()
        for x1, y1, x2, y2, cx, cy, conf, category in detected_boxes:
            best_tid = None
            min_dist = 85.0

            for tid, tdata in tracks.items():
                if tid in matched_track_ids:
                    continue
                tcx, tcy = tdata["history"][-1]
                dist = np.hypot(cx - tcx, cy - tcy)
                if dist < min_dist:
                    min_dist = dist
                    best_tid = tid

            if best_tid is not None:
                matched_track_ids.add(best_tid)
                tdata = tracks[best_tid]
                prev_cx, prev_cy = tdata["history"][-1]

                # EMA Smoothing on centroid: smooth = 0.6 * curr + 0.4 * prev
                smooth_cx = int(round(0.6 * cx + 0.4 * prev_cx))
                smooth_cy = int(round(0.6 * cy + 0.4 * prev_cy))

                # Real-time speed via HomographySpeedEstimator and SpeedEstimator
                if cid not in self.homography_calibrators:
                    self.homography_calibrators[cid] = HomographySpeedEstimator(frame_width=w, frame_height=h)
                if cid not in self.speed_estimators:
                    self.speed_estimators[cid] = SpeedEstimator(ema_alpha=0.60, max_valid_speed=180.0)
                if cid not in self.crash_detectors:
                    self.crash_detectors[cid] = CrashDetector(min_pre_speed=15.0, min_speed_drop=25.0, window_sec=1.5, min_consecutive_checks=3)

                # Ground-plane homography mapping
                bx = (x1 + x2) / 2.0
                by = float(y2)
                rx, ry = self.homography_calibrators[cid].transform_point(bx, by)

                # Speed update with real-world meters and wall-clock timestamp
                speed = self.speed_estimators[cid].update(best_tid, rx, ry, now)
                tdata["speed"] = speed
                tdata["formatted_speed"] = format_speed(speed)

                tdata["bbox"] = (x1, y1, x2, y2)
                tdata["history"].append((smooth_cx, smooth_cy))
                # Limit visual trajectory to last 15 smoothed points
                if len(tdata["history"]) > 15:
                    tdata["history"].pop(0)

                tdata["conf"] = conf
                tdata["category"] = category
                tdata["last_seen"] = now
            else:
                new_tid = self.next_track_ids[cid]
                self.next_track_ids[cid] += 1
                gvid = f"GV_{cid}_{new_tid}"
                
                if cid not in self.homography_calibrators:
                    self.homography_calibrators[cid] = HomographySpeedEstimator(frame_width=w, frame_height=h)
                if cid not in self.speed_estimators:
                    self.speed_estimators[cid] = SpeedEstimator(ema_alpha=0.60, max_valid_speed=180.0)
                if cid not in self.crash_detectors:
                    self.crash_detectors[cid] = CrashDetector(min_pre_speed=15.0, min_speed_drop=25.0, window_sec=1.5, min_consecutive_checks=3)

                rx, ry = self.homography_calibrators[cid].transform_point((x1 + x2) / 2.0, float(y2))
                self.speed_estimators[cid].update(new_tid, rx, ry, now)

                tracks[new_tid] = {
                    "track_id": new_tid,
                    "gvid": gvid,
                    "bbox": (x1, y1, x2, y2),
                    "history": [(int(cx), int(cy))],
                    "speed": 0.0,
                    "formatted_speed": "0 km/h",
                    "conf": conf,
                    "category": category,
                    "color": "UNKNOWN",
                    "license_plate": "UNREAD",
                    "view_angle": "Front",
                    "last_seen": now,
                    "last_db_sync": 0.0,
                }
                matched_track_ids.add(new_tid)

        # 3. PURGE STALE / LOW CONFIDENCE TRACKS
        # Purge when lost for > 0.8s or confidence < 0.45
        stale_tids = [
            tid for tid, tdata in tracks.items()
            if (now - tdata["last_seen"] > 0.8) or (tdata.get("conf", 1.0) < 0.45)
        ]
        for tid in stale_tids:
            if cid in self.speed_estimators:
                self.speed_estimators[cid].purge_track(tid)
            if cid in self.crash_detectors:
                self.crash_detectors[cid].purge_track(tid)
            del tracks[tid]

        # 4. EVALUATE CRASH DETECTION (DUAL CONDITION + PERSISTENCE FILTER)
        all_current_bboxes = {tid: tdata["bbox"] for tid, tdata in tracks.items()}
        for tid, track in tracks.items():
            if cid in self.crash_detectors and cid in self.speed_estimators:
                is_crash = self.crash_detectors[cid].check(
                    tid,
                    track["bbox"],
                    all_current_bboxes,
                    self.speed_estimators[cid],
                    current_time=now,
                )
                if is_crash:
                    logger.warning(f"[CRASH_DETECTED] Confirmed collision event on camera '{cid}' involving vehicle {track['gvid']} (track {tid})")
                    # Save frame snapshot crop to data/crops/
                    crops_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/crops"))
                    os.makedirs(crops_dir, exist_ok=True)
                    snapshot_filename = f"crash_{cid}_{tid}_{int(now)}.jpg"
                    snapshot_path = os.path.join(crops_dir, snapshot_filename)
                    try:
                        cv2.imwrite(snapshot_path, frame)
                    except Exception as e:
                        logger.warning(f"Could not save crash snapshot: {e}")

                    cam_info = self.cameras.get(cid, {})
                    incident_payload = {
                        "event_type": "CONFIRMED_INCIDENT",
                        "status": "AMBULANCE_REQUIRED",
                        "track_id": tid,
                        "fusion_score": 5,
                        "detected_signals": [
                            "ABRUPT_SPEED_DECELERATION",
                            "VEHICLE_BBOX_OVERLAP",
                            "PERSISTENT_CRASH_CONFIRMATION",
                        ],
                        "direction_zone": "DIRECTION_FORWARD",
                        "timestamp": now,
                        "camera_metadata": {
                            "camera_id": cid,
                            "location_name": cam_info.get("location_name", f"Sector {cid}"),
                            "gps": cam_info.get("gps", [25.2914, 79.8713]),
                            "direction_covered": cam_info.get("direction_covered", "Northbound"),
                            "snapshot_url": f"/data/crops/{snapshot_filename}",
                        },
                    }
                    self.broadcast_incident(incident_payload)

        # 5. DRAW OVERLAYS DIRECTLY ONTO OPENCV FRAME
        for tid, track in tracks.items():
            history = track["history"]
            # Visual trajectory trail (#00FFFF -> (0, 255, 255))
            if len(history) > 1:
                for k in range(1, len(history)):
                    cv2.line(frame, history[k - 1], history[k], (0, 255, 255), 2, cv2.LINE_AA)
                    cv2.circle(frame, history[k], 2, (0, 255, 255), -1)

            # Bounding box (#00FF00 -> (0, 255, 0))
            x1, y1, x2, y2 = track["bbox"]
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            # Corner accents
            line_len = min(12, max(4, (x2 - x1) // 4))
            cv2.line(frame, (x1, y1), (x1 + line_len, y1), (0, 255, 255), 2)
            cv2.line(frame, (x1, y1), (x1, y1 + line_len), (0, 255, 255), 2)
            cv2.line(frame, (x2, y2), (x2 - line_len, y2), (0, 255, 255), 2)
            cv2.line(frame, (x2, y2), (x2, y2 - line_len), (0, 255, 255), 2)

            # HUD text above box
            formatted_spd = format_speed(track.get("speed", 0.0))
            hud_text = f"{track['gvid']} | {formatted_spd} | {track.get('category', 'Car')}"
            text_size, _ = cv2.getTextSize(hud_text, cv2.FONT_HERSHEY_SIMPLEX, 0.40, 1)
            tw, th = text_size
            hud_y = max(th + 6, y1 - 6)
            cv2.rectangle(frame, (x1, hud_y - th - 4), (x1 + tw + 6, hud_y + 3), (0, 0, 0), -1)
            cv2.rectangle(frame, (x1, hud_y - th - 4), (x1 + tw + 6, hud_y + 3), (0, 255, 0), 1)
            cv2.putText(
                frame,
                hud_text,
                (x1 + 3, hud_y - 2),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.40,
                (0, 255, 0),
                1,
                cv2.LINE_AA,
            )

            # Rate-limited upsert into database (every 2.0s per vehicle)
            if now - track.get("last_db_sync", 0.0) >= 2.0:
                track["last_db_sync"] = now
                try:
                    self.db.upsert_vehicle(
                        gvid=track["gvid"],
                        camera_id=cid,
                        license_plate=track.get("license_plate", "UNREAD"),
                        category=track.get("category", "Car"),
                        color=track.get("color", "UNKNOWN"),
                        speed=track.get("speed", 45.0),
                        view_angle=track.get("view_angle", "Front"),
                    )
                except Exception:
                    pass

        return frame

    def _generate_synthetic_frame(self, cid: str, cam_info: Dict[str, Any]) -> bytes:
        """Generates an annotated synthetic ITS camera frame when live feed is offline/standby."""
        h, w = 360, 640
        img = np.zeros((h, w, 3), dtype=np.uint8)
        # Dark subtle gradient background
        for y in range(h):
            color = int(10 + (y / h) * 10)
            img[y, :] = (color, color, color + 4)

        # Camera HUD overlay
        loc = cam_info.get("location_name", "Surveillance Sector")
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        cv2.rectangle(img, (0, 0), (w, 30), (0, 0, 0), -1)
        cv2.putText(img, f"{cid} | {loc} | {now_str}", (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1, cv2.LINE_AA)

        # Status badge: OFFLINE (Red)
        cv2.rectangle(img, (w - 100, 4), (w - 10, 26), (0, 0, 160), -1)
        cv2.putText(img, "OFFLINE", (w - 90, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)

        # Center diagnostic text
        diag = self.diagnostics.get(cid, {})
        reason = diag.get("reason", "Feed offline or standby")
        cv2.putText(img, "LIVE STREAM UNAVAILABLE", (w // 2 - 140, h // 2 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 220), 2, cv2.LINE_AA)
        cv2.putText(img, str(reason)[:55], (w // 2 - 160, h // 2 + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (180, 180, 180), 1, cv2.LINE_AA)

        _, encoded = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
        return encoded.tobytes()

    def _capture_loop(self):
        """Continuously reads frames from active camera feeds, annotates them, and caches JPEG buffers."""
        while self.running:
            start_time = time.time()
            with self.lock:
                to_reload = list(self.pending_reloads)
                self.pending_reloads.clear()
            for rcid in to_reload:
                with self.lock:
                    if rcid in self.caps:
                        release_capture_handle(self.caps[rcid])
                        del self.caps[rcid]
                    self.latest_frames.pop(rcid, None)
                    self.camera_tracks.pop(rcid, None)
                    self.frame_counters.pop(rcid, None)

            with self.lock:
                active_cams = list(self.cameras.items())

            for cid, cam_info in active_cams:
                if not cam_info.get("enabled", True):
                    continue

                cap = self._get_or_open_cap(cid, cam_info)
                success = False

                if cap and cap.isOpened():
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        # Resize for optimal dashboard streaming bandwidth (640x360)
                        stream_frame = cv2.resize(frame, (640, 360))

                        # Draw detection overlays directly onto OpenCV frame before encoding
                        stream_frame = self._annotate_frame(cid, stream_frame)

                        # Stamp Camera HUD
                        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
                        cv2.rectangle(stream_frame, (0, 0), (640, 30), (0, 0, 0), -1)
                        cv2.putText(
                            stream_frame,
                            f"{cid} | {cam_info.get('location_name', '')} | {now_str}",
                            (10, 20),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.45,
                            (0, 255, 255),
                            1,
                            cv2.LINE_AA,
                        )

                        _, encoded = cv2.imencode(".jpg", stream_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                        self.latest_frames[cid] = encoded.tobytes()
                        success = True

                        # Update live telemetry state
                        active_count = len(self.camera_tracks.get(cid, {}))
                        active_density = round(active_count * 1.5, 1)
                        ai_eval = self.calculate_traffic_signal_ai(active_density)
                        if cid not in self.signal_states:
                            self.signal_states[cid] = {}
                        self.signal_states[cid]["vehicle_count"] = active_count
                        self.signal_states[cid]["density"] = active_density
                        self.signal_states[cid]["los"] = ai_eval["los"]
                        self.signal_states[cid]["is_live"] = True
                    else:
                        # Check source type for rewinding
                        source_val = cam_info.get("source")
                        source_res = resolve_camera_source(source_val)
                        if source_res["source_type"] == "file":
                            # Rewind video stream on EOF
                            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                            ret, frame = cap.read()
                            if not ret or frame is None:
                                # Re-open handle if seek failed on container
                                release_capture_handle(cap)
                                new_cap, _ = open_capture_handle(source_res["resolved_source"], "file")
                                if new_cap and new_cap.isOpened():
                                    self.caps[cid] = new_cap
                                    cap = new_cap
                                    ret, frame = cap.read()
                            
                            if ret and frame is not None:
                                stream_frame = cv2.resize(frame, (640, 360))
                                stream_frame = self._annotate_frame(cid, stream_frame)
                                now_str = time.strftime("%Y-%m-%d %H:%M:%S")
                                cv2.rectangle(stream_frame, (0, 0), (640, 30), (0, 0, 0), -1)
                                cv2.putText(
                                    stream_frame,
                                    f"{cid} | {cam_info.get('location_name', '')} | {now_str}",
                                    (10, 20),
                                    cv2.FONT_HERSHEY_SIMPLEX,
                                    0.45,
                                    (0, 255, 255),
                                    1,
                                    cv2.LINE_AA,
                                )
                                _, encoded = cv2.imencode(".jpg", stream_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                                self.latest_frames[cid] = encoded.tobytes()
                                success = True

                                active_count = len(self.camera_tracks.get(cid, {}))
                                active_density = round(active_count * 1.5, 1)
                                ai_eval = self.calculate_traffic_signal_ai(active_density)
                                if cid not in self.signal_states:
                                    self.signal_states[cid] = {}
                                self.signal_states[cid]["vehicle_count"] = active_count
                                self.signal_states[cid]["density"] = active_density
                                self.signal_states[cid]["los"] = ai_eval["los"]
                                self.signal_states[cid]["is_live"] = True
                        else:
                            # Live feed disconnected
                            release_capture_handle(cap)
                            self.caps.pop(cid, None)
                            self.diagnostics[cid] = {
                                "state": "OFFLINE",
                                "reason": "Live stream disconnected or stopped delivering frames",
                                "source": str(source_val),
                            }

                if not success:
                    if cid in self.signal_states:
                        self.signal_states[cid]["vehicle_count"] = 0
                        self.signal_states[cid]["density"] = 0.0
                        self.signal_states[cid]["los"] = "OFFLINE"
                        self.signal_states[cid]["is_live"] = False
                    self.latest_frames[cid] = self._generate_synthetic_frame(cid, cam_info)

            # Cap frame rate at ~25 FPS to conserve CPU and stream bandwidth
            elapsed = time.time() - start_time
            sleep_time = max(0.01, (1.0 / 25.0) - elapsed)
            time.sleep(sleep_time)

    def get_latest_frame_jpeg(self, camera_id: str) -> bytes:
        """Returns the most recent JPEG bytes for a camera stream."""
        if camera_id in self.latest_frames:
            return self.latest_frames[camera_id]

        with self.lock:
            cam_info = self.cameras.get(camera_id)
            if not cam_info and camera_id == "CAMERA_01" and self.cameras:
                cam_info = self.cameras.get("DD", next(iter(self.cameras.values())))
            if not cam_info:
                cam_info = {"id": camera_id, "location_name": "Standby"}

        # Attempt to read a live frame immediately
        cap = self._get_or_open_cap(camera_id, cam_info)
        if cap and cap.isOpened():
            ret, frame = cap.read()
            if ret and frame is not None:
                stream_frame = cv2.resize(frame, (640, 360))
                stream_frame = self._annotate_frame(camera_id, stream_frame)
                now_str = time.strftime("%Y-%m-%d %H:%M:%S")
                cv2.rectangle(stream_frame, (0, 0), (640, 30), (0, 0, 0), -1)
                cv2.putText(
                    stream_frame,
                    f"{camera_id} | {cam_info.get('location_name', '')} | {now_str}",
                    (10, 20),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.45,
                    (0, 255, 255),
                    1,
                    cv2.LINE_AA,
                )
                _, encoded = cv2.imencode(".jpg", stream_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                self.latest_frames[camera_id] = encoded.tobytes()
                return self.latest_frames[camera_id]

        fallback = self._generate_synthetic_frame(camera_id, cam_info)
        self.latest_frames[camera_id] = fallback
        return fallback

    def calculate_traffic_signal_ai(self, density: float) -> Dict[str, Any]:
        """Calculates Highway Capacity Manual (HCM) Level of Service (LOS) and
        dynamically predicts the optimal Green Light duration based on vehicle density.

        Rules:
        - High Density (LOS E/F, >= 22.0 veh/km/lane): Extend Green Light (+20s to +40s -> 45s-60s)
        - Moderate Flow (LOS C/D, 11.0-22.0 veh/km/lane): Balanced Cycle (15s-25s)
        - Free Flow (LOS A/B, < 11.0 veh/km/lane): Minimize Green Time (5s-10s) to prioritize busy corridors
        """
        if density >= 28.0:
            los = "LOS F"
            recommended_green = 60
            note = "High Congestion / Gridlock: Maximum green phase extension (+40s)"
        elif density >= 22.0:
            los = "LOS E"
            recommended_green = 48
            note = "Heavy Congestion: Extended green phase (+25s)"
        elif density >= 16.0:
            los = "LOS D"
            recommended_green = 32
            note = "Moderate Heavy Flow: Standard balanced cycle"
        elif density >= 11.0:
            los = "LOS C"
            recommended_green = 22
            note = "Moderate Flow: Balanced cycle (22s)"
        elif density >= 7.0:
            los = "LOS B"
            recommended_green = 12
            note = "Stable Free Flow: Shortened green phase to prioritize arterial corridor"
        elif density > 0.0:
            los = "LOS A"
            recommended_green = 8
            note = "Free Flow: Minimal green phase (8s)"
        else:
            los = "OFFLINE"
            recommended_green = 0
            note = "Feed Offline / No Signal"

        return {
            "los": los,
            "recommended_green": recommended_green,
            "policy_note": note,
            "density": round(density, 1),
        }

    def _telemetry_loop(self):
        """Updates traffic signal countdowns and broadcasts real-time telemetry to WebSocket clients."""
        while self.running:
            time.sleep(1.0)

            # Update simulated signal countdowns per camera
            telemetry_snapshot = []
            with self.lock:
                for cid, state in self.signal_states.items():
                    # Decrement countdown
                    state["countdown"] = max(0, state["countdown"] - 1)
                    if state["countdown"] == 0:
                        # Cycle light
                        if state["current_light"] == "GREEN":
                            state["current_light"] = "YELLOW"
                            state["countdown"] = 4
                        elif state["current_light"] == "YELLOW":
                            state["current_light"] = "RED"
                            state["countdown"] = 25
                        else:
                            state["current_light"] = "GREEN"
                            # Recompute green light duration based on density
                            ai_eval = self.calculate_traffic_signal_ai(state.get("density", 0.0))
                            state["recommended_green"] = ai_eval["recommended_green"]
                            state["countdown"] = max(8, ai_eval["recommended_green"])
                            state["cycle_duration"] = max(8, ai_eval["recommended_green"])
                            state["los"] = ai_eval["los"]

                    # Compute camera live stats
                    cam_info = self.cameras.get(cid, {})
                    is_live = state.get("is_live", False)
                    v_count = state.get("vehicle_count", 0) if is_live else 0
                    dens = state.get("density", 0.0) if is_live else 0.0
                    los_val = state.get("los", "LOS A") if is_live else "OFFLINE"

                    telemetry_snapshot.append({
                        "camera_id": cid,
                        "location_name": cam_info.get("location_name", cid),
                        "direction_covered": cam_info.get("direction_covered", "Northbound"),
                        "gps": cam_info.get("gps", [25.2914, 79.8713]),
                        "vehicle_count": v_count,
                        "density": dens,
                        "los": los_val,
                        "signal": {
                            "current_light": state.get("current_light", "GREEN") if is_live else "OFFLINE",
                            "countdown": state.get("countdown", 0),
                            "cycle_duration": state.get("cycle_duration", 30),
                            "recommended_green": state.get("recommended_green", 30),
                            "mode": state.get("mode", "AI_ADAPTIVE"),
                        },
                    })

            # Identify highest congestion zone
            highest_congestion_cam = None
            max_density = -1.0
            for item in telemetry_snapshot:
                if item["density"] > max_density:
                    max_density = item["density"]
                    highest_congestion_cam = item["camera_id"]

            payload = {
                "type": "TELEMETRY_UPDATE",
                "timestamp": time.time(),
                "high_congestion_zone": highest_congestion_cam or "CAMERA_01",
                "cameras": telemetry_snapshot,
                "active_alerts_count": len([a for a in self.active_alerts if not a.get("acknowledged", False)]),
            }

            self.broadcast_message(payload)

            # Compute and broadcast Simulator Junction Density & Adaptive Signal State
            sim_junctions = {}
            with self.lock:
                for cid, state in self.signal_states.items():
                    if cid.startswith("SIM_"):
                        # Format: SIM_<JUNCTION_ID>_<LANE_ID>
                        parts = cid.split("_")
                        if len(parts) >= 4:
                            j_id = f"{parts[1]}_{parts[2]}"
                            lane_id = "_".join(parts[3:])
                            if j_id not in sim_junctions:
                                sim_junctions[j_id] = {}
                            cnt = state.get("vehicle_count", 0)
                            sim_junctions[j_id][lane_id] = cnt
                            sim_junctions[j_id][lane_id.lower()] = cnt
                            sim_junctions[j_id][cid] = cnt
                        else:
                            if "JUNCTION_01" not in sim_junctions:
                                sim_junctions["JUNCTION_01"] = {}
                            cnt = state.get("vehicle_count", 0)
                            sim_junctions["JUNCTION_01"][cid] = cnt
                            sim_junctions["JUNCTION_01"][cid.lower()] = cnt

            # Broadcast for all simulator junctions found
            for junc_id, lane_dens in sim_junctions.items():
                sig_res = calculate_adaptive_signal({"junction_id": junc_id}, lane_dens)
                self.broadcast_message({
                    "type": "SIMULATOR_DENSITY_UPDATE",
                    "junction_id": junc_id,
                    "lane_densities": lane_dens,
                    "signals": sig_res.get("signals", {}),
                    "active_phase": sig_res.get("active_phase"),
                    "emergency_preemption": sig_res.get("emergency_preemption", False)
                })

    def broadcast_incident(self, incident_payload: Dict[str, Any]):
        """Dispatches an immediate high-priority emergency incident to all connected clients."""
        # Ensure incident payload adheres to control-room emergency specification
        alert_id = f"INCIDENT_{int(time.time() * 1000)}"
        cam_id = incident_payload.get("camera_metadata", {}).get("camera_id", "CAMERA_01")
        loc_name = incident_payload.get("camera_metadata", {}).get("location_name", "Highway Corridor")

        emergency_alert = {
            "alert_id": alert_id,
            "event_type": "CONFIRMED_INCIDENT",
            "camera_id": cam_id,
            "location_name": loc_name,
            "gps": incident_payload.get("camera_metadata", {}).get("gps", [25.2914, 79.8713]),
            "timestamp": incident_payload.get("timestamp", time.time()),
            "fusion_score": incident_payload.get("fusion_score", 3),
            "detected_signals": incident_payload.get("detected_signals", []),
            "track_id": incident_payload.get("track_id", 0),
            "direction_zone": incident_payload.get("direction_zone", "DIRECTION_FORWARD"),
            "status": "AMBULANCE_REQUIRED",
            "acknowledged": False,
            "ambulance_dispatched": False,
            "message": f"CRASH DETECTED AT {cam_id} ({loc_name}) — AMBULANCE REQUIRED",
        }

        with self.lock:
            # Add to active alerts (limit to latest 10)
            self.active_alerts.insert(0, emergency_alert)
            if len(self.active_alerts) > 10:
                self.active_alerts.pop()

        # Immediate WebSocket broadcast outside lock
        msg = {
            "type": "EMERGENCY_ALERT",
            "event_type": "CONFIRMED_INCIDENT",
            "alert": emergency_alert,
        }
        self.broadcast_message(msg)
        logger.warning(f"[BROADCASTER EMERGENCY]: Broadcasted confirmed incident {alert_id} at {cam_id}")

    def acknowledge_alert(self, alert_id: str) -> bool:
        """Marks an alert as acknowledged by operator."""
        found = False
        with self.lock:
            for alert in self.active_alerts:
                if alert["alert_id"] == alert_id:
                    alert["acknowledged"] = True
                    alert["status"] = "ACKNOWLEDGED_BY_OPERATOR"
                    found = True
                    break

        if found:
            self.broadcast_message({
                "type": "ALERT_STATUS_UPDATE",
                "alert_id": alert_id,
                "status": "ACKNOWLEDGED_BY_OPERATOR",
                "acknowledged": True,
            })
            return True
        return False

    def dispatch_ambulance(self, alert_id: str) -> bool:
        """Dispatches ambulance and updates incident status."""
        found = False
        with self.lock:
            for alert in self.active_alerts:
                if alert["alert_id"] == alert_id:
                    alert["ambulance_dispatched"] = True
                    alert["status"] = "AMBULANCE_DISPATCHED"
                    found = True
                    break

        if found:
            self.broadcast_message({
                "type": "ALERT_STATUS_UPDATE",
                "alert_id": alert_id,
                "status": "AMBULANCE_DISPATCHED",
                "ambulance_dispatched": True,
            })
            return True
        return False

    def register_websocket(self, ws: WebSocket):
        """Registers a connected WebSocket client."""
        with self.lock:
            self.active_websockets.add(ws)

    def unregister_websocket(self, ws: WebSocket):
        """Unregisters a disconnected WebSocket client."""
        with self.lock:
            self.active_websockets.discard(ws)

    def broadcast_message(self, message: Dict[str, Any]):
        """Sends JSON message to all connected clients safely across threads."""
        import asyncio
        data = json.dumps(message)
        with self.lock:
            sockets = list(self.active_websockets)

        if not sockets:
            return

        for ws in sockets:
            try:
                if self.loop and self.loop.is_running():
                    asyncio.run_coroutine_threadsafe(ws.send_text(data), self.loop)
                else:
                    # Fallback if in same event loop
                    asyncio.create_task(ws.send_text(data))
            except Exception:
                with self.lock:
                    self.active_websockets.discard(ws)


# Global singleton instance
broadcaster = StreamBroadcaster()
