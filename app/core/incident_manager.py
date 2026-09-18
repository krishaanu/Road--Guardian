import os
import uuid
import sqlite3
import cv2
from datetime import datetime
from typing import List, Dict, Any, Optional


class IncidentManager:
    """
    Manages the lifecycle, persistence, and broadcasting state 
    for high-confidence traffic alerts and collision incidents.
    """
    def __init__(self, db_path: str = "data/roadguardian.db", storage_dir: str = "static/incidents"):
        self.db_path = db_path
        self.storage_dir = storage_dir
        
        # Ensure image output directory exists
        os.makedirs(self.storage_dir, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initializes the incident events table if it doesn't exist."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS incident_events (
                incident_id TEXT PRIMARY KEY,
                camera_id TEXT,
                vehicle_ids TEXT,
                type TEXT,
                confidence REAL,
                image_path TEXT,
                timestamp TIMESTAMP,
                status TEXT
            )
        """)
        conn.commit()
        conn.close()

    def create_incident(
        self, 
        camera_id: str, 
        vehicle_ids: List[str], 
        incident_type: str, 
        confidence: float, 
        frame_crop: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Generates a unique IncidentEvent, saves a full-res freeze frame, 
        and writes the entry to SQLite.
        """
        incident_id = f"INC_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
        image_path = os.path.join(self.storage_dir, f"{incident_id}.jpg")
        
        # Save freeze-frame if frame image provided
        if frame_crop is not None:
            try:
                cv2.imwrite(image_path, frame_crop)
            except Exception as e:
                print(f"[IncidentManager Warning]: Could not save incident image: {e}")

        vehicle_str = ",".join(vehicle_ids) if isinstance(vehicle_ids, list) else str(vehicle_ids)

        # Insert record into SQLite DB
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO incident_events (incident_id, camera_id, vehicle_ids, type, confidence, image_path, timestamp, status)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'), 'ACTIVE')
        """, (incident_id, camera_id, vehicle_str, incident_type, confidence, image_path))
        conn.commit()
        conn.close()

        # Format URL path safely without backslashes inside f-string
        formatted_path = image_path.replace("\\", "/")

        return {
            "incident_id": incident_id,
            "camera_id": camera_id,
            "vehicle_ids": vehicle_ids,
            "type": incident_type,
            "confidence": confidence,
            "image_url": f"/{formatted_path}",
            "status": "ACTIVE",
            "timestamp": datetime.now().isoformat()
        }


def create_and_log_incident(
    camera_id: str, 
    vehicle_ids: List[str], 
    incident_type: str, 
    confidence: float, 
    frame_crop: Optional[Any] = None, 
    db_path: str = "data/roadguardian.db"
) -> Dict[str, Any]:
    """
    Functional helper to create and persist an incident event without 
    manually instantiating the class.
    """
    manager = IncidentManager(db_path=db_path)
    return manager.create_incident(
        camera_id=camera_id,
        vehicle_ids=vehicle_ids,
        incident_type=incident_type,
        confidence=confidence,
        frame_crop=frame_crop
    )
