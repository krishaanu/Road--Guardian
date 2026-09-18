from datetime import datetime

from app.database.database import save_alert


def process_collision(
    camera_id,
    vehicle_a,
    vehicle_b,
    probability
):

    percentage = probability * 100.0

    print()
    print("========================================")
    print("       ROADGUARDIAN ALERT")
    print("========================================")
    print("Possible collision detected")
    print("Camera:", camera_id)
    print("Vehicle A:", vehicle_a)
    print("Vehicle B:", vehicle_b)
    print(f"Probability: {percentage:.1f}%")
    print("Time:", datetime.now())
    print("========================================")
    print()

    save_alert(
        camera_id=camera_id,
        alert_type="POSSIBLE_COLLISION",
        vehicle_a=vehicle_a,
        vehicle_b=vehicle_b,
        probability=probability,
        message=(
            f"Possible collision between "
            f"vehicles {vehicle_a} and {vehicle_b}"
        )
    )