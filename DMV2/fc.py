import time
from pymavlink import mavutil

# 1. Konfigurasi Port Fisik dan Baudrate
# Jika via USB Telem: '/dev/ttyACM0' atau '/dev/ttyUSB0'
# Jika via GPIO UART Raspberry Pi: '/dev/ttyAMA0' atau '/dev/ttyS0'
PORT = '/dev/ttyACM0'
BAUDRATE = 115200  # Sesuaikan dengan setting SERIALx_BAUD pada FC (umumnya 57600 atau 115200)

print(f"Menghubungkan ke Flight Controller di {PORT} ({BAUDRATE} bps)...")
master = mavutil.mavlink_connection(PORT, baud=BAUDRATE)

# 2. Tunggu Paket Heartbeat Pertama (Verifikasi Koneksi)
print("Menunggu Heartbeat dari FC...")
master.wait_heartbeat()
print(f"FC Terdeteksi! System ID: {master.target_system}, Component ID: {master.target_component}")

# 3. Minta Pengiriman Data Telemetri Tertentu (Data Stream Request)
# Meminta telemetri stream berkala pada frekuensi 4 Hz
master.mav.request_data_stream_send(
    master.target_system,
    master.target_component,
    mavutil.mavlink.MAV_DATA_STREAM_ALL,
    4,  # Frekuensi dalam Hz
    1   # Aktifkan (1 = Start, 0 = Stop)
)

print("-" * 60)
print("Mulai membaca data telemetri...")

try:
    while True:
        # Baca paket pesan MAVLink yang masuk (blocking dengan timeout)
        msg = master.recv_match(blocking=True, timeout=2.0)
        
        if not msg:
            print("Peringatan: Timeout, tidak ada data masuk dari FC.")
            continue

        msg_type = msg.get_type()

        # Baca Data Orientasi / Sikap Drone (Roll, Pitch, Yaw)
        if msg_type == 'ATTITUDE':
            roll = msg.roll       # Radian
            pitch = msg.pitch     # Radian
            yaw = msg.yaw         # Radian
            print(f"[ATTITUDE] Roll: {roll:.3f} rad | Pitch: {pitch:.3f} rad | Yaw: {yaw:.3f} rad")

        # Baca Kecepatan Wahana (VFR_HUD)
        elif msg_type == 'VFR_HUD':
            airspeed = msg.airspeed        # m/s
            groundspeed = msg.groundspeed  # m/s
            throttle = msg.throttle        # %
            print(f"[HUD] Airspeed: {airspeed:.2f} m/s | GS: {groundspeed:.2f} m/s | Gas: {throttle}%")


        # # Baca Data Posisi GPS Global
        # elif msg_type == 'GLOBAL_POSITION_INT':
        #     lat = msg.lat / 1e7       # Derajat latitude
        #     lon = msg.lon / 1e7       # Derajat longitude
        #     alt_rel = msg.relative_alt / 1000.0  # Meter di atas permukaan tanah
        #     hdg = msg.hdg / 100.0     # Derajat heading kompas
        #     print(f"[GPS] Lat: {lat:.6f} | Lon: {lon:.6f} | Alt Relatif: {alt_rel:.2f} m | Arah: {hdg:.1f}°")

        # # Baca Status Baterai
        # elif msg_type == 'SYS_STATUS':
        #     voltage = msg.voltage_battery / 1000.0  # Volt
        #     current = msg.current_battery / 100.0   # Ampere
        #     remaining = msg.battery_remaining       # Persentase (%)
        #     print(f"[BATERAI] Tegangan: {voltage:.2f} V | Arus: {current:.2f} A | Sisa: {remaining}%")


        time.sleep(0.01)

except KeyboardInterrupt:
    print("\nProgram dihentikan oleh pengguna.")