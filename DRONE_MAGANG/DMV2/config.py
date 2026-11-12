"""Konfigurasi terpusat aplikasi drone.

Semua nilai yang biasanya perlu diubah saat tuning (index kamera, port FC,
rentang HSV, dan port web) dikumpulkan di sini agar tidak tersebar di
banyak modul.
"""

# ---------------------------------------------------------------------------
# Kamera
# ---------------------------------------------------------------------------
CAMERA_INDEX = 0
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
CAMERA_FPS = 30
JPEG_QUALITY = 75

# ---------------------------------------------------------------------------
# Deteksi kotak merah
# ---------------------------------------------------------------------------
MIN_AREA = 8000
MORPH_KERNEL_SIZE = (5, 5)

# Rentang HSV merah (OpenCV: H 0-179, S 0-255, V 0-255).
# Warna acuan dari note.txt:
#   #6c111c, #7e111e, #ba2839, #ab1e2f  ->  H sekitar 175
# Rentang dibuat cukup lebar agar tahan terhadap bayangan dan sorotan.
RED_HSV_RANGES = (
    ((0, 170, 90), (10, 255, 240)),
    ((170, 170, 90), (180, 255, 240)),
)

# ---------------------------------------------------------------------------
# Flight Controller (MAVLink)
# ---------------------------------------------------------------------------
FC_PORT = "/dev/ttyACM0"
FC_BAUDRATE = 115200

# Rate stream MAVLink.
# - TELEMETRY_STREAM_HZ hanya fallback lama (REQUEST_DATA_STREAM_ALL).
# - Pesan utama (GLOBAL_POSITION_INT & VFR_HUD) diminta lebih cepat lewat
#   MAV_CMD_SET_MESSAGE_INTERVAL agar altitude/groundspeed tidak delay.
TELEMETRY_STREAM_HZ = 4
TELEMETRY_PRIMARY_HZ = 10
TELEMETRY_EXTRA_HZ = 2
TELEMETRY_RECV_TIMEOUT = 0.2

FC_RECONNECT_DELAY = 3.0
FC_STALE_TIMEOUT = 5.0

# ---------------------------------------------------------------------------
# Web server / dashboard
# ---------------------------------------------------------------------------
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8080
TELEMETRY_POLL_MS = 200

# ---------------------------------------------------------------------------
# WebSocket (push state; polling tetap menjadi fallback di dashboard)
# ---------------------------------------------------------------------------
WS_ENABLED = True
WS_HOST = SERVER_HOST
WS_PORT = 8081
WS_PUSH_HZ = 10
