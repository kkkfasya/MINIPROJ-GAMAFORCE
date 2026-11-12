import cv2
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

    def get_frame(self):
        encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), 75]
        while True:
            ret, frame = self.cap.read()
            if not ret:
                continue
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
            # Halaman penampil untuk browser laptop
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            html = """<!DOCTYPE html>
<html>
<head>
    <title>Stream Kamera Raspberry Pi</title>
</head>
<body style="background:#111; color:white; text-align:center; padding-top:20px; font-family:sans-serif;">
    <h2>Live Kamera Raspberry Pi</h2>
    <img src="/stream.mjpg" width="640" height="480" style="border:2px solid #555; border-radius:8px;"/>
</body>
</html>"""
            self.wfile.write(html.encode('utf-8'))

        elif self.path == '/stream.mjpg':
            # Mengalirkan byte MJPEG langsung ke browser
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
                print("[INFO]: Klien ditutup")

        else:
            self.send_error(404)
            self.end_headers()

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

if __name__ == '__main__':
    PORT = 8080
    server = ThreadedHTTPServer(('0.0.0.0', PORT), StreamingHandler)
    print(f"[INFO] Server aktif di port {PORT}.")
    print("[INFO] Jalankan di browser laptop: http://<IP_RASPBERRY_PI>:8080")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] Menutup server...")
    finally:
        kamera.release()
        server.server_close()