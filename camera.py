# camera.py
# Reads the webcam in the background and keeps the latest frame, so the
# Vision window can show a live stream (served as MJPEG by AURA's local web
# server) and AURA can crop exactly what you clicked on to identify it.
# The camera is only on while the Vision window is open - closing it
# releases the webcam completely (the camera light goes off).
import time
import threading
import cv2

CAMERA_INDEX = 0
WIDTH, HEIGHT = 1280, 720
JPEG_QUALITY = 80

_lock = threading.Lock()
_frame = None        # latest frame (BGR numpy array)
_jpeg = None         # latest frame as JPEG bytes (for the stream)
_frame_id = 0
_running = False
_thread = None
_error = None


def _capture_loop():
    global _frame, _jpeg, _frame_id, _running, _error
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)  # DirectShow opens fastest on Windows
    if not cap.isOpened():
        _error = "I couldn't open your camera - is another app (Zoom, Teams, the Camera app) using it?"
        _running = False
        return
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
    _error = None
    try:
        while _running:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.02)
                continue
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            with _lock:
                _frame = frame
                _jpeg = buf.tobytes() if ok else _jpeg
                _frame_id += 1
    finally:
        cap.release()
        with _lock:
            _frame, _jpeg = None, None


def start():
    """Turns the camera on (no-op if already on). Returns (ok, error)."""
    global _running, _thread
    if _running and _thread and _thread.is_alive():
        return True, None
    _running = True
    _thread = threading.Thread(target=_capture_loop, daemon=True)
    _thread.start()
    # wait for the first frame (opening a webcam takes ~2 seconds)
    deadline = time.time() + 6
    while time.time() < deadline:
        if _error:
            return False, _error
        with _lock:
            if _jpeg is not None:
                return True, None
        time.sleep(0.05)
    return False, _error or "The camera didn't send a picture - try closing other apps that use it."


def stop():
    """Turns the camera off and releases it."""
    global _running
    _running = False


def is_on():
    return _running


def latest_frame():
    """(frame, frame_id) - a copy of the newest frame, or (None, 0)."""
    with _lock:
        return (None if _frame is None else _frame.copy()), _frame_id


def latest_jpeg():
    with _lock:
        return _jpeg, _frame_id


def mjpeg_stream(write, is_open=lambda: True):
    """Writes frames as a multipart MJPEG stream until the camera stops or
    the viewer disconnects. `write` is the HTTP response's write()."""
    last_id = -1
    while _running and is_open():
        jpeg, frame_id = latest_jpeg()
        if jpeg is None or frame_id == last_id:
            time.sleep(0.01)
            continue
        last_id = frame_id
        write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n")
        write(jpeg)
        write(b"\r\n")
