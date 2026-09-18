import cv2
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI()

# Shared frame buffer continuously updated by main.py loop
latest_frame_bytes = None

def generate_mjpeg_stream():
    global latest_frame_bytes
    while True:
        if latest_frame_bytes is not None:
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + latest_frame_bytes + b"\r\n"
            )

@app.get("/api/video_feed/{camera_id}")
async def video_feed(camera_id: str):
    return StreamingResponse(
        generate_mjpeg_stream(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )
