import os
import time
import json
import uuid
import cv2
import numpy as np
import logging
from typing import Dict, List, Any, Optional, Tuple
from pydantic import BaseModel

from app.database.database import DatabaseManager

logger = logging.getLogger("OffensePipeline")

CROPS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/crops"))
os.makedirs(CROPS_DIR, exist_ok=True)


class GateThresholds(BaseModel):
    gate1_motorcycle: float = 0.60
    gate2_rider: float = 0.60
    gate3_helmet: float = 0.70
    gate4_ocr: float = 0.85


class GateEvaluationResult(BaseModel):
    passed_all: bool
    status: str  # "CONFIRMED" or "PENDING_REVIEW"
    offense_type: str
    license_plate: str
    gate_scores: Dict[str, float]
    failed_gates: List[str]
    crop_path: Optional[str] = None
    record_id: Optional[int] = None


class ConfidenceGatedOffensePipeline:
    """4-Stage Confidence Gated Offense Evaluation Pipeline:
    Gate 1: Motorcycle Detection (threshold >= 0.60)
    Gate 2: Rider Localization (threshold >= 0.60)
    Gate 3: Helmet / No-Helmet Classification (threshold >= 0.70)
    Gate 4: License Plate OCR (threshold >= 0.85)

    Routing Invariant:
    - If all 4 gates satisfy threshold -> Insert into `infractions`
    - If any gate falls below threshold -> Route to `needs_review_queue` with crop image
    """

    def __init__(self, thresholds: Optional[GateThresholds] = None, db: Optional[DatabaseManager] = None):
        self.thresholds = thresholds or GateThresholds()
        self.db = db or DatabaseManager()

    def evaluate_candidate(
        self,
        camera_id: str,
        frame: Optional[np.ndarray],
        bbox: Optional[Tuple[int, int, int, int]],
        motorcycle_conf: float,
        rider_conf: float,
        helmet_conf: float,
        ocr_conf: float,
        license_plate: str = "UNREAD",
        offense_type: str = "NO_HELMET",
    ) -> GateEvaluationResult:
        """Evaluates candidate violation against the 4 confidence gates."""
        scores = {
            "gate1_motorcycle": round(float(motorcycle_conf), 3),
            "gate2_rider": round(float(rider_conf), 3),
            "gate3_helmet": round(float(helmet_conf), 3),
            "gate4_ocr": round(float(ocr_conf), 3),
        }

        failed_gates = []
        if motorcycle_conf < self.thresholds.gate1_motorcycle:
            failed_gates.append("Gate 1 (Motorcycle Conf < 0.60)")
        if rider_conf < self.thresholds.gate2_rider:
            failed_gates.append("Gate 2 (Rider Localization < 0.60)")
        if helmet_conf < self.thresholds.gate3_helmet:
            failed_gates.append("Gate 3 (Helmet Classification < 0.70)")
        if ocr_conf < self.thresholds.gate4_ocr:
            failed_gates.append("Gate 4 (License Plate OCR < 0.85)")

        passed_all = len(failed_gates) == 0

        # Save crop if frame and bbox are provided
        crop_filename = None
        crop_path = None
        if frame is not None and bbox is not None:
            try:
                x1, y1, x2, y2 = bbox
                h, w = frame.shape[:2]
                x1, y1 = max(0, int(x1)), max(0, int(y1))
                x2, y2 = min(w, int(x2)), min(h, int(y2))
                if x2 > x1 and y2 > y1:
                    crop = frame[y1:y2, x1:x2]
                    crop_filename = f"crop_{camera_id}_{int(time.time())}_{uuid.uuid4().hex[:6]}.jpg"
                    crop_path = os.path.join(CROPS_DIR, crop_filename)
                    cv2.imwrite(crop_path, crop)
            except Exception as e:
                logger.error(f"Error saving infraction crop: {e}")

        relative_crop_url = f"/data/crops/{crop_filename}" if crop_filename else None

        if passed_all:
            # Confirmed infraction
            rec_id = self.db.log_infraction(
                camera_id=camera_id,
                offense_type=offense_type,
                license_plate=license_plate,
                confidence_scores=json.dumps(scores),
                image_crop_path=relative_crop_url,
                status="CONFIRMED",
            )
            return GateEvaluationResult(
                passed_all=True,
                status="CONFIRMED",
                offense_type=offense_type,
                license_plate=license_plate,
                gate_scores=scores,
                failed_gates=[],
                crop_path=relative_crop_url,
                record_id=rec_id,
            )
        else:
            # Needs review queue
            rec_id = self.db.log_needs_review(
                camera_id=camera_id,
                offense_type=offense_type,
                gate_scores=json.dumps({
                    "scores": scores,
                    "reasons": failed_gates,
                }),
                license_plate=license_plate,
                image_crop_path=relative_crop_url,
                status="PENDING_REVIEW",
            )
            return GateEvaluationResult(
                passed_all=False,
                status="PENDING_REVIEW",
                offense_type=offense_type,
                license_plate=license_plate,
                gate_scores=scores,
                failed_gates=failed_gates,
                crop_path=relative_crop_url,
                record_id=rec_id,
            )
