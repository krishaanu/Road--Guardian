import numpy as np
from typing import Dict, Tuple, List, Optional


class SingleTrackKalmanFilter:
    """
    2D Constant Velocity (CV) Kalman Filter for an individual vehicle track.
    State vector: [x, y, vx, vy]^T
    Measurement vector: [x, y]^T
    """

    def __init__(
        self,
        initial_pos: Tuple[float, float],
        sigma_m: float = 3.5,       # Measurement noise std (pixels)
        sigma_a: float = 250.0,     # Process noise / acceleration std (pixels/s^2)
        initial_vel: Tuple[float, float] = (0.0, 0.0),
    ):
        self.sigma_m = sigma_m
        self.sigma_a = sigma_a

        # State vector [x, y, vx, vy]
        self.x = np.array([initial_pos[0], initial_pos[1], initial_vel[0], initial_vel[1]], dtype=np.float64)

        # Initial state covariance P: high velocity variance (1e6) so initial velocity rapidly converges
        self.P = np.diag([self.sigma_m ** 2, self.sigma_m ** 2, 1e6, 1e6]).astype(np.float64)

        # Measurement matrix H (2x4)
        self.H = np.array([
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0]
        ], dtype=np.float64)

        # Measurement covariance R (2x2)
        self.R = np.eye(2, dtype=np.float64) * (self.sigma_m ** 2)

    def predict(self, dt: float = 1.0 / 30.0) -> np.ndarray:
        """Predicts the state ahead by dt seconds."""
        # State transition matrix F
        F = np.array([
            [1.0, 0.0, dt,  0.0],
            [0.0, 1.0, 0.0, dt ],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0]
        ], dtype=np.float64)

        # Continuous white noise acceleration process covariance Q
        dt2 = dt * dt
        dt3 = dt2 * dt
        dt4 = dt3 * dt
        q_scale = self.sigma_a ** 2

        Q = np.array([
            [dt4 / 4.0, 0.0,       dt3 / 2.0, 0.0      ],
            [0.0,       dt4 / 4.0, 0.0,       dt3 / 2.0],
            [dt3 / 2.0, 0.0,       dt2,       0.0      ],
            [0.0,       dt3 / 2.0, 0.0,       dt2      ]
        ], dtype=np.float64) * q_scale

        self.x = F @ self.x
        self.P = F @ self.P @ F.T + Q
        return self.x

    def update(self, measurement: Tuple[float, float]) -> np.ndarray:
        """Updates the filter with a new 2D position measurement (x, y)."""
        z = np.array(measurement, dtype=np.float64)
        y = z - (self.H @ self.x)  # Innovation / residual

        # Innovation covariance S
        S = self.H @ self.P @ self.H.T + self.R

        # Kalman gain K
        K = self.P @ self.H.T @ np.linalg.inv(S)

        # State update
        self.x = self.x + (K @ y)

        # Covariance update (Joseph form for numerical stability)
        I = np.eye(4, dtype=np.float64)
        I_KH = I - (K @ self.H)
        self.P = I_KH @ self.P @ I_KH.T + K @ self.R @ K.T

        return self.x


class VehicleStateFilter:
    """
    Manages multi-vehicle 2D Constant Velocity Kalman Filters to eliminate pixel detection
    jitter from bounding box anchors and produce smooth position and velocity estimates.
    """

    def __init__(self, sigma_m: float = 3.5, sigma_a: float = 250.0, fps: float = 30.0):
        self.sigma_m = sigma_m
        self.sigma_a = sigma_a
        self.fps = fps
        self.filters: Dict[int, SingleTrackKalmanFilter] = {}
        self.track_ages: Dict[int, int] = {}
        self.last_positions: Dict[int, Tuple[float, float]] = {}

    def update(
        self,
        track_id: int,
        raw_anchor: Tuple[float, float],
        dt: Optional[float] = None
    ) -> Tuple[float, float, float, float]:
        """
        Updates the track's Kalman filter with raw anchor measurement.
        Returns (smoothed_x, smoothed_y, vx_pixels_per_s, vy_pixels_per_s).
        """
        if dt is None or dt <= 0:
            dt = 1.0 / self.fps

        if track_id not in self.filters:
            # Estimate initial velocity if previous raw position is available
            init_vx, init_vy = 0.0, 0.0
            if track_id in self.last_positions:
                prev_x, prev_y = self.last_positions[track_id]
                init_vx = (raw_anchor[0] - prev_x) / dt
                init_vy = (raw_anchor[1] - prev_y) / dt

            self.filters[track_id] = SingleTrackKalmanFilter(
                initial_pos=raw_anchor,
                sigma_m=self.sigma_m,
                sigma_a=self.sigma_a,
                initial_vel=(init_vx, init_vy),
            )
            self.track_ages[track_id] = 1
        else:
            self.track_ages[track_id] += 1
            self.filters[track_id].predict(dt)

        self.last_positions[track_id] = raw_anchor
        state = self.filters[track_id].update(raw_anchor)
        return float(state[0]), float(state[1]), float(state[2]), float(state[3])

    def get_position(self, track_id: int) -> Optional[Tuple[float, float]]:
        """Returns the smoothed (x, y) position for a track."""
        if track_id in self.filters:
            return float(self.filters[track_id].x[0]), float(self.filters[track_id].x[1])
        return None

    def get_velocity(self, track_id: int) -> Optional[Tuple[float, float]]:
        """Returns the smoothed (vx, vy) velocity (pixels/s) for a track."""
        if track_id in self.filters:
            return float(self.filters[track_id].x[2]), float(self.filters[track_id].x[3])
        return None

    def get_track_age(self, track_id: int) -> int:
        """Returns the total number of frames this track ID has been updated."""
        return self.track_ages.get(track_id, 0)

    def purge_lost_tracks(self, active_track_ids: List[int]):
        """Removes filters and tracking states for tracks that are no longer active."""
        active_set = set(active_track_ids)
        for tid in list(self.filters.keys()):
            if tid not in active_set:
                del self.filters[tid]
                self.track_ages.pop(tid, None)
                self.last_positions.pop(tid, None)

    def reset(self):
        """Resets all track filters."""
        self.filters.clear()
        self.track_ages.clear()
        self.last_positions.clear()
