import cv2
import numpy as np
from typing import List, Tuple, Union, Optional


class PerspectiveTransformer:
    """
    Transforms 2D camera pixel coordinates into flat ground-plane coordinates in meters.
    Uses OpenCV's cv2.getPerspectiveTransform and cv2.perspectiveTransform to rectify
    perspective foreshortening on road surfaces.
    """

    def __init__(
        self,
        src_points: Optional[Union[np.ndarray, List[List[float]]]] = None,
        dst_points: Optional[Union[np.ndarray, List[List[float]]]] = None,
        road_width_m: float = 10.5,    # Standard 3-lane highway width (~3.5m per lane)
        road_length_m: float = 200.0,  # Visible motorway segment length (~0.20 km)
        frame_size: Tuple[int, int] = (1920, 1080),  # (width, height)
    ):
        """
        Initializes perspective transformation homography matrix.
        If src_points and dst_points are not provided, uses a standard motorway perspective trapezoid.

        :param src_points: 4 source (x, y) coordinates in pixel space.
        :param dst_points: 4 destination (X, Y) coordinates in real-world metric space (meters).
        :param road_width_m: Estimated road width in meters.
        :param road_length_m: Estimated road length in meters.
        :param frame_size: Video frame resolution (width, height).
        """
        w, h = frame_size

        if src_points is None:
            # Default trapezoidal ROI covering the road lanes
            # Ordered: [bottom-left, bottom-right, top-right, top-left]
            self.src_points = np.float32([
                [w * 0.10, h * 0.95],
                [w * 0.90, h * 0.95],
                [w * 0.65, h * 0.35],
                [w * 0.35, h * 0.35],
            ])
        else:
            self.src_points = np.float32(src_points)

        if dst_points is None:
            # Metric bird's-eye rectangular ground plane (X: meters across, Y: meters along)
            self.dst_points = np.float32([
                [0.0, 0.0],
                [road_width_m, 0.0],
                [road_width_m, road_length_m],
                [0.0, road_length_m],
            ])
        else:
            self.dst_points = np.float32(dst_points)

        if self.src_points.shape != (4, 2) or self.dst_points.shape != (4, 2):
            raise ValueError("src_points and dst_points must each contain exactly 4 (x, y) coordinates.")

        # Compute forward and inverse homography matrices
        self.M = cv2.getPerspectiveTransform(self.src_points, self.dst_points)
        self.inv_M = cv2.getPerspectiveTransform(self.dst_points, self.src_points)

    def transform_point(self, point: Union[Tuple[float, float], List[float], np.ndarray]) -> Tuple[float, float]:
        """
        Converts a single 2D frame pixel coordinate (x, y) into a ground-plane coordinate (X, Y) in meters.
        """
        pts = np.array([[[float(point[0]), float(point[1])]]], dtype=np.float32)
        transformed = cv2.perspectiveTransform(pts, self.M)
        return float(transformed[0][0][0]), float(transformed[0][0][1])

    def transform_points(self, points: Union[List, np.ndarray]) -> np.ndarray:
        """
        Converts multiple 2D frame pixel coordinates into ground-plane coordinates in meters.

        :param points: Iterable or array of (x, y) pixel coordinates.
        :return: (N, 2) NumPy array of ground coordinates in meters.
        """
        if len(points) == 0:
            return np.empty((0, 2), dtype=np.float32)

        pts = np.asarray(points, dtype=np.float32).reshape(-1, 1, 2)
        transformed = cv2.perspectiveTransform(pts, self.M)
        return transformed.reshape(-1, 2)

    def inverse_transform_point(self, ground_point: Union[Tuple[float, float], List[float], np.ndarray]) -> Tuple[float, float]:
        """
        Converts a ground coordinate (X, Y) in meters back to 2D frame pixel coordinates (x, y).
        """
        pts = np.array([[[float(ground_point[0]), float(ground_point[1])]]], dtype=np.float32)
        transformed = cv2.perspectiveTransform(pts, self.inv_M)
        return float(transformed[0][0][0]), float(transformed[0][0][1])

    def calculate_ground_distance(
        self,
        pt1: Union[Tuple[float, float], List[float], np.ndarray],
        pt2: Union[Tuple[float, float], List[float], np.ndarray],
    ) -> float:
        """
        Computes the Euclidean distance in real-world meters between two pixel coordinate points.
        """
        g1 = np.array(self.transform_point(pt1))
        g2 = np.array(self.transform_point(pt2))
        return float(np.linalg.norm(g2 - g1))
