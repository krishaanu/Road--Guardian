import math
import time
import numpy as np
from typing import Dict, List, Tuple, Optional
from app.tracking.trajectory import TrajectoryManager


class VehicleTrajectoryManager:
    """Wraps underlying TrajectoryManager to log vehicle movement histories."""
    def __init__(self, history_size=100, timeout=3.0):
        self.manager = TrajectoryManager(
            history_size=history_size,
            timeout=timeout
        )

    def update(self, track_id, x, y, timestamp):
        return self.manager.update(track_id, x, y, timestamp)

    def all_tracks(self):
        return self.manager.all_tracks()

    def get(self, track_id):
        return self.manager.get(track_id)

    def cleanup(self):
        return self.manager.cleanup()


class TrackedVehicle:
    """Stores camera-scoped track state, Re-ID embeddings, and speed histories."""
    def __init__(self, camera_id: str, local_track_id: int, gvid: str, category: str, bbox: Tuple[int, int, int, int]):
        self.camera_id = camera_id
        self.local_track_id = local_track_id
        self.scoped_key = (camera_id, local_track_id)
        self.gvid = gvid
        self.category = category
        self.bbox = bbox  # (x1, y1, x2, y2)
        
        # Centroid
        self.centroid = (int((bbox[0] + bbox[2]) / 2), int((bbox[1] + bbox[3]) / 2))
        
        # State Tracking
        self.speed_kmh = 0.0
        self.speed_history: List[float] = []
        self.history: List[Tuple[int, int]] = []
        self.last_timestamp = time.time()
        self.embedding: Optional[np.ndarray] = None

    def update_position(self, bbox: Tuple[int, int, int, int], timestamp: float, meters_per_pixel: float = 0.05):
        """Updates position, calculates smoothed speed using dt, and appends speed history."""
        dt = timestamp - self.last_timestamp
        self.last_timestamp = timestamp
        
        new_centroid = (int((bbox[0] + bbox[2]) / 2), int((bbox[1] + bbox[3]) / 2))
        
        # Speed Calculation & EMA Smoothing (0.7 * prev + 0.3 * inst)
        if dt >= 0.033 and len(self.history) >= 4:
            dist_pixels = math.sqrt((new_centroid[0] - self.centroid[0])**2 + (new_centroid[1] - self.centroid[1])**2)
            dist_meters = dist_pixels * meters_per_pixel
            inst_speed = (dist_meters / dt) * 3.6
            if inst_speed <= 200.0:
                smoothed_speed = (0.7 * self.speed_kmh) + (0.3 * inst_speed) if self.speed_kmh > 0 else inst_speed
                self.speed_kmh = float(np.clip(smoothed_speed, 0.0, 200.0))
        
        # Update Centroids and Rolling Histories
        self.centroid = new_centroid
        self.bbox = bbox
        self.history.append(new_centroid)
        self.speed_history.append(self.speed_kmh)
        
        # Keep histories bounded
        if len(self.history) > 20:
            self.history.pop(0)
        if len(self.speed_history) > 20:
            self.speed_history.pop(0)


class CrossCameraTracker:
    """
    Manages global identities (GVIDs) across multiple cameras using composite key scoping 
    (camera_id, local_track_id) and L2-normalized cosine similarity embeddings.
    """
    def __init__(self, reid_threshold: float = 0.75):
        self.reid_threshold = reid_threshold
        self.local_to_global: Dict[Tuple[str, int], str] = {}
        self.global_gallery: Dict[str, np.ndarray] = {}  # gvid -> L2-normalized embedding
        self.trajectory_manager = VehicleTrajectoryManager()
        self.active_tracks: Dict[Tuple[str, int], TrackedVehicle] = {}
        self.gvid_counter = 100

    def compute_cosine_similarity(self, emb_a: np.ndarray, emb_b: np.ndarray) -> float:
        """Calculates true L2-normalized cosine similarity between two vectors."""
        norm_a = np.linalg.norm(emb_a)
        norm_b = np.linalg.norm(emb_b)
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return float(np.dot(emb_a, emb_b) / (norm_a * norm_b))

    def get_or_create_gvid(self, camera_id: str, local_track_id: int, embedding: Optional[np.ndarray] = None) -> str:
        """Resolves local camera track ID to a unified Global Vehicle ID (GVID)."""
        scoped_key = (camera_id, local_track_id)

        # Return existing mapping if present
        if scoped_key in self.local_to_global:
            return self.local_to_global[scoped_key]

        # Attempt Cross-Camera Re-ID Match
        if embedding is not None:
            best_gvid = None
            best_sim = 0.0
            
            for gvid, gallery_emb in self.global_gallery.items():
                sim = self.compute_cosine_similarity(embedding, gallery_emb)
                if sim > self.reid_threshold and sim > best_sim:
                    best_sim = sim
                    best_gvid = gvid
            
            if best_gvid:
                self.local_to_global[scoped_key] = best_gvid
                return best_gvid

        # Create unified deterministic GVID from camera_id and track_id
        new_gvid = f"GV_{camera_id}_{local_track_id}"
        self.local_to_global[scoped_key] = new_gvid
        
        if embedding is not None:
            norm = np.linalg.norm(embedding)
            self.global_gallery[new_gvid] = embedding / norm if norm > 0 else embedding

        return new_gvid

    def update_track(self, camera_id: str, local_track_id: int, bbox: Tuple[int, int, int, int], category: str, embedding: Optional[np.ndarray] = None) -> TrackedVehicle:
        """Updates or creates a camera-scoped tracked vehicle instance."""
        scoped_key = (camera_id, local_track_id)
        timestamp = time.time()
        
        gvid = self.get_or_create_gvid(camera_id, local_track_id, embedding)
        
        if scoped_key not in self.active_tracks:
            vehicle = TrackedVehicle(camera_id, local_track_id, gvid, category, bbox)
            self.active_tracks[scoped_key] = vehicle
        else:
            vehicle = self.active_tracks[scoped_key]
            
        vehicle.update_position(bbox, timestamp)
        
        # Update underlying trajectory manager
        self.trajectory_manager.update(f"{camera_id}_{local_track_id}", vehicle.centroid[0], vehicle.centroid[1], timestamp)
        
        return vehicle

    def cleanup_stale_tracks(self):
        """Cleans up inactive tracks and trajectory histories."""
        self.trajectory_manager.cleanup()