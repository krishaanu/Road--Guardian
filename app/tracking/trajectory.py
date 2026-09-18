import math
import time
from collections import deque


class VehicleTrajectory:

    def __init__(self, track_id, history_size=100):
        self.track_id = track_id

        self.points = deque(
            maxlen=history_size
        )

        self.last_seen = time.time()

    def update(self, x, y, timestamp):
        self.points.append({
            "x": float(x),
            "y": float(y),
            "timestamp": float(timestamp)
        })

        self.last_seen = time.time()

    def get_points(self):
        return list(self.points)

    def length(self):
        return len(self.points)

    def latest(self):
        if not self.points:
            return None

        return self.points[-1]

    def velocity_vector(self):

        if len(self.points) < 2:
            return 0.0, 0.0

        previous = self.points[-2]
        current = self.points[-1]

        dt = current["timestamp"] - previous["timestamp"]

        if dt <= 0:
            return 0.0, 0.0

        vx = (
            current["x"] - previous["x"]
        ) / dt

        vy = (
            current["y"] - previous["y"]
        ) / dt

        return vx, vy

    def distance_from_latest(self, x, y):

        latest = self.latest()

        if latest is None:
            return float("inf")

        return math.sqrt(
            (latest["x"] - x) ** 2 +
            (latest["y"] - y) ** 2
        )


class TrajectoryManager:

    def __init__(
        self,
        history_size=100,
        timeout=3.0
    ):
        self.history_size = history_size
        self.timeout = timeout
        self.tracks = {}

    def update(
        self,
        track_id,
        x,
        y,
        timestamp
    ):

        if track_id not in self.tracks:

            self.tracks[track_id] = VehicleTrajectory(
                track_id,
                self.history_size
            )

        trajectory = self.tracks[track_id]

        trajectory.update(
            x,
            y,
            timestamp
        )

        return trajectory

    def get(self, track_id):
        return self.tracks.get(track_id)

    def all_tracks(self):
        return self.tracks

    def cleanup(self):

        now = time.time()

        expired = []

        for track_id, trajectory in self.tracks.items():

            if (
                now -
                trajectory.last_seen
                > self.timeout
            ):
                expired.append(track_id)

        for track_id in expired:
            del self.tracks[track_id]

        return expired