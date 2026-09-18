import numpy as np

class RiderSafetyAnalyzer:
    def __init__(self):
        # Class IDs from COCO or custom fine-tuned weights
        self.BIKE_CLASSES = [1, 3]  # bicycle, motorcycle/scooter
        self.PERSON_CLASS = 0
        self.HELMET_CLASS = 105     # Custom class ID or fine-tuned model ID

    def detect_triple_riding(self, bike_box, person_boxes):
        """
        Counts person bounding boxes overlapping/contained within a bike bounding box.
        """
        bx1, by1, bx2, by2 = bike_box
        riders_count = 0

        for px1, py1, px2, py2 in person_boxes:
            # Calculate overlapping center of person relative to bike box
            pcx, pcy = (px1 + px2) / 2, (py1 + py2) / 2
            if bx1 <= pcx <= bx2 and (by1 - (by2 - by1) * 0.3) <= pcy <= by2:
                riders_count += 1

        return riders_count >= 3, riders_count

    def detect_no_helmet(self, person_box, helmet_boxes):
        """
        Verifies if a helmet detection exists in the upper head area of the rider.
        """
        px1, py1, px2, py2 = person_box
        head_zone_y2 = py1 + (py2 - py1) * 0.35  # Upper 35% of person height

        for hx1, hy1, hx2, hy2 in helmet_boxes:
            hcx, hcy = (hx1 + hx2) / 2, (hy1 + hy2) / 2
            if px1 <= hcx <= px2 and py1 <= hcy <= head_zone_y2:
                return False  # Helmet detected

        return True  # No helmet found
