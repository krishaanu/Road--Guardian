import sqlite3
import time
from datetime import datetime

class LongTermTrafficAggregator:
    def __init__(self, db_path="data/roadguardian.db"):
        self.db_path = db_path
        self._init_analytics_db()

    def _init_analytics_db(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_traffic_summary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    location_id TEXT NOT NULL,
                    peak_vehicle_count INTEGER,
                    avg_speed_kmh REAL,
                    congestion_events INTEGER,
                    improvement_score REAL,
                    needs_improvement BOOLEAN
                )
            """)
            conn.commit()

    def log_hourly_summary(self, location_id, vehicle_count, avg_speed, congestion_count):
        today = datetime.now().strftime("%Y-%m-%d")
        # Needs improvement if average speed drops below 15 km/h during high density
        needs_improvement = avg_speed < 15.0 and vehicle_count > 10

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO daily_traffic_summary 
                (date, location_id, peak_vehicle_count, avg_speed_kmh, congestion_events, improvement_score, needs_improvement)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (today, location_id, vehicle_count, avg_speed, congestion_count, (100.0 - avg_speed), needs_improvement))
            conn.commit()

    def generate_weekly_report(self, location_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT AVG(peak_vehicle_count), AVG(avg_speed_kmh), SUM(congestion_events), SUM(needs_improvement)
                FROM daily_traffic_summary
                WHERE location_id = ? AND date >= date('now', '-7 days')
            """, (location_id,))
            row = cursor.fetchone()

            avg_vehicles = row[0] or 0
            avg_speed = row[1] or 0.0
            total_congestions = row[2] or 0
            improvement_days = row[3] or 0

            return {
                "location_id": location_id,
                "average_vehicles_per_day": round(avg_vehicles, 1),
                "weekly_avg_speed": round(avg_speed, 1),
                "congestion_incidents": total_congestions,
                "infrastructure_flag": improvement_days >= 3,
                "status_recommendation": "Road infrastructure change suggested (High Congestion)" if improvement_days >= 3 else "Traffic flow nominal"
            }
