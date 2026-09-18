import cv2
import numpy as np
import easyocr
import re
from typing import Optional


class VehicleAttributeExtractor:
    """Extracts license plate text and vehicle body color from bounding box crops."""

    def __init__(self):
        # Initialize EasyOCR for English text recognition
        self.reader = easyocr.Reader(['en'], gpu=True)

    def extract_plate_text(self, car_crop: np.ndarray) -> Optional[str]:
        """Detects and reads alphanumeric characters from a car bounding box."""
        if car_crop is None or car_crop.size == 0:
            return None

        # Run OCR on the crop
        results = self.reader.readtext(car_crop, detail=0)
        
        for text in results:
            clean_text = re.sub(r'[^A-Z0-9]', '', text.upper())
            if 4 <= len(clean_text) <= 10:
                return clean_text

        return None

    def detect_color(self, car_crop: np.ndarray) -> str:
        """Determines vehicle color using robust dominant color analysis in HSV space."""
        if car_crop is None or car_crop.size == 0:
            return "UNKNOWN"

        h, w, _ = car_crop.shape
        if h < 10 or w < 10:
            return "UNKNOWN"

        # Crop the central region (15% to 85% range) to exclude road background
        center_crop = car_crop[int(h * 0.20):int(h * 0.80), int(w * 0.20):int(w * 0.80)]
        if center_crop.size == 0:
            center_crop = car_crop

        # Convert crop to HSV color space
        hsv = cv2.cvtColor(center_crop, cv2.COLOR_BGR2HSV)
        
        # Calculate median HSV values to avoid extreme outlier pixels
        hue = np.median(hsv[:, :, 0])
        sat = np.median(hsv[:, :, 1])
        val = np.median(hsv[:, :, 2])

        # Color boundaries in OpenCV HSV (Hue: 0-180, Sat: 0-255, Val: 0-255)
        if val < 45:
            return "BLACK"
        if val > 190 and sat < 40:
            return "WHITE"
        if sat < 45:
            return "SILVER/GRAY"
        
        # Chromatic colors based on Hue angle
        if hue < 10 or hue >= 165:
            return "RED"
        elif 10 <= hue < 25:
            return "ORANGE/BROWN"
        elif 25 <= hue < 35:
            return "YELLOW"
        elif 35 <= hue < 85:
            return "GREEN"
        elif 85 <= hue < 135:
            return "BLUE"
        elif 135 <= hue < 165:
            return "PURPLE"

        return "UNKNOWN"