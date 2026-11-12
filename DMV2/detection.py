"""Deteksi kotak merah berbasis OpenCV.

Modul ini sengaja tidak melakukan apa pun saat di-import (tanpa membuka
kamera atau menjalankan server) supaya aman dipakai oleh ``main.py``.
Logika HSV/kontur diambil dari ``deteksi.py`` dan seluruh parameter
diambil dari ``config.py``.
"""

import cv2
import numpy as np

import config


def _build_kernel():
    size = config.MORPH_KERNEL_SIZE
    if isinstance(size, int):
        size = (size, size)
    return np.ones(size, np.uint8)


_KERNEL = _build_kernel()


def detect_boxes(frame, min_area=None):
    """Deteksi kotak merah pada ``frame`` (BGR).

    Args:
        frame: Frame BGR dari OpenCV.
        min_area: Luas kontur minimum. Default ``config.MIN_AREA``.

    Returns:
        Tuple ``(annotated_frame, detections)``. ``annotated_frame`` adalah
        salinan frame dengan kotak hijau, tanda silang, dan label. ``detections``
        adalah list dict berisi ``x``, ``y``, ``w``, ``h``, ``area``,
        ``cx``, ``cy`` untuk setiap objek yang lolos filter.
    """
    if min_area is None:
        min_area = config.MIN_AREA

    annotated = frame.copy()
    detections = []

    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    mask = None
    for lower, upper in config.RED_HSV_RANGES:
        lower = np.array(lower, dtype=np.uint8)
        upper = np.array(upper, dtype=np.uint8)
        part = cv2.inRange(hsv, lower, upper)
        mask = part if mask is None else cv2.bitwise_or(mask, part)

    if mask is None:  # RED_HSV_RANGES kosong
        return annotated, detections

    # Hilangkan noise bintik lalu rapikan objek.
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, _KERNEL)
    mask = cv2.morphologyEx(mask, cv2.MORPH_DILATE, _KERNEL)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area:
            continue

        x, y, w, h = cv2.boundingRect(contour)

        moments = cv2.moments(contour)
        if moments["m00"] != 0:
            cx = int(moments["m10"] / moments["m00"])
            cy = int(moments["m01"] / moments["m00"])
        else:
            cx, cy = x + w // 2, y + h // 2

        detections.append(
            {
                "x": int(x),
                "y": int(y),
                "w": int(w),
                "h": int(h),
                "area": float(area),
                "cx": cx,
                "cy": cy,
            }
        )

        cv2.rectangle(annotated, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.drawMarker(
            annotated,
            (cx, cy),
            (0, 0, 255),
            markerType=cv2.MARKER_CROSS,
            markerSize=12,
            thickness=2,
        )
        label = f"Box Merah [{cx}, {cy}]"
        cv2.putText(
            annotated,
            label,
            (x, max(y - 10, 15)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 255, 0),
            2,
        )

    return annotated, detections
