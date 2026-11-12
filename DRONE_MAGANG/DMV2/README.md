# DMV2 - Drone Monitor

Aplikasi monitoring drone berbasis Raspberry Pi:

- stream kamera MJPEG ke browser;
- deteksi kotak merah dengan OpenCV;
- telemetri MAVLink dari Flight Controller (altitude, groundspeed, baterai, GPS, attitude);
- dashboard web dengan push state lewat WebSocket dan fallback polling JSON.

## Menjalankan

```bash
uv sync
uv run python main.py
```

Buka `http://<IP_RASPBERRY_PI>:8080/`.

Konfigurasi utama berada di `config.py`: index kamera, port FC, rentang HSV,
port HTTP, dan port WebSocket.

## Endpoint

| Endpoint | Fungsi |
|---|---|
| `/` | Dashboard |
| `/stream.mjpg` | Live video MJPEG |
| `/snapshot.jpg` | Snapshot frame terakhir |
| `/telemetry.json` | Telemetri (fallback polling) |
| `/detections.json` | Hasil deteksi (fallback polling) |
| `/health` | Status kamera + telemetri |
| `ws://<host>:8081/ws` | Push state gabungan (WebSocket) |

Dashboard otomatis memakai WebSocket bila tersedia. Jika koneksi gagal,
dashboard kembali ke polling endpoint JSON di atas.
