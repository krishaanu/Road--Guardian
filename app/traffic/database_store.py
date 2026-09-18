"""
Persistent SQLite & In-Memory Store for RoadGuardian Traffic Topology, Signal States, and Links.

Manages:
- Camera topology registration and metadata
- Mutually exclusive camera pair links (CameraPairLink with MUTEX_SIGNAL)
- Signal states per camera and intersection group
- Chronological audit logs
"""

import os
import sqlite3
import json
import time
from typing import Dict, List, Optional, Any, Literal
from pydantic import BaseModel, Field

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/roadguardian.db"))


class CameraPairLink(BaseModel):
    """
    Models an intersection relationship between two conflicting camera directional flows.
    """
    link_id: str = Field(..., example="LINK_NORTH_WEST_INTERSECTION")
    primary_cam_id: str = Field(..., example="CAMERA_01")
    secondary_cam_id: str = Field(..., example="CAMERA_02")
    relationship: Literal["MUTEX_SIGNAL"] = "MUTEX_SIGNAL"
    yellow_clearance_sec: float = Field(3.0, ge=1.0, le=10.0, description="Duration in seconds of yellow clearance before granting green to opposing path")
    enabled: bool = True
    created_at: float = Field(default_factory=time.time)


class CameraMetadata(BaseModel):
    """
    Camera topology record.
    """
    id: str
    name: str
    location_name: str
    source: str
    source_type: str = "video"
    gps: List[float] = Field(default_factory=lambda: [25.2914, 79.8713])
    direction_covered: str = "Northbound"
    enabled: bool = True
    created_at: float = Field(default_factory=time.time)


class TrafficStore:
    """
    Dual-layer store: In-memory cache for ultra-fast frame-loop reads,
    backed by SQLite for durability across server restarts.
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

        # In-memory caches
        self.cameras: Dict[str, CameraMetadata] = {}
        self.pair_links: Dict[str, CameraPairLink] = {}
        self.signal_states: Dict[str, Dict[str, Any]] = {}

        self._load_from_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Camera topology table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS camera_topology (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    location_name TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_type TEXT DEFAULT 'video',
                    gps TEXT DEFAULT '[25.2914, 79.8713]',
                    direction_covered TEXT DEFAULT 'Northbound',
                    enabled INTEGER DEFAULT 1,
                    created_at REAL
                )
            """)

            # Camera pair links table for MUTEX intersection logic
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS camera_pair_links (
                    link_id TEXT PRIMARY KEY,
                    primary_cam_id TEXT NOT NULL,
                    secondary_cam_id TEXT NOT NULL,
                    relationship TEXT DEFAULT 'MUTEX_SIGNAL',
                    yellow_clearance_sec REAL DEFAULT 3.0,
                    enabled INTEGER DEFAULT 1,
                    created_at REAL
                )
            """)

            # Audit logs table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS intersection_audit_logs (
                    audit_id TEXT PRIMARY KEY,
                    timestamp REAL NOT NULL,
                    event_type TEXT NOT NULL,
                    camera_id TEXT,
                    details TEXT
                )
            """)
            conn.commit()

    def _load_from_db(self):
        """Loads cached data from SQLite into fast in-memory structures."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM camera_topology")
            for row in cursor.fetchall():
                gps = json.loads(row["gps"]) if row["gps"] else [25.2914, 79.8713]
                cam = CameraMetadata(
                    id=row["id"],
                    name=row["name"],
                    location_name=row["location_name"],
                    source=row["source"],
                    source_type=row["source_type"],
                    gps=gps,
                    direction_covered=row["direction_covered"],
                    enabled=bool(row["enabled"]),
                    created_at=row["created_at"] or time.time(),
                )
                self.cameras[cam.id] = cam
                if cam.id not in self.signal_states:
                    self.signal_states[cam.id] = {
                        "camera_id": cam.id,
                        "current_state": "GREEN",
                        "countdown": 30,
                        "yellow_active": False,
                        "yellow_expires_at": 0.0,
                        "pending_state": None,
                    }

            cursor.execute("SELECT * FROM camera_pair_links")
            for row in cursor.fetchall():
                link = CameraPairLink(
                    link_id=row["link_id"],
                    primary_cam_id=row["primary_cam_id"],
                    secondary_cam_id=row["secondary_cam_id"],
                    relationship=row["relationship"],
                    yellow_clearance_sec=float(row["yellow_clearance_sec"]),
                    enabled=bool(row["enabled"]),
                    created_at=float(row["created_at"]),
                )
                self.pair_links[link.link_id] = link

        # Seed default intersection pair if empty and default cameras exist
        if not self.pair_links and "CAMERA_01" in self.cameras and "CAMERA_02" in self.cameras:
            self.save_pair_link(CameraPairLink(
                link_id="LINK_CAM01_CAM02_MUTEX",
                primary_cam_id="CAMERA_01",
                secondary_cam_id="CAMERA_02",
                relationship="MUTEX_SIGNAL",
                yellow_clearance_sec=3.0,
                enabled=True,
            ))

    def save_camera(self, cam: CameraMetadata):
        """Persists or updates camera topology."""
        self.cameras[cam.id] = cam
        if cam.id not in self.signal_states:
            self.signal_states[cam.id] = {
                "camera_id": cam.id,
                "current_state": "GREEN",
                "countdown": 30,
                "yellow_active": False,
                "yellow_expires_at": 0.0,
                "pending_state": None,
            }

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO camera_topology (id, name, location_name, source, source_type, gps, direction_covered, enabled, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    location_name=excluded.location_name,
                    source=excluded.source,
                    source_type=excluded.source_type,
                    gps=excluded.gps,
                    direction_covered=excluded.direction_covered,
                    enabled=excluded.enabled
            """, (
                cam.id,
                cam.name,
                cam.location_name,
                cam.source,
                cam.source_type,
                json.dumps(cam.gps),
                cam.direction_covered,
                1 if cam.enabled else 0,
                cam.created_at,
            ))
            conn.commit()

    def delete_camera(self, camera_id: str) -> Dict[str, Any]:
        """
        Gracefully deletes a camera:
        1. Removes from camera_topology.
        2. Decouples and removes any active CameraPairLinks where it was primary or secondary.
        3. Cleans in-memory states and logs the audit event.
        """
        decoupled_links = []
        # Find all pair links referencing this camera
        to_delete_links = [
            lid for lid, link in self.pair_links.items()
            if link.primary_cam_id == camera_id or link.secondary_cam_id == camera_id
        ]
        for lid in to_delete_links:
            decoupled_links.append(self.pair_links[lid].model_dump())
            del self.pair_links[lid]

        # Remove from in-memory
        removed_cam = self.cameras.pop(camera_id, None)
        self.signal_states.pop(camera_id, None)

        # Remove from database
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM camera_topology WHERE id = ?", (camera_id,))
            for lid in to_delete_links:
                cursor.execute("DELETE FROM camera_pair_links WHERE link_id = ?", (lid,))
            conn.commit()

        self.log_audit(
            event_type="CAMERA_DELETED_AND_LINKS_DECOUPLED",
            camera_id=camera_id,
            details={
                "deleted_camera": removed_cam.model_dump() if removed_cam else None,
                "decoupled_links": decoupled_links,
            }
        )

        return {
            "camera_id": camera_id,
            "decoupled_links_count": len(decoupled_links),
            "decoupled_links": decoupled_links,
        }

    def save_pair_link(self, link: CameraPairLink):
        """Saves a CameraPairLink."""
        self.pair_links[link.link_id] = link
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO camera_pair_links (link_id, primary_cam_id, secondary_cam_id, relationship, yellow_clearance_sec, enabled, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(link_id) DO UPDATE SET
                    primary_cam_id=excluded.primary_cam_id,
                    secondary_cam_id=excluded.secondary_cam_id,
                    relationship=excluded.relationship,
                    yellow_clearance_sec=excluded.yellow_clearance_sec,
                    enabled=excluded.enabled
            """, (
                link.link_id,
                link.primary_cam_id,
                link.secondary_cam_id,
                link.relationship,
                link.yellow_clearance_sec,
                1 if link.enabled else 0,
                link.created_at,
            ))
            conn.commit()

    def delete_pair_link(self, link_id: str) -> bool:
        """Deletes a CameraPairLink."""
        if link_id in self.pair_links:
            del self.pair_links[link_id]
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM camera_pair_links WHERE link_id = ?", (link_id,))
                conn.commit()
            return True
        return False

    def get_links_for_camera(self, camera_id: str) -> List[CameraPairLink]:
        """Returns all pair links involving the specified camera."""
        return [
            link for link in self.pair_links.values()
            if link.enabled and (link.primary_cam_id == camera_id or link.secondary_cam_id == camera_id)
        ]

    def log_audit(self, event_type: str, camera_id: Optional[str], details: Dict[str, Any]):
        """Records an audit log entry into SQLite."""
        audit_id = f"AUDIT_{int(time.time() * 1000)}"
        ts = time.time()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO intersection_audit_logs (audit_id, timestamp, event_type, camera_id, details)
                VALUES (?, ?, ?, ?, ?)
            """, (audit_id, ts, event_type, camera_id, json.dumps(details)))
            conn.commit()

    def get_audit_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieves latest audit logs."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM intersection_audit_logs ORDER BY timestamp DESC LIMIT ?", (limit,))
            results = []
            for row in cursor.fetchall():
                results.append({
                    "audit_id": row["audit_id"],
                    "timestamp": row["timestamp"],
                    "event_type": row["event_type"],
                    "camera_id": row["camera_id"],
                    "details": json.loads(row["details"]) if row["details"] else {},
                })
            return results


traffic_store = TrafficStore()
