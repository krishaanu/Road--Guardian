import cv2
import json
import logging
import os
from typing import Dict, Any, Tuple, Optional
from app.core.source_resolver import resolve_camera_source, open_capture_handle, release_capture_handle

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CameraManager")


class CameraSource:
    """Encapsulates a single camera capture stream handle and metadata."""

    def __init__(self, camera_id: str, name: str, source_type: str, source: Any):
        self.camera_id = camera_id
        self.name = name
        self.source_type = source_type
        self.source = str(source).replace("\\", "/").strip('"\'')
        self.cap: Optional[cv2.VideoCapture] = None

    def connect(self) -> bool:
        """Resolves source path/device index and opens OpenCV VideoCapture handle safely."""
        res = resolve_camera_source(self.source)
        if not res.get("is_valid", False):
            logger.error(f"[{self.camera_id}] Invalid camera source '{self.source}': {res.get('error')}")
            return False

        if self.cap is not None:
            release_capture_handle(self.cap)
            self.cap = None

        cap, err = open_capture_handle(res["resolved_source"], res["source_type"])
        if not cap or not cap.isOpened():
            logger.error(f"[{self.camera_id}] Failed to open stream handle for source: {res['resolved_source']}. Reason: {err}")
            return False

        self.cap = cap
        self.source_type = res["source_type"]
        self.source = res["resolved_source"]
        logger.info(f"[{self.camera_id}] Connected successfully to {self.name} ({res['resolved_source']})")
        return True

    def read(self) -> Tuple[bool, Any]:
        """Reads the next video frame from active capture handle safely."""
        if self.cap is None or not self.cap.isOpened():
            return False, None
        return self.cap.read()

    def release(self):
        """Releases capture handle resources cleanly."""
        if self.cap is not None:
            release_capture_handle(self.cap)
            self.cap = None
            logger.info(f"[{self.camera_id}] Stream handle released.")


class CameraManager:
    """Manages multi-camera streams from JSON configuration files."""

    def __init__(self, config_path: str = "config/cameras.json"):
        self.config_path = config_path
        self.cameras: Dict[str, CameraSource] = {}
        self._load_config()

    @property
    def active_streams(self) -> Dict[str, CameraSource]:
        """Exposes active camera streams for main.py processing loop routines."""
        return {
            cam_id: cam
            for cam_id, cam in self.cameras.items()
            if cam.cap is not None and cam.cap.isOpened()
        }

    @property
    def streams(self) -> Dict[str, CameraSource]:
        """Backward compatibility alias for main.py fallback lookups."""
        return self.active_streams

    def _load_config(self):
        """Loads registered camera metadata from config/cameras.json."""
        if not os.path.exists(self.config_path):
            logger.warning(f"Config file missing at {self.config_path}. Initializing empty camera registry.")
            return

        try:
            with open(self.config_path, "r") as f:
                data = json.load(f)
                for cam in data.get("cameras", []):
                    if cam.get("enabled", True):
                        source = CameraSource(
                            camera_id=cam["id"],
                            name=cam.get("name", cam["id"]),
                            source_type=cam.get("source_type", "video"),
                            source=cam["source"],
                        )
                        self.cameras[cam["id"]] = source
        except Exception as e:
            logger.error(f"Failed to load camera configuration from {self.config_path}: {e}")

    def initialize_all(self):
        """Initializes capture connections across all configured cameras."""
        for cam_id, cam in self.cameras.items():
            cam.connect()

    def get_stream(self, camera_id: str) -> Optional[CameraSource]:
        """Retrieves or reconnects a specific camera stream handle."""
        cam = self.cameras.get(camera_id)
        if cam and (cam.cap is None or not cam.cap.isOpened()):
            cam.connect()
        return cam

    def reload(self):
        """Reloads configuration and updates active capture streams dynamically."""
        self.release_all()
        self._load_config()
        self.initialize_all()

    def release_all(self):
        """Releases all active stream handles and clears camera registry."""
        for cam in self.cameras.values():
            cam.release()
        self.cameras.clear()