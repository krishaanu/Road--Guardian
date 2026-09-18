import cv2
import re


class PlateReader:

    def __init__(
        self,
        plate_model,
        reader,
        device="cpu",
        confidence=0.35
    ):
        self.plate_model = plate_model
        self.reader = reader
        self.device = device
        self.confidence = confidence

    @staticmethod
    def clean_text(text):

        text = text.upper()

        return re.sub(
            r"[^A-Z0-9]",
            "",
            text
        )

    def detect_plate(self, vehicle_crop):

        if (
            vehicle_crop is None
            or vehicle_crop.size == 0
        ):
            return None

        results = self.plate_model(
            vehicle_crop,
            conf=self.confidence,
            verbose=False,
            device=self.device
        )

        boxes = results[0].boxes

        if boxes is None or len(boxes) == 0:
            return None

        best = max(
            boxes,
            key=lambda b: float(b.conf[0])
        )

        x1, y1, x2, y2 = map(
            int,
            best.xyxy[0].tolist()
        )

        h, w = vehicle_crop.shape[:2]

        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(w, x2)
        y2 = min(h, y2)

        if x2 <= x1 or y2 <= y1:
            return None

        plate = vehicle_crop[
            y1:y2,
            x1:x2
        ]

        if plate.size == 0:
            return None

        return plate

    def read(self, vehicle_crop):

        plate = self.detect_plate(
            vehicle_crop
        )

        if plate is None:
            return None, 0.0

        h, w = plate.shape[:2]

        if w < 40 or h < 12:
            return None, 0.0

        # Resize
        resized = cv2.resize(
            plate,
            None,
            fx=3,
            fy=3,
            interpolation=cv2.INTER_CUBIC
        )

        gray = cv2.cvtColor(
            resized,
            cv2.COLOR_BGR2GRAY
        )

        clahe = cv2.createCLAHE(
            clipLimit=2.5,
            tileGridSize=(8, 8)
        )

        enhanced = clahe.apply(gray)

        threshold = cv2.adaptiveThreshold(
            enhanced,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            10
        )

        best_text = None
        best_confidence = 0.0

        for image in (
            resized,
            enhanced,
            threshold
        ):

            results = self.reader.readtext(
                image,
                detail=1,
                paragraph=False,
                allowlist=(
                    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                    "0123456789"
                )
            )

            for result in results:

                if len(result) < 3:
                    continue

                text = self.clean_text(
                    result[1]
                )

                confidence = float(
                    result[2]
                )

                if len(text) < 4:
                    continue

                if confidence > best_confidence:
                    best_text = text
                    best_confidence = confidence

        if best_text is None:
            return None, 0.0

        return (
            best_text,
            best_confidence
        )