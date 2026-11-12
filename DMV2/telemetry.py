"""Worker telemetri MAVLink untuk aplikasi drone.

``TelemetryWorker`` berjalan sebagai thread terpisah, menyambung ulang ke
Flight Controller bila koneksi terputus, dan menyimpan state terakhir yang
aman dibaca dari thread lain lewat ``get_state()``.

Modul ini tidak melakukan koneksi saat di-import.
"""

import math
import threading
import time

import config

try:
    from pymavlink import mavutil
except ImportError:  # pragma: no cover - hanya untuk pesan error yang jelas
    mavutil = None


def _rpy_to_degrees(roll, pitch, yaw):
    return (
        math.degrees(roll),
        math.degrees(pitch),
        math.degrees(yaw) % 360.0,
    )


class TelemetryWorker(threading.Thread):
    """Thread pembaca telemetri MAVLink dengan auto-reconnect."""

    def __init__(
        self,
        port=None,
        baudrate=None,
        stream_hz=None,
        primary_hz=None,
        extra_hz=None,
        recv_timeout=None,
        reconnect_delay=None,
        stale_timeout=None,
    ):
        super().__init__(name="TelemetryWorker", daemon=True)

        self.port = port if port is not None else config.FC_PORT
        self.baudrate = baudrate if baudrate is not None else config.FC_BAUDRATE
        self.stream_hz = stream_hz if stream_hz is not None else config.TELEMETRY_STREAM_HZ
        self.primary_hz = (
            primary_hz if primary_hz is not None else config.TELEMETRY_PRIMARY_HZ
        )
        self.extra_hz = extra_hz if extra_hz is not None else config.TELEMETRY_EXTRA_HZ
        self.recv_timeout = (
            recv_timeout if recv_timeout is not None else config.TELEMETRY_RECV_TIMEOUT
        )
        self.reconnect_delay = (
            reconnect_delay if reconnect_delay is not None else config.FC_RECONNECT_DELAY
        )
        self.stale_timeout = (
            stale_timeout if stale_timeout is not None else config.FC_STALE_TIMEOUT
        )

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._master = None
        self._last_message_time = 0.0
        self._last_error = None

        self._state = {
            "connected": False,
            "stale": True,
            "last_update": None,
            "last_error": None,
            "altitude": None,          # meter, relatif terhadap home
            "altitude_source": None,   # "GLOBAL_POSITION_INT" / "VFR_HUD"
            "groundspeed": None,       # m/s
            "airspeed": None,          # m/s
            "throttle": None,          # %
            "heading": None,           # derajat
            "latitude": None,          # derajat
            "longitude": None,         # derajat
            "roll": None,              # derajat
            "pitch": None,             # derajat
            "yaw": None,               # derajat
            "battery_voltage": None,   # volt
            "battery_current": None,   # ampere
            "battery_remaining": None, # %
            "satellites": None,
            "fix_type": None,
            "armed": None,
            "mode": None,
        }

    # ------------------------------------------------------------------
    # Thread lifecycle
    # ------------------------------------------------------------------
    def stop(self):
        self._stop_event.set()
        master = self._master
        if master is not None:
            try:
                master.close()
            except Exception:
                pass

    def run(self):
        if mavutil is None:
            message = (
                "pymavlink tidak terpasang. Jalankan: pip install pymavlink"
            )
            print(f"[TELEMETRY] {message}")
            with self._lock:
                self._state["last_error"] = message
            return

        while not self._stop_event.is_set():
            try:
                self._connect_and_read()
            except Exception as exc:  # koneksi putus / port hilang
                self._last_error = str(exc)
                self._set_disconnected(str(exc))
                print(f"[TELEMETRY] Koneksi bermasalah: {exc}")

            if self._stop_event.is_set():
                break
            self._stop_event.wait(self.reconnect_delay)

    # ------------------------------------------------------------------
    # Koneksi
    # ------------------------------------------------------------------
    def _connect_and_read(self):
        print(f"[TELEMETRY] Menghubungkan ke {self.port} ({self.baudrate} bps)...")
        master = mavutil.mavlink_connection(self.port, baud=self.baudrate)
        self._master = master

        try:
            try:
                heartbeat = master.wait_heartbeat(timeout=self.stale_timeout)
            except TypeError:
                # Versi pymavlink lama tidak punya argumen timeout.
                heartbeat = master.wait_heartbeat()

            if heartbeat is None:
                raise ConnectionError(
                    f"Tidak ada heartbeat dari FC dalam {self.stale_timeout:.0f} detik"
                )
            if self._stop_event.is_set():
                return

            print(
                f"[TELEMETRY] FC terdeteksi (system={master.target_system}, "
                f"component={master.target_component})"
            )
            self._last_message_time = time.time()
            with self._lock:
                self._state["connected"] = True
                self._state["last_error"] = None

            self._request_streams(master)

            while not self._stop_event.is_set():
                msg = master.recv_match(blocking=True, timeout=self.recv_timeout)
                if msg is None:
                    self._check_stale()
                    continue

                if self._stop_event.is_set():
                    break

                self._last_message_time = time.time()
                self._handle_message(msg)
        finally:
            try:
                master.close()
            except Exception:
                pass
            if self._master is master:
                self._master = None

    def _request_streams(self, master):
        """Minta stream telemetri dengan rate tinggi untuk data utama.

        ``REQUEST_DATA_STREAM`` sudah deprecated tetapi masih didukung
        ArduPilot, jadi tetap dipakai sebagai fallback. Setelah itu
        ``MAV_CMD_SET_MESSAGE_INTERVAL`` dipakai untuk meminta pesan utama
        (altitude & groundspeed) jauh lebih cepat tanpa ikut mempercepat
        semua stream lain.
        """
        try:
            master.mav.request_data_stream_send(
                master.target_system,
                master.target_component,
                mavutil.mavlink.MAV_DATA_STREAM_ALL,
                int(self.stream_hz),
                1,
            )
        except Exception as exc:
            print(f"[TELEMETRY] Gagal meminta data stream: {exc}")

        # ID pesan bisa berbeda antar versi pymavlink; pakai getattr + nilai
        # standar MAVLink sebagai cadangan.
        message_ids = {
            "GLOBAL_POSITION_INT": getattr(
                mavutil.mavlink, "MAVLINK_MSG_ID_GLOBAL_POSITION_INT", 33
            ),
            "VFR_HUD": getattr(mavutil.mavlink, "MAVLINK_MSG_ID_VFR_HUD", 74),
            "ATTITUDE": getattr(mavutil.mavlink, "MAVLINK_MSG_ID_ATTITUDE", 30),
            "SYS_STATUS": getattr(mavutil.mavlink, "MAVLINK_MSG_ID_SYS_STATUS", 1),
            "GPS_RAW_INT": getattr(mavutil.mavlink, "MAVLINK_MSG_ID_GPS_RAW_INT", 24),
        }
        intervals = (
            ("GLOBAL_POSITION_INT", self.primary_hz),
            ("VFR_HUD", self.primary_hz),
            ("ATTITUDE", self.extra_hz),
            ("SYS_STATUS", self.extra_hz),
            ("GPS_RAW_INT", self.extra_hz),
        )

        command_id = getattr(
            mavutil.mavlink, "MAV_CMD_SET_MESSAGE_INTERVAL", 511
        )
        for name, hz in intervals:
            if hz <= 0:
                continue
            try:
                master.mav.command_long_send(
                    master.target_system,
                    master.target_component,
                    command_id,
                    0,
                    message_ids[name],
                    int(1_000_000 / hz),
                    0,
                    0,
                    0,
                    0,
                    0,
                )
            except Exception as exc:
                print(f"[TELEMETRY] Gagal set interval {name}: {exc}")

    # ------------------------------------------------------------------
    # Parsing pesan
    # ------------------------------------------------------------------
    def _handle_message(self, msg):
        msg_type = msg.get_type()
        if msg_type == "BAD_DATA":
            return

        updates = {}

        if msg_type == "VFR_HUD":
            updates["groundspeed"] = float(msg.groundspeed)
            updates["airspeed"] = float(msg.airspeed)
            updates["throttle"] = int(msg.throttle)
            updates["heading"] = float(msg.heading)
            # Fallback ketinggian bila GLOBAL_POSITION_INT belum ada.
            with self._lock:
                has_gpi_alt = self._state["altitude_source"] == "GLOBAL_POSITION_INT"
            if not has_gpi_alt:
                updates["altitude"] = float(msg.alt)
                updates["altitude_source"] = "VFR_HUD"

        elif msg_type == "GLOBAL_POSITION_INT":
            updates["latitude"] = msg.lat / 1e7
            updates["longitude"] = msg.lon / 1e7
            updates["altitude"] = msg.relative_alt / 1000.0
            updates["altitude_source"] = "GLOBAL_POSITION_INT"
            if msg.hdg != 65535:  # UINT16_MAX = tidak valid
                updates["heading"] = msg.hdg / 100.0

        elif msg_type == "ATTITUDE":
            roll, pitch, yaw = _rpy_to_degrees(msg.roll, msg.pitch, msg.yaw)
            updates["roll"] = roll
            updates["pitch"] = pitch
            updates["yaw"] = yaw

        elif msg_type == "SYS_STATUS":
            if msg.voltage_battery != 65535:
                updates["battery_voltage"] = msg.voltage_battery / 1000.0
            if msg.current_battery != -1:
                updates["battery_current"] = msg.current_battery / 100.0
            if msg.battery_remaining != -1:
                updates["battery_remaining"] = int(msg.battery_remaining)

        elif msg_type == "GPS_RAW_INT":
            if msg.satellites_visible != 255:
                updates["satellites"] = int(msg.satellites_visible)
            updates["fix_type"] = int(msg.fix_type)

        elif msg_type == "HEARTBEAT":
            # Hanya pesan dari autopilot, bukan dari GCS/perangkat lain.
            if msg.get_srcComponent() == mavutil.mavlink.MAV_COMP_ID_AUTOPILOT1:
                updates["armed"] = bool(
                    msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
                )
                updates["mode"] = mavutil.mode_string_v10(msg)

        if updates:
            with self._lock:
                self._state.update(updates)
                self._state["last_update"] = time.time()
                self._state["stale"] = False

        self._check_stale()

    def _check_stale(self):
        age = time.time() - self._last_message_time
        if age > self.stale_timeout:
            with self._lock:
                self._state["stale"] = True

    def _set_disconnected(self, error):
        with self._lock:
            self._state["connected"] = False
            self._state["stale"] = True
            self._state["last_error"] = error

    # ------------------------------------------------------------------
    # Pembacaan state
    # ------------------------------------------------------------------
    def get_state(self):
        """Salin state telemetri terkini (thread-safe)."""
        with self._lock:
            state = dict(self._state)

        last_update = state.get("last_update")
        if last_update is None:
            state["age"] = None
        else:
            state["age"] = round(time.time() - last_update, 2)

        state["stale"] = state["stale"] or (
            state["age"] is not None and state["age"] > self.stale_timeout
        )
        return state
