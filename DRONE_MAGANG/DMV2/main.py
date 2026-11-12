"""Entry point aplikasi monitoring drone.

Menggabungkan:
  * kamera USB/CSI (OpenCV)          -> thread ``CameraWorker``
  * deteksi kotak merah              -> ``detection.detect_boxes``
  * telemetri Flight Controller      -> ``telemetry.TelemetryWorker``
  * web server + MJPEG + JSON API    -> ``DroneHTTPServer``
  * push state real-time             -> ``ws_server.WebSocketServer``

Jalankan dengan:
    python3 main.py

Lalu buka ``http://<IP_RASPBERRY_PI>:8080`` dari browser laptop.
Dashboard memakai WebSocket bila tersedia dan otomatis kembali ke polling
JSON bila koneksi WebSocket gagal.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn

import cv2

import config
from detection import detect_boxes
from telemetry import TelemetryWorker
from ws_server import WebSocketServer

BASE_DIR = Path(__file__).resolve().parent
DASHBOARD_PATH = BASE_DIR / "dashboard.html"


# ---------------------------------------------------------------------------
# Kamera
# ---------------------------------------------------------------------------
class CameraWorker(threading.Thread):
    """Membaca kamera sekali saja dan membagikan frame terakhir ke semua klien.

    Hanya thread ini yang menyentuh ``cv2.VideoCapture`` sehingga aman ketika
    banyak browser membuka ``/stream.mjpg`` bersamaan.
    """

    def __init__(
        self,
        camera_index=None,
        width=None,
        height=None,
        fps=None,
        jpeg_quality=None,
    ):
        super().__init__(name="CameraWorker", daemon=True)

        self.camera_index = (
            camera_index if camera_index is not None else config.CAMERA_INDEX
        )
        self.width = width if width is not None else config.FRAME_WIDTH
        self.height = height if height is not None else config.FRAME_HEIGHT
        self.fps = fps if fps is not None else config.CAMERA_FPS
        self.jpeg_quality = (
            jpeg_quality if jpeg_quality is not None else config.JPEG_QUALITY
        )

        self._cap = None
        self._stop_event = threading.Event()
        self._condition = threading.Condition()

        self._jpeg = None
        self._frame_id = 0
        self._detections = []
        self._frame_size = (0, 0)
        self._fps_measured = 0.0
        self._last_error = None
        self._last_detection_error = None

    # -- lifecycle ---------------------------------------------------------
    def open(self):
        cap = cv2.VideoCapture(self.camera_index)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)

        if not cap.isOpened():
            cap.release()
            raise RuntimeError(
                f"Kamera index {self.camera_index} tidak dapat dibuka. "
                "Periksa koneksi kamera dan izin grup 'video'."
            )

        self._cap = cap

    def stop(self):
        self._stop_event.set()
        with self._condition:
            self._condition.notify_all()

    def run(self):
        encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), int(self.jpeg_quality)]
        consecutive_failures = 0
        fps_window_start = time.time()
        fps_window_frames = 0

        try:
            while not self._stop_event.is_set():
                if self._cap is None:
                    try:
                        self.open()
                    except RuntimeError as exc:
                        self._last_error = str(exc)
                        print(f"[CAMERA] {exc}")
                        self._stop_event.wait(2.0)
                        continue

                ok, frame = self._cap.read()
                if not ok or frame is None:
                    consecutive_failures += 1
                    self._last_error = "Gagal membaca frame dari kamera"
                    if consecutive_failures == 30:
                        print("[CAMERA] Frame gagal berulang, membuka ulang kamera...")
                        self._release_capture()
                    self._stop_event.wait(0.05)
                    continue

                consecutive_failures = 0

                try:
                    annotated, detections = detect_boxes(frame)
                except Exception as exc:  # jangan sampai stream mati total
                    annotated = frame
                    detections = []
                    self._last_detection_error = f"Deteksi gagal: {exc}"
                else:
                    self._last_detection_error = None

                ok, buffer = cv2.imencode(".jpg", annotated, encode_params)
                if not ok:
                    continue

                now = time.time()
                fps_window_frames += 1
                if now - fps_window_start >= 1.0:
                    self._fps_measured = fps_window_frames / (now - fps_window_start)
                    fps_window_start = now
                    fps_window_frames = 0

                with self._condition:
                    self._jpeg = buffer.tobytes()
                    self._detections = detections
                    self._frame_size = (frame.shape[1], frame.shape[0])
                    self._frame_id += 1
                    self._last_error = None
                    self._condition.notify_all()
        finally:
            self._release_capture()

    def _release_capture(self):
        if self._cap is not None:
            try:
                self._cap.release()
            finally:
                self._cap = None

    # -- pembacaan state ---------------------------------------------------
    def wait_for_frame(self, last_frame_id, timeout=2.0):
        """Tunggu frame baru. Mengembalikan ``(frame_id, jpeg, detections)``."""
        with self._condition:
            if self._frame_id == last_frame_id:
                self._condition.wait(timeout)
            return self._frame_id, self._jpeg, list(self._detections)

    def get_snapshot(self):
        with self._condition:
            return self._jpeg

    def get_detections(self):
        with self._condition:
            return list(self._detections)

    def get_status(self):
        with self._condition:
            return {
                "opened": self._cap is not None,
                "frame_id": self._frame_id,
                "frame_width": self._frame_size[0],
                "frame_height": self._frame_size[1],
                "fps": round(self._fps_measured, 1),
                "detections": len(self._detections),
                "last_error": self._last_error,
                "last_detection_error": self._last_detection_error,
            }


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------
class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class StreamingHandler(BaseHTTPRequestHandler):
    server_version = "DroneMonitor/1.0"

    # -- helpers -----------------------------------------------------------
    @property
    def camera(self):
        return self.server.camera

    @property
    def telemetry(self):
        return self.server.telemetry

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body, content_type, status=200, cache="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    # -- routing -----------------------------------------------------------
    def do_GET(self):
        path = self.path.split("?", 1)[0]

        if path in ("/", "/dashboard.html", "/index.html"):
            self._serve_dashboard()
        elif path == "/stream.mjpg":
            self._serve_stream()
        elif path == "/telemetry.json":
            self._send_json(self.telemetry.get_state())
        elif path == "/detections.json":
            self._serve_detections()
        elif path == "/snapshot.jpg":
            self._serve_snapshot()
        elif path == "/health":
            self._send_json(
                {
                    "camera": self.camera.get_status(),
                    "telemetry": self.telemetry.get_state(),
                }
            )
        else:
            self.send_error(404, "Not Found")

    def _serve_dashboard(self):
        try:
            html = DASHBOARD_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            self._send_bytes(
                b"<h1>dashboard.html tidak ditemukan</h1>",
                "text/html; charset=utf-8",
                status=500,
            )
            return

        html = html.replace("{{TELEMETRY_POLL_MS}}", str(config.TELEMETRY_POLL_MS))
        html = html.replace("{{WS_PORT}}", str(config.WS_PORT))
        self._send_bytes(html.encode("utf-8"), "text/html; charset=utf-8")

    def _serve_stream(self):
        self.send_response(200)
        self.send_header("Age", "0")
        self.send_header("Cache-Control", "no-cache, private")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=FRAME")
        self.end_headers()

        last_frame_id = -1
        try:
            while True:
                frame_id, jpeg, _ = self.camera.wait_for_frame(last_frame_id, timeout=2.0)
                if jpeg is None or frame_id == last_frame_id:
                    continue
                last_frame_id = frame_id

                self.wfile.write(b"--FRAME\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(
                    f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii")
                )
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _serve_detections(self):
        detections = self.camera.get_detections()
        status = self.camera.get_status()
        self._send_json(
            {
                "count": len(detections),
                "boxes": detections,
                "frame_width": status["frame_width"],
                "frame_height": status["frame_height"],
                "timestamp": time.time(),
            }
        )

    def _serve_snapshot(self):
        jpeg = self.camera.get_snapshot()
        if jpeg is None:
            self.send_error(503, "Frame kamera belum tersedia")
            return
        self._send_bytes(jpeg, "image/jpeg", cache="no-cache")

    def log_message(self, fmt, *args):  # noqa: A003 - signature bawaan
        # Stream MJPEG membuat log sangat ramai; cukup diamkan.
        pass


class DroneHTTPServer(ThreadedHTTPServer):
    def __init__(self, address, handler, camera, telemetry):
        super().__init__(address, handler)
        self.camera = camera
        self.telemetry = telemetry


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    camera = CameraWorker()
    try:
        camera.open()
    except RuntimeError as exc:
        print(f"[ERROR] {exc}")
        return 1

    telemetry = TelemetryWorker()

    camera.start()
    telemetry.start()

    try:
        server = DroneHTTPServer(
            (config.SERVER_HOST, config.SERVER_PORT),
            StreamingHandler,
            camera=camera,
            telemetry=telemetry,
        )
    except OSError as exc:
        print(f"[ERROR] Gagal membuka port {config.SERVER_PORT}: {exc}")
        camera.stop()
        telemetry.stop()
        return 1

    ws_server = WebSocketServer(camera, telemetry)
    ws_server.start()

    print(f"[INFO] Dashboard: http://<IP_RASPBERRY_PI>:{config.SERVER_PORT}/")
    print(f"[INFO] Stream   : http://<IP_RASPBERRY_PI>:{config.SERVER_PORT}/stream.mjpg")
    print(f"[INFO] Telemetri: http://<IP_RASPBERRY_PI>:{config.SERVER_PORT}/telemetry.json")
    print(f"[INFO] Deteksi  : http://<IP_RASPBERRY_PI>:{config.SERVER_PORT}/detections.json")
    print("[INFO] Tekan Ctrl+C untuk berhenti.")

    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\n[INFO] Menutup aplikasi...")
    finally:
        ws_server.stop()
        server.server_close()
        camera.stop()
        telemetry.stop()
        camera.join(timeout=2.0)
        telemetry.join(timeout=2.0)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
