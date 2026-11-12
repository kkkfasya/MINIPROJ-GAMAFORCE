"""Server WebSocket untuk push state gabungan ke dashboard.

Dashboard tetap memakai endpoint JSON sebagai fallback, jadi modul ini
opsional: bila paket ``websockets`` tidak tersedia atau port gagal dibuka,
aplikasi tetap berjalan dengan polling seperti sebelumnya.

Video tetap dikirim lewat MJPEG; WebSocket hanya membawa telemetri,
status kamera, dan hasil deteksi.
"""

import json
import threading
import time

import config

try:
    from websockets.exceptions import ConnectionClosed
    from websockets.sync.server import serve
except ImportError:  # pragma: no cover - fallback bila websockets tidak ada
    ConnectionClosed = None
    serve = None

if ConnectionClosed is not None:
    _WS_CLOSED = (ConnectionClosed,)
else:  # except () tidak menangkap apa pun
    _WS_CLOSED = ()


class StateHub(threading.Thread):
    """Membangun snapshot state secara berkala untuk semua klien WebSocket.

    Snapshot yang sama dipakai bersama oleh seluruh klien, sehingga biaya
    serialisasi JSON hanya sekali per interval, bukan sekali per klien.
    Pola ``wait_for_update`` sengaja mirip dengan ``CameraWorker`` agar
    klien selalu mendapat state terbaru tanpa antrian yang menumpuk.
    """

    def __init__(self, camera, telemetry, push_hz=None):
        super().__init__(name="StateHub", daemon=True)

        self.camera = camera
        self.telemetry = telemetry
        self.hz = push_hz if push_hz is not None else config.WS_PUSH_HZ
        self._interval = 1.0 / max(float(self.hz), 0.1)

        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self._version = 0
        self._payload = None

    def run(self):
        while not self._stop_event.wait(self._interval):
            self._publish()

    def stop(self):
        self._stop_event.set()
        with self._condition:
            self._condition.notify_all()

    def is_stopped(self):
        return self._stop_event.is_set()

    def wait_for_update(self, last_version, timeout=2.0):
        """Tunggu snapshot baru dan kembalikan ``(version, payload)``."""
        with self._condition:
            if self._version == last_version:
                self._condition.wait(timeout)
            return self._version, self._payload

    def _publish(self):
        try:
            detections = self.camera.get_detections()
            status = self.camera.get_status()
            now = time.time()
            payload = json.dumps(
                {
                    "type": "state",
                    "seq": self._version + 1,
                    "ts": now,
                    "telemetry": self.telemetry.get_state(),
                    "camera": status,
                    "detections": {
                        "count": len(detections),
                        "boxes": detections,
                        "frame_width": status.get("frame_width"),
                        "frame_height": status.get("frame_height"),
                        "timestamp": now,
                    },
                },
                separators=(",", ":"),
            )
        except Exception as exc:  # jangan sampai thread state mati
            print(f"[WS] Gagal membuat state: {exc}")
            return

        with self._condition:
            self._version += 1
            self._payload = payload
            self._condition.notify_all()


class WebSocketServer:
    """Membungkus server WebSocket sinkron agar cocok dengan arsitektur thread."""

    def __init__(self, camera, telemetry, host=None, port=None, push_hz=None):
        self.host = host if host is not None else config.WS_HOST
        self.port = int(port if port is not None else config.WS_PORT)
        self.hub = StateHub(camera, telemetry, push_hz=push_hz)

        self._server = None
        self._thread = None

    def start(self):
        """Mulai server. Mengembalikan ``True`` bila WebSocket aktif."""
        if not config.WS_ENABLED:
            return False

        if serve is None:
            print("[WS] websockets tidak terpasang; dashboard memakai polling.")
            return False

        if self._server is not None:
            return True

        try:
            self._server = serve(
                self._handle_client,
                self.host,
                self.port,
                compression=None,  # pesan kecil; deflate hanya membuang CPU
            )
            self.hub.start()
            self._thread = threading.Thread(
                name="WebSocketServer",
                target=self._server.serve_forever,
                daemon=True,
            )
            self._thread.start()
        except Exception as exc:
            print(
                f"[WS] Gagal mengaktifkan WebSocket di port {self.port}: {exc}. "
                "Dashboard memakai polling."
            )
            self.stop()
            return False

        print(f"[WS] Server aktif: ws://<IP_RASPBERRY_PI>:{self.port}/ws")
        return True

    def stop(self):
        """Hentikan hub dan server WebSocket dengan aman."""
        self.hub.stop()

        server = self._server
        self._server = None
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass

        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _handle_client(self, websocket):
        # 0 = versi awal hub, jadi klien baru menunggu snapshot pertama
        # tanpa busy-loop saat belum ada state yang dipublikasikan.
        last_version = 0
        try:
            while not self.hub.is_stopped():
                version, payload = self.hub.wait_for_update(last_version)

                if self.hub.is_stopped():
                    break
                if payload is None or version == last_version:
                    continue

                last_version = version
                websocket.send(payload)
        except _WS_CLOSED:
            # Klien menutup tab / koneksi.
            pass
        except Exception as exc:
            print(f"[WS] Koneksi klien berakhir: {exc}")
