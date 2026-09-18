import numpy as np
from typing import Dict, List, Tuple, Optional


def compute_iou(boxA: Tuple[int, int, int, int], boxB: Tuple[int, int, int, int]) -> float:
    """Computes Intersection over Union (IoU) between two bounding boxes."""
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2])
    yB = min(boxA[3], boxB[3])

    interArea = max(0, xB - xA) * max(0, yB - yA)
    if interArea == 0:
        return 0.0

    boxAArea = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
    boxBArea = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])

    return interArea / float(boxAArea + boxBArea - interArea)


class TrajectoryIntersectionEngine:
    """Strict Collision Engine.
    
    Predictive 'Collision Risk' has been removed.
    Alerts trigger ONLY upon confirmed physical impact (Bounding box overlap + sudden deceleration).
    """

    def __init__(self, iou_threshold: float = 0.15, min_deceleration_kmh: float = 25.0):
        self.iou_threshold = iou_threshold
        self.min_deceleration_kmh = min_deceleration_kmh
        self.impact_debounce: Dict[Tuple[int, int], int] = {}

    def detect_confirmed_collisions(
        self,
        boxes: Dict[int, Tuple[int, int, int, int]],
        speeds: Dict[int, float],
        previous_speeds: Dict[int, float],
        track_ages: Dict[int, int],
    ) -> List[Tuple[int, int]]:
        """Detects actual physical collisions.
        
        Requires:
        1. Bounding box IoU overlap > 0.15
        2. Rapid speed drop (> 25 km/h within 1-2 frames)
        3. Both tracks age >= 10 frames (filters ByteTrack spawn artifacts)
        """
        confirmed_collisions = []
        track_ids = list(boxes.keys())

        for i in range(len(track_ids)):
            for j in range(i + 1, len(track_ids)):
                idA, idB = track_ids[i], track_ids[j]

                # 1. Ignore newly spawned tracks (ByteTrack occlusion artifacts)
                if track_ages.get(idA, 0) < 10 or track_ages.get(idB, 0) < 10:
                    continue

                boxA = boxes[idA]
                boxB = boxes[idB]

                # 2. Check physical bounding box overlap
                iou = compute_iou(boxA, boxB)
                if iou >= self.iou_threshold:
                    # 3. Check for sudden deceleration on impact
                    speed_drop_A = previous_speeds.get(idA, 0.0) - speeds.get(idA, 0.0)
                    speed_drop_B = previous_speeds.get(idB, 0.0) - speeds.get(idB, 0.0)

                    if speed_drop_A > self.min_deceleration_kmh or speed_drop_B > self.min_deceleration_kmh:
                        pair_key = (min(idA, idB), max(idA, idB))
                        self.impact_debounce[pair_key] = self.impact_debounce.get(pair_key, 0) + 1

                        # Require sustained impact over at least 3 frames
                        if self.impact_debounce[pair_key] >= 3:
                            confirmed_collisions.append((idA, idB))
                    else:
                        pair_key = (min(idA, idB), max(idA, idB))
                        self.impact_debounce[pair_key] = max(0, self.impact_debounce.get(pair_key, 0) - 1)

        return confirmed_collisions

    def purge_lost_tracks(self, active_track_ids: List[int]):
        """Cleans up internal buffers for tracks no longer visible."""
        active_set = set(active_track_ids)
        dead_keys = [
            pair for pair in self.impact_debounce
            if pair[0] not in active_set or pair[1] not in active_set
        ]
        for k in dead_keys:
            del self.impact_debounce[k]

    def reset(self):
        """Resets debounce states upon stream rewind."""
        self.impact_debounce.clear()