import math
import uuid
import sqlite3
import numpy as np
from datetime import datetime

# ==========================================
# 1. RE-ID COSINE SIMILARITY NORMALIZATION
# ==========================================

def compute_cosine_similarity(emb_a: np.ndarray, emb_b: np.ndarray) -> float:
    """Calculates L2-normalized cosine similarity between feature vectors."""
    norm_a = np.linalg.norm(emb_a)
    norm_b = np.linalg.norm(emb_b)
    
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
        
    return float(np.dot(emb_a, emb_b) / (norm_a * norm_b))


# ==========================================
# 2. TRAJECTORY & PROBABILITY LOGIC
# ==========================================

def _latest_velocity(trajectory):
    vx, vy = trajectory.velocity_vector()
    return vx, vy


def collision_probability(trajectory_a, trajectory_b):
    """
    Estimates whether two tracked vehicles are moving toward the same area.
    This is a predictive warning score, NOT proof of a crash.
    """
    a = trajectory_a.latest()
    b = trajectory_b.latest()

    if a is None or b is None:
        return 0.0

    ax, ay = a["x"], a["y"]
    bx, by = b["x"], b["y"]

    distance = math.sqrt((bx - ax) ** 2 + (by - ay) ** 2)

    if distance <= 0:
        return 1.0

    avx, avy = _latest_velocity(trajectory_a)
    bvx, bvy = _latest_velocity(trajectory_b)

    # Relative position
    rx = bx - ax
    ry = by - ay

    # Relative velocity
    rvx = bvx - avx
    rvy = bvy - avy

    relative_speed_sq = rvx ** 2 + rvy ** 2

    if relative_speed_sq < 1e-6:
        return 0.0

    # Time of closest approach
    t = -(rx * rvx + ry * rvy) / relative_speed_sq

    # Ignore vehicles moving away or too far in future
    if t < 0 or t > 3.0:
        return 0.0

    closest_x = rx + rvx * t
    closest_y = ry + rvy * t
    closest_distance = math.sqrt(closest_x ** 2 + closest_y ** 2)

    danger_distance = 80.0

    distance_score = max(0.0, 1.0 - closest_distance / danger_distance)
    time_score = max(0.0, 1.0 - t / 3.0)

    probability = (distance_score * 0.65) + (time_score * 0.35)

    return max(0.0, min(1.0, probability))


# ==========================================
# 3. WINDOW-BASED COLLISION CONFIRMATION
# ==========================================

def evaluate_impact_event(track_a, track_b, iou_val, edge_ratio=0.0):
    """
    Combines spatial overlap, predictive probability, trajectory convergence,
    and a 10-frame window deceleration check to confirm real collisions.
    """
    if iou_val < 0.15:
        return False, 0.0

    # 1. Evaluate Predictive Probability
    prob = collision_probability(track_a.trajectory, track_b.trajectory)

    # 2. Window-Based Deceleration Check (10-frame history window)
    speed_hist_a = track_a.speed_history[-10:] if hasattr(track_a, 'speed_history') else [0]
    speed_hist_b = track_b.speed_history[-10:] if hasattr(track_b, 'speed_history') else [0]
    
    drop_a = (max(speed_hist_a) - speed_hist_a[-1]) if speed_hist_a else 0
    drop_b = (max(speed_hist_b) - speed_hist_b[-1]) if speed_hist_b else 0
    
    significant_decel = (drop_a >= 22.0) or (drop_b >= 22.0)

    # 3. Heading Convergence Check
    avx, avy = _latest_velocity(track_a.trajectory)
    bvx, bvy = _latest_velocity(track_b.trajectory)
    is_converging = (avx * bvx + avy * bvy) < 0

    # Confirmation Gate
    if (significant_decel or prob > 0.70) and is_converging:
        confidence = 0.75 + (prob * 0.10)
        
        # Edge ratio density boost (auxiliary signal only)
        if edge_ratio > 0.28:
            confidence += 0.15

        return True, float(np.clip(confidence, 0.0, 1.0))

    return False, 0.0


# ==========================================
# 4. DURABLE INCIDENT DISPATCHER
# ==========================================

def create_and_log_incident(camera_id, vehicle_ids, incident_type, confidence, frame_crop=None, db_path="traffic_data.db"):
    """
    Generates a durable IncidentEvent, persists record to SQLite,
    and returns a structured dictionary for WebSocket broadcasting.
    """
    incident_id = f"INC_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
    image_path = f"static/incidents/{incident_id}.jpg"
    
    # Save freeze-frame if image provided
    if frame_crop is not None:
        import cv2
        cv2.imwrite(image_path, frame_crop)

    # Insert incident into SQLite DB
    conn = sqlite3.connect(db_path)
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
    cursor.execute("""
        INSERT INTO incident_events (incident_id, camera_id, vehicle_ids, type, confidence, image_path, timestamp, status)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'), 'ACTIVE')
    """, (incident_id, camera_id, ",".join(vehicle_ids), incident_type, confidence, image_path))
    conn.commit()
    conn.close()

    return {
        "incident_id": incident_id,
        "camera_id": camera_id,
        "vehicle_ids": vehicle_ids,
        "type": incident_type,
        "confidence": confidence,
        "image_url": f"/{image_path}",
        "status": "ACTIVE",
        "timestamp": datetime.now().isoformat()
    }