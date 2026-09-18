import cv2
import numpy as np
from typing import Dict, List, Tuple


def draw_trajectories_and_events(
    frame: np.ndarray,
    trajectories: Dict[int, List[Tuple[float, float]]],
    speeds: Dict[int, float],
    drifting_ids: List[int],
    alerts: List[str]
) -> np.ndarray:
    """
    Renders electric blue motion trails that dynamically fade and 
    instantly disappear when vehicles exit the surveillance scene.
    """
    overlay = frame.copy()

    # 1. Render Electric Blue Motion Trails
    for track_id, points in trajectories.items():
        if len(points) < 2:
            continue

        num_pts = len(points)
        for i in range(1, num_pts):
            pt1 = (int(points[i - 1][0]), int(points[i - 1][1]))
            pt2 = (int(points[i][0]), int(points[i][1]))

            # Accelerated opacity falloff (older points fade much faster)
            alpha = (i / num_pts) ** 1.8
            thickness = max(1, int(3 * alpha))

            # Bright Electric Blue Color in OpenCV BGR Space: (255, 128, 0)
            blue_color = (255, 128, 0)
            cv2.line(overlay, pt1, pt2, blue_color, thickness, cv2.LINE_AA)

        # Draw glowing head marker dot at active vehicle centroid
        last_pt = (int(points[-1][0]), int(points[-1][1]))
        cv2.circle(overlay, last_pt, 4, (255, 200, 0), -1, cv2.LINE_AA)

        # Render Smoothed Speed Metric Tag
        speed = speeds.get(track_id, 0.0)
        badge_color = (0, 0, 255) if speed > 80.0 else (0, 230, 118)
        
        cv2.putText(
            overlay,
            f"{speed:.0f} km/h",
            (last_pt[0] - 15, last_pt[1] - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            badge_color,
            1,
            cv2.LINE_AA
        )

        if track_id in drifting_ids:
            cv2.putText(
                overlay,
                "⚠️ DRIFT",
                (last_pt[0] - 15, last_pt[1] - 26),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 165, 255),
                2,
                cv2.LINE_AA
            )

    # 2. Blend Overlay onto Main Frame (85% Overlay / 15% Frame)
    cv2.addWeighted(overlay, 0.85, frame, 0.15, 0, frame)

    # 3. Render Top Emergency Incident Alerts Banner
    y_offset = 35
    for alert in alerts:
        cv2.rectangle(frame, (10, y_offset - 22), (430, y_offset + 6), (0, 0, 180), -1)
        cv2.putText(frame, f"ALERT: {alert}", (18, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
        y_offset += 32

    return frame