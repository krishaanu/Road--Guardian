import os
import sys
import time
import logging
from typing import Any, Dict, Optional, Tuple, Union
import cv2

logger = logging.getLogger("SourceResolver")


def resolve_camera_source(raw_source: Any) -> Dict[str, Any]:
    """
    Classifies and normalizes a configured camera source into:
      - webcam: integer device index (0, 1, ...)
      - file: normalized local file path (.mp4, .avi, .mkv, .mov)
      - network: RTSP / HTTP / HTTPS / RTMP stream URL

    Returns a structured dictionary:
      {
        "source_type": "webcam" | "file" | "network" | "invalid",
        "resolved_source": int | str,
        "display_name": str,
        "is_valid": bool,
        "error": Optional[str],
        "message": str
      }
    """
    if raw_source is None or (isinstance(raw_source, str) and not raw_source.strip()):
        return {
            "source_type": "invalid",
            "resolved_source": "",
            "display_name": "Empty source",
            "is_valid": False,
            "error": "No stream source provided",
            "message": "Source cannot be empty",
        }

    # 1. Check if numeric or integer string -> Webcam Device Index
    if isinstance(raw_source, int):
        idx = raw_source
        return {
            "source_type": "webcam",
            "resolved_source": idx,
            "display_name": f"Webcam device {idx}",
            "is_valid": True,
            "error": None,
            "message": f"Webcam device index {idx}",
        }

    source_str = str(raw_source).strip().replace("\\", "/").strip('"\'')

    if source_str.isdigit():
        idx = int(source_str)
        return {
            "source_type": "webcam",
            "resolved_source": idx,
            "display_name": f"Webcam device {idx}",
            "is_valid": True,
            "error": None,
            "message": f"Webcam device index {idx}",
        }

    # 2. Check if Network / IP Stream URL
    lowered = source_str.lower()
    if lowered.startswith(("rtsp://", "http://", "https://", "rtmp://")):
        return {
            "source_type": "network",
            "resolved_source": source_str,
            "display_name": "Stream URL",
            "is_valid": True,
            "error": None,
            "message": f"Network stream: {source_str[:30]}...",
        }

    # 3. Otherwise treat as Local File Path
    clean_path = os.path.normpath(source_str).replace("\\", "/")
    
    # Resolve relative paths against project root and upload directories
    if not os.path.exists(clean_path):
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
        # Candidate resolution paths
        candidate_paths = [
            os.path.join(project_root, clean_path),
            os.path.join(project_root, "uploads", clean_path),
            os.path.join(project_root, "uploads", os.path.basename(clean_path)),
            os.path.join(project_root, "data", "uploads", clean_path),
            os.path.join(project_root, "data", "uploads", os.path.basename(clean_path)),
            os.path.join(project_root, os.path.basename(clean_path)),
            os.path.join(os.path.expanduser("~"), "Downloads", os.path.basename(clean_path)),
        ]
        
        # Also check inside lane subdirectories (e.g. uploads/LANE_1/, uploads/LANE_2/, etc.)
        uploads_base = os.path.join(project_root, "uploads")
        if os.path.exists(uploads_base):
            for root_dir, _, files in os.walk(uploads_base):
                for f in files:
                    if f.lower() == os.path.basename(clean_path).lower():
                        candidate_paths.append(os.path.join(root_dir, f))

        found = False
        for cand in candidate_paths:
            norm_cand = os.path.normpath(cand).replace("\\", "/")
            if os.path.exists(norm_cand) and os.path.isfile(norm_cand):
                clean_path = norm_cand
                found = True
                break
        
        if not found:
            # Check if any .mp4 file exists in uploads/ as a helpful sample fallback
            if os.path.exists(uploads_base):
                for root_dir, _, files in os.walk(uploads_base):
                    for f in files:
                        if f.lower().endswith((".mp4", ".avi", ".mkv", ".mov")):
                            clean_path = os.path.normpath(os.path.join(root_dir, f)).replace("\\", "/")
                            found = True
                            break
                    if found:
                        break

        if not found:
            return {
                "source_type": "file",
                "resolved_source": clean_path,
                "display_name": "Local file",
                "is_valid": False,
                "error": f"File not found on server disk: {clean_path}",
                "message": f"File not found: {os.path.basename(clean_path)}",
            }

    ext = os.path.splitext(clean_path)[1].lower()
    valid_exts = [".mp4", ".avi", ".mkv", ".mov"]
    if ext not in valid_exts:
        return {
            "source_type": "file",
            "resolved_source": clean_path,
            "display_name": "Local file",
            "is_valid": False,
            "error": f"Unsupported video format '{ext}'. Expected one of: {', '.join(valid_exts)}",
            "message": f"Unsupported video format '{ext}'",
        }

    return {
        "source_type": "file",
        "resolved_source": clean_path,
        "display_name": "Local file",
        "is_valid": True,
        "error": None,
        "message": f"Local file: {os.path.basename(clean_path)}",
    }


def open_capture_handle(
    resolved_source: Union[int, str],
    source_type: str
) -> Tuple[Optional[cv2.VideoCapture], Optional[str]]:
    """
    Safely opens a cv2.VideoCapture handle with backend-appropriate flags:
      - webcam: uses cv2.CAP_DSHOW on Windows to prevent freezes and index mapping issues.
      - file / network: opens with standard path / URL decoding.

    Returns:
      (cap, error_reason)
      If successful, error_reason is None.
      If failed, cap is None and error_reason is a clear diagnostic string.
    """
    cap = None
    try:
        if source_type == "webcam":
            if not isinstance(resolved_source, int):
                try:
                    resolved_source = int(resolved_source)
                except ValueError:
                    return None, f"Invalid webcam index: {resolved_source} (must be integer)"

            if os.name == "nt":
                try:
                    cap = cv2.VideoCapture(resolved_source, cv2.CAP_DSHOW)
                    if not cap or not cap.isOpened():
                        if cap is not None:
                            try:
                                cap.release()
                            except Exception:
                                pass
                        cap = cv2.VideoCapture(resolved_source)
                except Exception as dshow_err:
                    logger.warning(f"CAP_DSHOW open failed for device #{resolved_source}: {dshow_err}")
                    cap = cv2.VideoCapture(resolved_source)
            else:
                cap = cv2.VideoCapture(resolved_source)

            if not cap or not cap.isOpened():
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
                return None, f"Webcam #{resolved_source} cannot be opened (device busy or unplugged)"

            # Verify initial frame read
            try:
                test_ret, _ = cap.read()
                if not test_ret:
                    try:
                        cap.release()
                    except Exception:
                        pass
                    return None, f"Webcam #{resolved_source} opened but returned no video frame"
            except Exception as test_err:
                try:
                    cap.release()
                except Exception:
                    pass
                return None, f"Webcam #{resolved_source} error reading frame: {test_err}"

        elif source_type == "network":
            cap = cv2.VideoCapture(str(resolved_source))
            if not cap or not cap.isOpened():
                if cap is not None:
                    cap.release()
                return None, f"Could not connect to network stream: {resolved_source}"

        else:  # file
            path_str = str(resolved_source)
            if not os.path.exists(path_str):
                return None, f"File not found on disk: {path_str}"

            cap = cv2.VideoCapture(path_str)
            if not cap or not cap.isOpened():
                if cap is not None:
                    cap.release()
                return None, f"OpenCV failed to decode video file: {os.path.basename(path_str)} (unsupported codec or corrupted file)"

        return cap, None

    except Exception as err:
        logger.error(f"Exception while opening capture handle for {resolved_source}: {err}", exc_info=True)
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        return None, f"Capture initialization error: {str(err)}"


def release_capture_handle(cap: Optional[cv2.VideoCapture]):
    """Safely releases a cv2.VideoCapture handle."""
    if cap is not None:
        try:
            cap.release()
            time.sleep(0.05)
        except Exception as e:
            logger.warning(f"Error releasing VideoCapture handle: {e}")
