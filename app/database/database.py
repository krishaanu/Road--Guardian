import sqlite3
import os
import logging
from datetime import datetime

logger = logging.getLogger("DatabaseManager")


class DatabaseManager:
    """Manages SQLite database connections and traffic logging for RoadGuardian."""

    def __init__(self, db_path: str = "data/roadguardian.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._initialize_schema()
        logger.info(f"Database initialized at {self.db_path}")

    def get_connection(self) -> sqlite3.Connection:
        """Returns a raw SQLite connection handle configured to return dictionary-style rows."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize_schema(self):
        """Creates the necessary database tables if they do not exist."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # Table for tracking unique vehicles with color and subtype support
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS vehicle_registry (
                    global_vehicle_id TEXT PRIMARY KEY,
                    plate_text TEXT UNIQUE,
                    vehicle_type TEXT,
                    vehicle_subtype TEXT,
                    color TEXT,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen TIMESTAMP
                )
            """)

            # Table for frame-by-frame observations
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    global_vehicle_id TEXT,
                    camera_id TEXT,
                    plate_text TEXT,
                    vehicle_type TEXT,
                    vehicle_subtype TEXT,
                    color TEXT,
                    confidence REAL,
                    FOREIGN KEY(global_vehicle_id) REFERENCES vehicle_registry(global_vehicle_id)
                )
            """)

            # Table for security alerts (Overspeeding, Collisions, Drift, Stops)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    camera_id TEXT,
                    alert_type TEXT,
                    severity REAL
                )
            """)

            # Table for deduplicated vehicle inventory (Smart India Hackathon specification)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS vehicle_inventory (
                    gvid TEXT PRIMARY KEY,
                    camera_id TEXT,
                    license_plate TEXT,
                    category TEXT,
                    color TEXT,
                    speed REAL,
                    view_angle TEXT,
                    last_seen TIMESTAMP
                )
            """)

            # Table for confirmed infractions (Passed all 4 confidence gates)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS infractions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    camera_id TEXT,
                    offense_type TEXT,
                    license_plate TEXT,
                    confidence_scores TEXT,
                    image_crop_path TEXT,
                    status TEXT DEFAULT 'CONFIRMED'
                )
            """)

            # Table for confidence-gated Needs Review queue (Failed 1+ gates)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS needs_review_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    camera_id TEXT,
                    offense_type TEXT,
                    gate_scores TEXT,
                    license_plate TEXT,
                    image_crop_path TEXT,
                    status TEXT DEFAULT 'PENDING_REVIEW',
                    reviewer_notes TEXT
                )
            """)

            # Table for emergency preemption audit logs
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS preemption_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    camera_id TEXT,
                    junction_id TEXT,
                    time_saved_sec REAL,
                    vehicle_type TEXT DEFAULT 'AMBULANCE'
                )
            """)

            conn.commit()

    def register_or_update_vehicle(
        self,
        global_vehicle_id: str,
        plate_text: str = None,
        vehicle_type: str = "vehicle",
        vehicle_subtype: str = "Unknown",
        color: str = "UNKNOWN",
    ):
        """Inserts a new vehicle or updates an existing record based on Global ID or License Plate.
        Handles primary key collisions and plate_text UNIQUE constraints gracefully without throwing sqlite3.IntegrityError.
        """
        clean_plate = plate_text.strip() if plate_text and plate_text.strip() else None

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            if not clean_plate:
                try:
                    cursor.execute(
                        """
                        INSERT INTO vehicle_registry (global_vehicle_id, vehicle_type, vehicle_subtype, color, last_seen)
                        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(global_vehicle_id) DO UPDATE SET
                            vehicle_type = coalesce(excluded.vehicle_type, vehicle_registry.vehicle_type),
                            vehicle_subtype = coalesce(excluded.vehicle_subtype, vehicle_registry.vehicle_subtype),
                            color = coalesce(excluded.color, vehicle_registry.color),
                            last_seen = CURRENT_TIMESTAMP
                        """,
                        (global_vehicle_id, vehicle_type, vehicle_subtype, color),
                    )
                except sqlite3.IntegrityError as e:
                    logger.warning(f"Integrity warning on vehicle {global_vehicle_id}: {e}")
                    cursor.execute(
                        """
                        UPDATE vehicle_registry
                        SET vehicle_type = coalesce(?, vehicle_type),
                            vehicle_subtype = coalesce(?, vehicle_subtype),
                            color = coalesce(?, color),
                            last_seen = CURRENT_TIMESTAMP
                        WHERE global_vehicle_id = ?
                        """,
                        (vehicle_type, vehicle_subtype, color, global_vehicle_id),
                    )
            else:
                try:
                    cursor.execute(
                        """
                        INSERT INTO vehicle_registry (global_vehicle_id, plate_text, vehicle_type, vehicle_subtype, color, last_seen)
                        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(global_vehicle_id) DO UPDATE SET
                            plate_text = coalesce(excluded.plate_text, vehicle_registry.plate_text),
                            vehicle_type = coalesce(excluded.vehicle_type, vehicle_registry.vehicle_type),
                            vehicle_subtype = coalesce(excluded.vehicle_subtype, vehicle_registry.vehicle_subtype),
                            color = coalesce(excluded.color, vehicle_registry.color),
                            last_seen = CURRENT_TIMESTAMP
                        """,
                        (global_vehicle_id, clean_plate, vehicle_type, vehicle_subtype, color),
                    )
                except sqlite3.IntegrityError:
                    # Gracefully handle collision where clean_plate already exists under another global_vehicle_id
                    cursor.execute(
                        """
                        UPDATE vehicle_registry
                        SET vehicle_type = coalesce(?, vehicle_type),
                            vehicle_subtype = coalesce(?, vehicle_subtype),
                            color = coalesce(?, color),
                            last_seen = CURRENT_TIMESTAMP
                        WHERE plate_text = ?
                        """,
                        (vehicle_type, vehicle_subtype, color, clean_plate),
                    )

            conn.commit()

    def log_observation(
        self,
        global_vehicle_id: str,
        camera_id: str,
        plate_text: str = None,
        vehicle_type: str = "vehicle",
        vehicle_subtype: str = "Unknown",
        color: str = "UNKNOWN",
        confidence: float = 0.0,
    ):
        """Logs a single vehicle detection frame observation."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO observations (global_vehicle_id, camera_id, plate_text, vehicle_type, vehicle_subtype, color, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (global_vehicle_id, camera_id, plate_text, vehicle_type, vehicle_subtype, color, confidence),
            )
            conn.commit()

    def log_alert(self, camera_id: str, alert_type: str, severity: float):
        """Logs a security event alert (e.g., Overspeeding, Stop, Collision)."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO alerts (camera_id, alert_type, severity)
                VALUES (?, ?, ?)
            """,
                (camera_id, alert_type, severity),
            )
            conn.commit()

    def upsert_vehicle(
        self,
        gvid: str,
        camera_id: str,
        license_plate: str = "UNREAD",
        category: str = "Car",
        color: str = "Silver",
        speed: float = 0.0,
        view_angle: str = "Front",
    ):
        """Deduplicated UPSERT pattern for vehicle inventory.
        Updates state on conflict, preserving unique GVID across frames.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO vehicle_inventory (gvid, camera_id, license_plate, category, color, speed, view_angle, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(gvid) DO UPDATE SET
                    camera_id=excluded.camera_id,
                    license_plate=excluded.license_plate,
                    category=excluded.category,
                    color=excluded.color,
                    speed=excluded.speed,
                    view_angle=excluded.view_angle,
                    last_seen=datetime('now');
                """,
                (gvid, camera_id, license_plate, category, color, speed, view_angle),
            )
            conn.commit()

    def get_vehicle_inventory(
        self,
        camera_id: str = None,
        search: str = None,
        vehicle_type: str = None,
        color: str = None,
        limit: int = 50,
    ) -> list:
        """Retrieves deduplicated vehicle records from vehicle_inventory."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            conditions = []
            params = []

            if camera_id:
                conditions.append("camera_id = ?")
                params.append(camera_id)
            if vehicle_type and vehicle_type.strip():
                conditions.append("LOWER(category) = ?")
                params.append(vehicle_type.strip().lower())
            if color and color.strip():
                conditions.append("LOWER(color) = ?")
                params.append(color.strip().lower())
            if search:
                conditions.append("(gvid LIKE ? OR license_plate LIKE ?)")
                params.append(f"%{search}%")
                params.append(f"%{search}%")

            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            query = f"""
                SELECT gvid, camera_id, license_plate, category, color, speed, view_angle, last_seen
                FROM vehicle_inventory
                {where_clause}
                ORDER BY last_seen DESC
                LIMIT ?
            """
            params.append(limit)
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def log_infraction(
        self,
        camera_id: str,
        offense_type: str,
        license_plate: str = "UNREAD",
        confidence_scores: str = "{}",
        image_crop_path: str = None,
        status: str = "CONFIRMED",
    ) -> int:
        """Logs a confirmed infraction that passed all confidence gates."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO infractions (camera_id, offense_type, license_plate, confidence_scores, image_crop_path, status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (camera_id, offense_type, license_plate, confidence_scores, image_crop_path, status),
            )
            conn.commit()
            return cursor.lastrowid

    def get_infractions(self, camera_id: str = None, limit: int = 50) -> list:
        """Retrieves recent confirmed infractions."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if camera_id:
                cursor.execute(
                    "SELECT * FROM infractions WHERE camera_id = ? ORDER BY timestamp DESC LIMIT ?",
                    (camera_id, limit),
                )
            else:
                cursor.execute("SELECT * FROM infractions ORDER BY timestamp DESC LIMIT ?", (limit,))
            return [dict(r) for r in cursor.fetchall()]

    def log_needs_review(
        self,
        camera_id: str,
        offense_type: str,
        gate_scores: str = "{}",
        license_plate: str = "UNREAD",
        image_crop_path: str = None,
        status: str = "PENDING_REVIEW",
    ) -> int:
        """Logs a candidate offense that failed one or more confidence gates to the human review queue."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO needs_review_queue (camera_id, offense_type, gate_scores, license_plate, image_crop_path, status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (camera_id, offense_type, gate_scores, license_plate, image_crop_path, status),
            )
            conn.commit()
            return cursor.lastrowid

    def get_needs_review(self, camera_id: str = None, status: str = "PENDING_REVIEW", limit: int = 50) -> list:
        """Retrieves offenses requiring human verification."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if camera_id and status:
                cursor.execute(
                    "SELECT * FROM needs_review_queue WHERE camera_id = ? AND status = ? ORDER BY timestamp DESC LIMIT ?",
                    (camera_id, status, limit),
                )
            elif status:
                cursor.execute(
                    "SELECT * FROM needs_review_queue WHERE status = ? ORDER BY timestamp DESC LIMIT ?",
                    (status, limit),
                )
            elif camera_id:
                cursor.execute(
                    "SELECT * FROM needs_review_queue WHERE camera_id = ? ORDER BY timestamp DESC LIMIT ?",
                    (camera_id, limit),
                )
            else:
                cursor.execute("SELECT * FROM needs_review_queue ORDER BY timestamp DESC LIMIT ?", (limit,))
            return [dict(r) for r in cursor.fetchall()]

    def update_review_status(self, review_id: int, status: str, reviewer_notes: str = None) -> bool:
        """Updates review status to APPROVED or DISMISSED."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE needs_review_queue
                SET status = ?, reviewer_notes = coalesce(?, reviewer_notes)
                WHERE id = ?
                """,
                (status, reviewer_notes, review_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def log_preemption(
        self,
        camera_id: str,
        junction_id: str,
        time_saved_sec: float,
        vehicle_type: str = "AMBULANCE",
    ) -> int:
        """Logs an emergency preemption event for audit purposes."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO preemption_logs (camera_id, junction_id, time_saved_sec, vehicle_type)
                VALUES (?, ?, ?, ?)
                """,
                (camera_id, junction_id, time_saved_sec, vehicle_type),
            )
            conn.commit()
            return cursor.lastrowid

    def get_preemption_logs(self, junction_id: str = None, limit: int = 50) -> list:
        """Retrieves emergency preemption audit records."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if junction_id:
                cursor.execute(
                    "SELECT * FROM preemption_logs WHERE junction_id = ? ORDER BY timestamp DESC LIMIT ?",
                    (junction_id, limit),
                )
            else:
                cursor.execute("SELECT * FROM preemption_logs ORDER BY timestamp DESC LIMIT ?", (limit,))
            return [dict(r) for r in cursor.fetchall()]

    def acknowledge_incident(self, incident_id: str) -> bool:
        """Updates incident status to ACKNOWLEDGED in SQLite database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS incidents (
                    id TEXT PRIMARY KEY,
                    camera_id TEXT,
                    event_type TEXT,
                    status TEXT DEFAULT 'PENDING',
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    acknowledged BOOLEAN DEFAULT 0
                )
            """)
            try:
                cursor.execute("ALTER TABLE alerts ADD COLUMN status TEXT DEFAULT 'PENDING'")
            except Exception:
                pass

            # Update alerts table if matched
            clean_id = str(incident_id).replace("HIST_", "").replace("SIM_", "")
            if clean_id.isdigit():
                cursor.execute("UPDATE alerts SET status = 'ACKNOWLEDGED' WHERE id = ?", (int(clean_id),))
            cursor.execute("UPDATE alerts SET status = 'ACKNOWLEDGED' WHERE camera_id = ?", (str(incident_id),))

            # Upsert into incidents table
            cursor.execute("""
                INSERT INTO incidents (id, camera_id, event_type, status, acknowledged)
                VALUES (?, 'CAMERA_01', 'CONFIRMED_INCIDENT', 'ACKNOWLEDGED', 1)
                ON CONFLICT(id) DO UPDATE SET status = 'ACKNOWLEDGED', acknowledged = 1
            """, (str(incident_id),))
            conn.commit()
            logger.info(f"Incident {incident_id} marked as ACKNOWLEDGED in database.")
            return True