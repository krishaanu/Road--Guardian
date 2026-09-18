"""
Zone Calibration & Homography Mapping Module for RoadGuardian ITS.

Defines reference lines (red x 2, blue, yellow) as calibrated distance anchors,
constructs the bounded corridor polygon between the two red lines,
and transforms 2D camera pixels into metric ground coordinates.
"""

from typing import List, Tuple, Dict, Optional, Union
import numpy as np
import cv2
from pydantic import BaseModel, Field


class ZoneAnchor(BaseModel):
    """
    Data structure representing a calibrated reference line anchor on the roadway.
    """
    line_id: str = Field(..., description="Unique identifier for the reference line, e.g., 'red_entry', 'red_exit', 'blue_datum', 'yellow_limit'")
    pixel_points: List[Tuple[int, int]] = Field(..., min_length=2, description="At least two (x, y) pixel coordinates defining the line segment across lanes")
    real_distance_m: float = Field(..., description="Calibrated longitudinal distance in meters along the road corridor")


class ZoneCalibrationConfig(BaseModel):
    """
    Configuration encapsulating all calibrated reference line anchors and corridor parameters.
    """
    camera_id: str = "CAMERA_01"
    road_width_m: float = 10.5  # Standard 3-lane motorway width (~3.5m per lane)
    road_length_m: float = 200.0
    entry_line_id: str = "red_entry"
    exit_line_id: str = "red_exit"
    datum_line_id: str = "blue_datum"
    limit_line_id: str = "yellow_limit"
    anchors: Dict[str, ZoneAnchor] = Field(default_factory=dict)


class CalibratedHomography:
    """
    Computes and manages homography transformation matrix M and its inverse,
    formulates the bounded tracking corridor between red lines, and rectifies
    perspective distortion into real-world metric coordinates.
    """

    def __init__(self, config: Optional[ZoneCalibrationConfig] = None):
        if config is None:
            config = self.get_default_calibration()
        self.config = config

        self.M: Optional[np.ndarray] = None
        self.inv_M: Optional[np.ndarray] = None
        self.corridor_polygon: Optional[np.ndarray] = None

        self._build_homography_and_corridor()

    @staticmethod
    def get_default_calibration(frame_size: Tuple[int, int] = (1920, 1080)) -> ZoneCalibrationConfig:
        """
        Builds a default motorway calibration configuration with red, blue, and yellow anchors.
        Coordinates assume a perspective road where bottom is near and top is far.
        """
        w, h = frame_size
        anchors = {
            # Entry red line (near field)
            "red_entry": ZoneAnchor(
                line_id="red_entry",
                pixel_points=[(int(w * 0.15), int(h * 0.88)), (int(w * 0.85), int(h * 0.88))],
                real_distance_m=10.0,
            ),
            # Optimal reference blue line (datum, 100% confidence)
            "blue_datum": ZoneAnchor(
                line_id="blue_datum",
                pixel_points=[(int(w * 0.22), int(h * 0.72)), (int(w * 0.78), int(h * 0.72))],
                real_distance_m=35.0,
            ),
            # Exit red line (end of verified speed corridor)
            "red_exit": ZoneAnchor(
                line_id="red_exit",
                pixel_points=[(int(w * 0.30), int(h * 0.55)), (int(w * 0.70), int(h * 0.55))],
                real_distance_m=70.0,
            ),
            # Yellow limit line (far boundary, threshold for review)
            "yellow_limit": ZoneAnchor(
                line_id="yellow_limit",
                pixel_points=[(int(w * 0.38), int(h * 0.38)), (int(w * 0.62), int(h * 0.38))],
                real_distance_m=110.0,
            ),
        }
        return ZoneCalibrationConfig(
            camera_id="CAMERA_01",
            road_width_m=10.5,
            road_length_m=120.0,
            entry_line_id="red_entry",
            exit_line_id="red_exit",
            datum_line_id="blue_datum",
            limit_line_id="yellow_limit",
            anchors=anchors,
        )

    def _build_homography_and_corridor(self):
        """Calculates homography matrix and builds the 4-corner corridor polygon."""
        anchors = self.config.anchors
        entry_anchor = anchors.get(self.config.entry_line_id)
        exit_anchor = anchors.get(self.config.exit_line_id)

        if entry_anchor and exit_anchor:
            p_entry_left = entry_anchor.pixel_points[0]
            p_entry_right = entry_anchor.pixel_points[-1]
            p_exit_right = exit_anchor.pixel_points[-1]
            p_exit_left = exit_anchor.pixel_points[0]

            # Formulate the bounded polygon between the two red lines:
            # Order: bottom-left, bottom-right, top-right, top-left
            self.corridor_polygon = np.array([
                p_entry_left,
                p_entry_right,
                p_exit_right,
                p_exit_left
            ], dtype=np.int32)

            # Define 4-point homography source correspondences in pixel space
            src_pts = np.float32([
                p_entry_left,
                p_entry_right,
                p_exit_right,
                p_exit_left
            ])

            # Corresponding metric ground plane coordinates (meters)
            # X: 0 to road_width_m, Y: entry distance to exit distance
            y_entry = entry_anchor.real_distance_m
            y_exit = exit_anchor.real_distance_m
            rw = self.config.road_width_m

            dst_pts = np.float32([
                [0.0, y_entry],
                [rw, y_entry],
                [rw, y_exit],
                [0.0, y_exit]
            ])

            self.M = cv2.getPerspectiveTransform(src_pts, dst_pts)
            self.inv_M = cv2.getPerspectiveTransform(dst_pts, src_pts)
        else:
            # Fallback identity if anchors are missing
            self.M = np.eye(3, dtype=np.float32)
            self.inv_M = np.eye(3, dtype=np.float32)

    def is_in_corridor(self, point: Tuple[float, float]) -> bool:
        """
        Determines whether a pixel coordinate (x, y) lies inside the red-line corridor.
        Returns True if inside or on the boundary, False otherwise.
        """
        if self.corridor_polygon is None or len(self.corridor_polygon) < 3:
            return False
        # cv2.pointPolygonTest: >0 inside, 0 on edge, <0 outside
        dist = cv2.pointPolygonTest(self.corridor_polygon, (float(point[0]), float(point[1])), measureDist=False)
        return dist >= 0

    def transform_point(self, point: Union[Tuple[float, float], List[float], np.ndarray]) -> Tuple[float, float]:
        """
        Transforms a pixel coordinate (x, y) to real-world ground coordinate (X, Y) in meters.
        """
        if self.M is None:
            return float(point[0]), float(point[1])
        pts = np.array([[[float(point[0]), float(point[1])]]], dtype=np.float32)
        transformed = cv2.perspectiveTransform(pts, self.M)
        return float(transformed[0][0][0]), float(transformed[0][0][1])

    def inverse_transform_point(self, ground_point: Union[Tuple[float, float], List[float], np.ndarray]) -> Tuple[float, float]:
        """
        Transforms real-world ground coordinate (X, Y) back to 2D pixel coordinates (x, y).
        """
        if self.inv_M is None:
            return float(ground_point[0]), float(ground_point[1])
        pts = np.array([[[float(ground_point[0]), float(ground_point[1])]]], dtype=np.float32)
        transformed = cv2.perspectiveTransform(pts, self.inv_M)
        return float(transformed[0][0][0]), float(transformed[0][0][1])

    def get_axial_distance(self, point: Tuple[float, float]) -> float:
        """
        Returns the longitudinal ground distance (Y in meters) along the road corridor.
        """
        _, y_metric = self.transform_point(point)
        return y_metric

    def calculate_ground_distance(self, pt1: Tuple[float, float], pt2: Tuple[float, float]) -> float:
        """
        Calculates metric Euclidean distance in meters between two pixel coordinate points.
        """
        g1 = np.array(self.transform_point(pt1))
        g2 = np.array(self.transform_point(pt2))
        return float(np.linalg.norm(g2 - g1))

    def get_corridor_length_m(self) -> float:
        """Returns the metric length of the red-line corridor in meters."""
        entry_anchor = self.config.anchors.get(self.config.entry_line_id)
        exit_anchor = self.config.anchors.get(self.config.exit_line_id)
        if entry_anchor and exit_anchor:
            return abs(exit_anchor.real_distance_m - entry_anchor.real_distance_m)
        return 60.0


class CameraZoneRegistry:
    """
    Maintains calibrated homography models and reference line anchors per camera stream.
    Enables dynamic registration and graceful cleanup when cameras are deleted.
    """

    def __init__(self):
        self._calibrations: Dict[str, CalibratedHomography] = {}
        # Pre-seed default calibration for CAMERA_01
        self._calibrations["CAMERA_01"] = CalibratedHomography(
            CalibratedHomography.get_default_calibration()
        )

    def register_calibration(self, camera_id: str, config: ZoneCalibrationConfig) -> CalibratedHomography:
        """Registers or updates calibration for a camera."""
        config.camera_id = camera_id
        homography = CalibratedHomography(config)
        self._calibrations[camera_id] = homography
        return homography

    def get_calibration(self, camera_id: str) -> CalibratedHomography:
        """Retrieves calibration for a camera, falling back to default if not yet explicitly calibrated."""
        if camera_id not in self._calibrations:
            cfg = CalibratedHomography.get_default_calibration()
            cfg.camera_id = camera_id
            self._calibrations[camera_id] = CalibratedHomography(cfg)
        return self._calibrations[camera_id]

    def clear_calibration(self, camera_id: str) -> bool:
        """Purges cached calibration zones and corridor polygons for a deleted camera."""
        if camera_id in self._calibrations:
            del self._calibrations[camera_id]
            return True
        return False


zone_registry = CameraZoneRegistry()
