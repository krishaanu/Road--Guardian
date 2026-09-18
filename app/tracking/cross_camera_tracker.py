import uuid
import time
import numpy as np
from typing import Dict, Any, Optional, Tuple


class GlobalVehicleRecord:
    """Unified vehicle entity across multi-camera setups."""

    def __init__(self, global_id: str, plate: Optional[str], vehicle_type: str, color: str):
        self.global_id = global_id
        self.plate = plate or "UNREADABLE"
        self.vehicle_type = vehicle_type
        self.color = color
        self.observations = []
        self.embedding: Optional[np.ndarray] = None

    def add_observation(self, camera_id: str, confidence: float, metadata: Optional[Dict[str, Any]] = None):
        self.observations.append({
            "camera_id": camera_id,
            "timestamp": time.time(),
            "confidence": confidence,
            "metadata": metadata or {}
        })


class CrossCameraTracker:
    """Maps local camera stream track IDs to unified Global Vehicle IDs (GVIDs)

    using plate recognition and 512-d Re-ID embeddings.
    """

    def __init__(self, reid_similarity_threshold: float = 0.75):
        self.reid_similarity_threshold = reid_similarity_threshold
        self.plate_to_global_id: Dict[str, str] = {}
        self.local_to_global: Dict[Tuple[str, int], str] = {}
        self.records: Dict[str, GlobalVehicleRecord] = {}

    def resolve_identity(
        self,
        camera_id: str,
        local_track_id: int,
        plate_text: Optional[str] = None,
        embedding: Optional[np.ndarray] = None,
        vehicle_type: str = "vehicle",
        color: str = "unknown",
        confidence: float = 1.0,
    ) -> Tuple[str, bool]:
        """Resolves identity across cameras using License Plate, Re-ID embedding, or local track persistence.

        Returns:
            Tuple[str, bool]: (global_vehicle_id, is_new_identity)
        """
        local_key = (camera_id, local_track_id)

        # 1. Check existing local track assignment
        if local_key in self.local_to_global:
            gid = self.local_to_global[local_key]
            record = self.records[gid]
            if plate_text and len(plate_text) >= 4 and record.plate == "UNREADABLE":
                record.plate = plate_text
                self.plate_to_global_id[plate_text] = gid
            if embedding is not None and record.embedding is None:
                record.embedding = embedding
            record.add_observation(camera_id, confidence)
            return gid, False

        # 2. Match by License Plate Text
        if plate_text and len(plate_text) >= 4:
            if plate_text in self.plate_to_global_id:
                gid = self.plate_to_global_id[plate_text]
                record = self.records[gid]
                record.add_observation(camera_id, confidence)
                if embedding is not None and record.embedding is None:
                    record.embedding = embedding
                self.local_to_global[local_key] = gid
                return gid, False

        # 3. Match by Cosine Similarity on 512-d Re-ID Embedding Vector
        if embedding is not None and np.linalg.norm(embedding) > 0:
            best_match_gid = None
            highest_sim = -1.0

            for gid, record in self.records.items():
                if record.embedding is not None and np.linalg.norm(record.embedding) > 0:
                    sim = float(np.dot(embedding, record.embedding))
                    if sim > highest_sim:
                        highest_sim = sim
                        best_match_gid = gid

            if best_match_gid is not None and highest_sim >= self.reid_similarity_threshold:
                self.local_to_global[local_key] = best_match_gid
                record = self.records[best_match_gid]
                record.add_observation(camera_id, confidence)
                return best_match_gid, False

        # 4. Create new Global Record
        if plate_text and len(plate_text) >= 4:
            gid = f"GV-{uuid.uuid4().hex[:6].upper()}"
            self.plate_to_global_id[plate_text] = gid
        else:
            gid = f"LOCAL-{camera_id}-{local_track_id}"

        record = GlobalVehicleRecord(gid, plate_text, vehicle_type, color)
        if embedding is not None:
            record.embedding = embedding
        record.add_observation(camera_id, confidence)

        self.records[gid] = record
        self.local_to_global[local_key] = gid
        return gid, True

    def match_or_register(
        self,
        camera_id: str,
        track_id: int,
        plate_text: Optional[str] = None,
        embedding: Optional[np.ndarray] = None,
        vehicle_type: str = "vehicle",
    ) -> Tuple[str, bool]:
        """Wrapper to ensure backward compatibility with main loop execution."""
        return self.resolve_identity(
            camera_id=camera_id,
            local_track_id=track_id,
            plate_text=plate_text,
            embedding=embedding,
            vehicle_type=vehicle_type,
        )