import enum
import cv2
import numpy as np


class VehicleView(enum.Enum):
    FRONT = "FRONT"
    REAR = "REAR"
    SIDE = "SIDE"
    UNKNOWN = "UNKNOWN"


class VehicleViewClassifier:
    """
    Lightweight vehicle-view estimator using visual geometry and edge density analysis.
    
    Estimates FRONT / REAR / SIDE / UNKNOWN without requiring a heavy neural network.
    """

    def __init__(self, device: str = "cpu"):
        self.device = device

    def predict(
        self, vehicle_crop: np.ndarray, has_plate: bool = False
    ) -> VehicleView:
        """
        Predicts vehicle view (FRONT, REAR, SIDE, UNKNOWN).
        
        Args:
            vehicle_crop (np.ndarray): Crop image of the vehicle (BGR format).
            has_plate (bool, optional): Hint flag indicating license plate presence.
        
        Returns:
            VehicleView: Enum instance corresponding to the detected view.
        """
        if vehicle_crop is None or vehicle_crop.size == 0:
            return VehicleView.UNKNOWN

        h, w = vehicle_crop.shape[:2]

        if h < 20 or w < 20:
            return VehicleView.UNKNOWN

        # Quick hint override: Plate presence usually indicates front or rear view
        ratio = w / float(h)

        # Very wide crop indicates a side-oriented vehicle trajectory
        if ratio > 2.2:
            return VehicleView.SIDE

        # Convert to grayscale for Canny edge density analysis
        gray = cv2.cvtColor(vehicle_crop, cv2.COLOR_BGR2GRAY)

        # Analyze horizontal edge distribution
        edges = cv2.Canny(gray, 80, 160)

        upper = edges[: max(1, h // 2)]
        lower = edges[h // 2 :]

        upper_density = np.count_nonzero(upper) / max(1, upper.size)
        lower_density = np.count_nonzero(lower) / max(1, lower.size)

        # Front views generally produce stronger lower-half structure (grille/bumper)
        if lower_density > upper_density * 1.10:
            return VehicleView.FRONT

        # Rear views often show strong upper/mid structural boundaries (windshield/tailgate)
        if upper_density > lower_density * 1.10:
            return VehicleView.REAR

        # Fallback for balanced edge density using license plate hint
        if has_plate:
            return VehicleView.FRONT

        return VehicleView.UNKNOWN