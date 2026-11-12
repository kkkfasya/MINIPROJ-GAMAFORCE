import cv2
import numpy as np
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

class Kamera:
    def __init__(self, camera_index=0):
        self.cap = cv2.VideoCapture(camera_index)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_FPS, 30)

        if not self.cap.isOpened():
            raise RuntimeError(f"[ERROR] Kamera index {camera_index} tidak dapat dibuka.")

        # Kernel untuk operasi morfologi (pembersihan noise biner)
        self.kernel = np.ones((5, 5), np.uint8)

    def deteksi_box(self, frame, min_area=800):
        # 1. Konversi BGR ke ruang warna HSV
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        # 2. Definisikan batas warna merah (Merah berada di ujung awal dan akhir nilai Hue)
        lower_red2 = np.array([173, 170, 100])   # S diturunkan ke 170, V ke 80 agar tahan bayangan
        upper_red2 = np.array([180, 255, 240])  # V dinaikkan ke 210 agar tahan sorotan terang

        # Rentang cadangan (Jika ada gradasi merah yang sedikit menyeberang ke Hue 0-10)
        lower_red1 = np.array([0, 170, 100])
        upper_red1 = np.array([17, 255, 240])

        lower_red3 = np.array([10, 170, 120])
        upper_red3 = np.array([20, 255, 240])

        # 3. Gabungkan kedua masker warna merah
        mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
        mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
        # mask3 = 

        mask = cv2.bitwise_or(mask1, mask2)

        # 4. Operasi morfologi untuk menghilangkan derau/noise bintik-bintik kecil
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self.kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_DILATE, self.kernel)

        # 5. Cari kontur objek pada masker biner
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for c in contours:
            area = cv2.contourArea(c)
            # Saring objek berdasarkan luas minimum agar bintik kecil terabaikan
            if area > min_area:
                x, y, w, h = cv2.boundingRect(c)

                # Hitung titik pusat massa objek (Centroid)
                M = cv2.moments(c)
                if M["m00"] != 0:
                    cx = int(M["m10"] / M["m00"])
                    cy = int(M["m01"] / M["m00"])
                else:
                    cx, cy = x + w // 2, y + h // 2

                # Gambar Bounding Box hijau di sekeliling box merah
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

                # Gambar tanda silang (+) pada titik tengah objek
                cv2.drawMarker(frame, (cx, cy), (0, 0, 255), 
                               markerType=cv2.MARKER_CROSS, markerSize=12, thickness=2)

                # Tambahkan label teks koordinat dan status
                label = f"Box Merah [{cx}, {cy}]"
                cv2.putText(frame, label, (x, max(y - 10, 15)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

        return frame

    def get_frame(self):
        encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), 75]
        while True:
            ret, frame = self.cap.read()
            if not ret:
                continue

            # Jalankan algoritma deteksi sebelum frame di-encode ke JPEG
            frame = self.deteksi_box(frame)

            ret, buffer = cv2.imencode('.jpg', frame, encode_params)
            if not ret:
                continue
            
            # Menghasilkan byte JPEG secara kontinu
            yield buffer.tobytes()

    def release(self):
        if self.cap.isOpened():
            self.cap.release()


kamera = Kamera(camera_index=0)


class StreamingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            html = """<!DOCTYPE html>
<html>
<head>
    <title>Stream Deteksi Box - Raspberry Pi</title>
</head>
<body style="background:#111; color:white; text-align:center; padding-top:20px; font-family:sans-serif;">
    <h2>Live Deteksi Box Merah</h2>
    <img src="/stream.mjpg" width="640" height="480" style="border:2px solid #555; border-radius:8px;"/>
</body>
</html>"""
            self.wfile.write(html.encode('utf-8'))

        elif self.path == '/stream.mjpg':
            self.send_response(200)
            self.send_header('Age', '0')
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
            self.end_headers()
            try:
                for frame_bytes in kamera.get_frame():
                    self.wfile.write(b'--FRAME\r\n')
                    self.send_header('Content-Type', 'image/jpeg')
                    self.send_header('Content-Length', str(len(frame_bytes)))
                    self.end_headers()
                    self.wfile.write(frame_bytes)
                    self.wfile.write(b'\r\n')
            except (ConnectionResetError, BrokenPipeError):
                print("[INFO]: Klien browser ditutup")

        else:
            self.send_error(404)
            self.end_headers()


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


if __name__ == '__main__':
    PORT = 8080
    server = ThreadedHTTPServer(('0.0.0.0', PORT), StreamingHandler)
    print(f"[INFO] Server deteksi aktif di port {PORT}.")
    print("[INFO] Buka di browser laptop: http://<IP_RASPBERRY_PI>:8080")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] Menutup server...")
    finally:
        kamera.release()
        server.server_close()