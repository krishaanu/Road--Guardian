import urllib.request
import urllib.parse
import json
import time
import io
import os
import sys

def run_tests():
    print("Connecting to live backend...")
    for _ in range(10):
        try:
            req = urllib.request.urlopen("http://127.0.0.1:8000/")
            res = json.loads(req.read().decode())
            if res.get("status") == "online":
                print("[PASS] Backend healthcheck online:", res)
                break
        except Exception:
            time.sleep(1.0)
    else:
        print("[FAIL] Backend server not responding on port 8000")
        sys.exit(1)

    # 1. Telemetry verification (Acceptance Criterion 3 & 4 snapshot)
    req = urllib.request.urlopen("http://127.0.0.1:8000/api/signals/telemetry")
    tel = json.loads(req.read().decode())
    print("\nTelemetry Response Summary:")
    print("Status:", tel.get("status"))
    print("Active Phase:", tel.get("active_phase"))
    print("Snapshot State:", tel.get("snapshot", {}).get("current_state"))
    print("Lanes in Snapshot:", list(tel.get("snapshot", {}).get("signals", {}).keys()))

    assert tel["status"] == "success", "Telemetry status must be success"
    assert "snapshot" in tel, "Must include snapshot object"
    snap = tel["snapshot"]
    assert "signals" in snap, "Snapshot must include signals dictionary"
    for lane_id in ["LANE_1", "LANE_2", "LANE_3", "LANE_4"]:
        assert lane_id in snap["signals"], f"{lane_id} missing in snapshot"
        sig = snap["signals"][lane_id]
        assert "state" in sig, f"Missing state in {lane_id}"
        assert "density" in sig, f"Missing density in {lane_id}"
        assert "action" in sig, f"Missing action in {lane_id}"
    print("[PASS] Criterion 3 & 4 (Snapshot Telemetry Schema) verified successfully.")

    # 2. Upload with camera_id query param (Acceptance Criterion 1)
    boundary = "----WebKitFormBoundaryXyZ123Test"
    file_content = b"fake MP4 video bytes for testing"
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(b'Content-Disposition: form-data; name="file"; filename="sample_lane2.mp4"\r\n')
    body.extend(b"Content-Type: video/mp4\r\n\r\n")
    body.extend(file_content)
    body.extend(f"\r\n--{boundary}--\r\n".encode("utf-8"))

    upload_req = urllib.request.Request(
        "http://127.0.0.1:8000/api/upload_video?camera_id=LANE_2",
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    up_res = json.loads(urllib.request.urlopen(upload_req).read().decode())
    print("\nUpload Response:", up_res)
    assert up_res.get("status") == "success", "Upload status must be success"
    assert up_res.get("camera_id") == "LANE_2", "Camera ID must be LANE_2"
    clean_path = up_res.get("file_path", "").replace("\\", "/")
    assert "uploads/LANE_2/" in clean_path, f"Upload path must be in uploads/LANE_2/, got: {clean_path}"
    assert os.path.exists(clean_path), f"File {clean_path} must exist on disk"
    print("[PASS] Criterion 1 (Camera-isolated upload under uploads/LANE_2/) verified successfully.")

    # 3. Stream Endpoint Isolation (Acceptance Criterion 2)
    # Stream from an uninitialized camera should yield fallback frames without throwing HTTP 500
    stream_req = urllib.request.Request("http://127.0.0.1:8000/api/stream/UNKNOWN_LANE_TEST")
    stream_conn = urllib.request.urlopen(stream_req, timeout=5)
    assert stream_conn.status == 200, f"Expected HTTP 200 for stream, got {stream_conn.status}"
    chunk = stream_conn.read(512)
    assert b"--frame" in chunk or b"image/jpeg" in chunk, "Stream should return multipart image/jpeg"
    stream_conn.close()
    print("[PASS] Criterion 2 (Stream isolation with fallback frame) verified successfully.")

    # 4. Speed noise filtering (Acceptance Criterion 5)
    from app.analytics.signal_allocator import format_speed
    assert format_speed(0.2, 10) == "Vehicle stopped"
    assert format_speed(2.4, 10) == "Not confirmed"
    assert format_speed(42.5, 10) == "42.5 km/h"
    assert format_speed(42.5, 3) == "Not enough data"
    print("[PASS] Criterion 5 (Speed noise floor filtering) verified successfully.")

    print("\n=======================================================")
    print("ALL 5 ACCEPTANCE CRITERIA VERIFIED AND PASSING (100%)!")
    print("=======================================================")

if __name__ == "__main__":
    run_tests()
