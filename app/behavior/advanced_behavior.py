import cv2
import numpy as np
import math
import logging

logger = logging.getLogger("AdvancedBehaviorEngine")


class AdvancedBehaviorEngine:
    """
    Advanced Vehicle Dynamics and Behavior Engine for RoadGuardian.
    Handles speed estimation, drift analysis, lane switching, 
    vehicle classification (Cars, Motorcycles, Auto-Rickshaws, Subtypes),
    color profiling, and physical impact/shatter detection.
    """

    def __init__(
        self,
        pixels_per_meter: float = 15.0,
        speed_limit_kmh: float = 60.0,
        drift_angle_threshold_deg: float = 30.0,
        min_drift_speed_kmh: float = 45.0,
        fps: float = 30.0,
    ):
        self.pixels_per_meter = pixels_per_meter
        self.speed_limit_kmh = speed_limit_kmh
        self.drift_angle_threshold_deg = drift_angle_threshold_deg
        self.min_drift_speed_kmh = min_drift_speed_kmh
        self.fps = fps

    # -------------------------------------------------------------------------
    # 1. ANCHOR & TRAJECTORY UTILITIES
    # -------------------------------------------------------------------------
    def get_vehicle_anchor(self, box, anchor_type="bottom_center"):
        """
        Computes the target anchor point for bounding box tracking.
        'bottom_center' is ideal for stable ground-plane speed and trajectory analysis.
        """
        x1, y1, x2, y2 = box
        if anchor_type == "bottom_center":
            return (int((x1 + x2) / 2.0), int(y2))
        elif anchor_type == "center":
            return (int((x1 + x2) / 2.0), int((y1 + y2) / 2.0))
        return (int(x1), int(y1))

    # -------------------------------------------------------------------------
    # 2. SPEED & BEHAVIOR ANALYSIS
    # -------------------------------------------------------------------------
    def calculate_speed_kmh(self, trajectory):
        """
        Calculates instantaneous vehicle speed in km/h based on trajectory points.
        Requires at least 2 historical positions.
        """
        if len(trajectory) < 2:
            return 0.0

        # Calculate distance between last two points
        pt1 = np.array(trajectory[-2])
        pt2 = np.array(trajectory[-1])
        dist_pixels = float(np.linalg.norm(pt2 - pt1))

        # Convert to real-world meters and calculate speed
        dist_meters = dist_pixels / max(self.pixels_per_meter, 1.0)
        speed_mps = dist_meters * self.fps
        speed_kmh = speed_mps * 3.6

        return float(speed_kmh)

    def detect_drifting(self, trajectory, current_speed_kmh):
        """
        Detects sudden lateral drifting or sliding behavior.
        Active only when vehicle speed exceeds self.min_drift_speed_kmh.
        """
        if current_speed_kmh < self.min_drift_speed_kmh or len(trajectory) < 5:
            return False

        # Compute displacement vectors over recent window
        vec_recent = np.array(trajectory[-1]) - np.array(trajectory[-3])
        vec_prior = np.array(trajectory[-3]) - np.array(trajectory[-5])

        norm_recent = np.linalg.norm(vec_recent)
        norm_prior = np.linalg.norm(vec_prior)

        if norm_recent == 0 or norm_prior == 0:
            return False

        # Calculate angle between movement vectors
        dot_product = np.dot(vec_recent, vec_prior) / (norm_recent * norm_prior)
        dot_product = np.clip(dot_product, -1.0, 1.0)
        angle_deg = math.degrees(math.acos(dot_product))

        return angle_deg > self.drift_angle_threshold_deg

    def detect_lane_switch(self, trajectory, lane_boundaries):
        """
        Detects if a trajectory has crossed any defined X-coordinate lane boundary lines.
        """
        if len(trajectory) < 2:
            return False

        prev_x = trajectory[-2][0]
        curr_x = trajectory[-1][0]

        for boundary in lane_boundaries:
            if (prev_x < boundary <= curr_x) or (curr_x <= boundary < prev_x):
                return True

        return False

    # -------------------------------------------------------------------------
    # 3. ADVANCED VEHICLE CLASSIFICATION & COLOR PROFILING
    # -------------------------------------------------------------------------
    def classify_vehicle(self, crop, base_type="car"):
        """
        Classifies vehicle body subtype (Sedan, SUV, Hatchback, Auto-Rickshaw, Motorcycle, Bicycle)
        and extracts dominant vehicle color using aspect ratios and HSV analysis.
        """
        if crop is None or crop.size == 0:
            return base_type.title(), "UNKNOWN"

        h, w, _ = crop.shape
        aspect_ratio = h / max(float(w), 1.0)
        dominant_color = self._extract_dominant_color(crop)

        # A. Motorcycle / Bicycle Check
        if base_type in ["motorcycle", "bicycle"] or (aspect_ratio > 1.25 and w < 180):
            detected_type = "Bicycle" if base_type == "bicycle" else "Motorcycle"
            return detected_type, dominant_color

        # B. Auto-Rickshaw Heuristic (Indian Traffic context)
        # Typically tall footprint (aspect ratio ~0.90 to 1.35), narrow width (< 260px)
        # and characteristic yellow/green top or yellow body panels.
        if base_type in ["car", "bus", "truck"] and (0.85 <= aspect_ratio <= 1.35) and w < 260:
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            # HSV range for Indian Auto-Rickshaw Yellow
            yellow_mask = cv2.inRange(hsv, (15, 80, 80), (35, 255, 255))
            yellow_ratio = np.sum(yellow_mask > 0) / float(h * w)

            # HSV range for Auto-Rickshaw Green (CNG variant)
            green_mask = cv2.inRange(hsv, (35, 40, 40), (85, 255, 255))
            green_ratio = np.sum(green_mask > 0) / float(h * w)

            if yellow_ratio > 0.06 or (green_ratio > 0.12 and yellow_ratio > 0.02):
                return "Auto-Rickshaw", dominant_color

        # C. Larger Commercial Vehicle Handling
        if base_type in ["bus", "truck"]:
            return base_type.title(), dominant_color

        # D. Standard Passenger Car Subtype Classification
        if aspect_ratio < 0.70:
            subtype = "Sedan"
        elif aspect_ratio > 0.95:
            subtype = "SUV"
        else:
            subtype = "Hatchback"

        return subtype, dominant_color

    def _extract_dominant_color(self, crop):
        """
        Extracts dominant primary vehicle color in HSV space while ignoring ground shadows.
        """
        try:
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)

            # Central region focus to reduce background clutter
            h, w, _ = crop.shape
            center_crop = hsv[int(h * 0.2) : int(h * 0.8), int(w * 0.2) : int(w * 0.8)]
            if center_crop.size == 0:
                center_crop = hsv

            # Filter out black asphalt road surface and bright reflections
            mask = cv2.inRange(center_crop, (0, 30, 30), (180, 255, 225))
            filtered_hsv = center_crop[mask > 0]

            if len(filtered_hsv) == 0:
                return "UNKNOWN"

            avg_h = np.mean(filtered_hsv[:, 0])
            avg_s = np.mean(filtered_hsv[:, 1])
            avg_v = np.mean(filtered_hsv[:, 2])

            if avg_v < 50:
                return "Black"
            elif avg_v > 200 and avg_s < 30:
                return "White"
            elif avg_s < 40:
                return "Silver/Grey"

            # Hue Mapping
            if (0 <= avg_h < 10) or (160 <= avg_h <= 180):
                return "Red"
            elif 10 <= avg_h < 25:
                return "Orange"
            elif 25 <= avg_h < 35:
                return "Yellow"
            elif 35 <= avg_h < 85:
                return "Green"
            elif 85 <= avg_h < 130:
                return "Blue"
            elif 130 <= avg_h < 160:
                return "Purple"

            return "UNKNOWN"
        except Exception as e:
            logger.debug(f"Color extraction error: {e}")
            return "UNKNOWN"

    # -------------------------------------------------------------------------
    # 4. DAMAGE / IMPACT / SHATTER DETECTION
    # -------------------------------------------------------------------------
    def detect_glass_shatter_or_impact(self, crop):
        """
        Detects structural crash damage or shattered glass via Canny edge high-density patterns.
        """
        if crop is None or crop.size == 0:
            return False

        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 100, 200)

        edge_ratio = np.sum(edges > 0) / float(gray.shape[0] * gray.shape[1])
        
        # High edge density (>28%) typically indicates spiderweb glass fracture or crinkled metal
        return bool(edge_ratio > 0.28)