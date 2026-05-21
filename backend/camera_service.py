"""Raspberry Pi camera capture for the /scan card scanner.

Concerns split:
- This module owns the HARDWARE: the picamera2 lifecycle, the still-burst
  capture used for rarity analysis, and the MJPEG preview stream the browser
  shows while you frame a card. It knows nothing about cards, rarity, or prices.
- ``scan_service.py`` orchestrates capture -> identify -> rarity -> price.

Design notes:
- picamera2 is the apt package ``python3-picamera2`` — NOT pip-installable into
  an isolated venv (see deploy/pi-run-nosudo.sh, which flips the venv to use
  system-site-packages). We lazy-import it so this whole backend still boots on
  a dev laptop or a Pi with no camera; ``is_available()`` reports the truth and
  the /scan endpoints 503 cleanly when it's False.
- One camera, one consumer. The preview loop and a capture burst both touch the
  same hardware, so a single lock serialises them. Preview acquires the lock
  per-FRAME (and releases between frames), so a capture burst preempts after at
  most one preview frame, holds the lock for the whole burst, then preview
  resumes — "capture pauses preview" without killing the stream.
- Dev/off-Pi: set ``SCAN_FAKE_CAMERA_DIR`` to a folder of card photos and the
  whole flow runs headless against those fixtures (preview cycles them, capture
  returns them) so the scanner is testable with no hardware.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

# (bytes, mime) — same shape the DeepSeek client + identify_service consume.
Frame = Tuple[bytes, str]

# Capture tuning. Burst frames are HIGH quality on purpose: the rarity CV layer
# reads fine foil-line/sparkle signal that JPEG compression destroys at low Q.
_BURST_FRAMES = int(os.environ.get("SCAN_BURST_FRAMES", "9"))
_BURST_INTERVAL_S = float(os.environ.get("SCAN_BURST_INTERVAL_S", "0.25"))
_BURST_JPEG_QUALITY = int(os.environ.get("SCAN_BURST_QUALITY", "95"))
_PREVIEW_JPEG_QUALITY = int(os.environ.get("SCAN_PREVIEW_QUALITY", "70"))
_PREVIEW_MAX_FPS = float(os.environ.get("SCAN_PREVIEW_FPS", "10"))
# Main stream resolution. 1536x864 (16:9) keeps the foil signal crisp without a
# huge per-frame payload; override on a beefier rig.
_CAPTURE_W = int(os.environ.get("SCAN_CAPTURE_W", "1536"))
_CAPTURE_H = int(os.environ.get("SCAN_CAPTURE_H", "864"))


class CameraUnavailableError(RuntimeError):
    """No usable camera (no picamera2, no hardware, and no fake-camera dir)."""


def _fake_camera_dir() -> Optional[Path]:
    """Return the fixture-frames dir from SCAN_FAKE_CAMERA_DIR, if set + real."""
    raw = os.environ.get("SCAN_FAKE_CAMERA_DIR")
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_dir() else None


def _picamera2_importable() -> bool:
    try:
        import picamera2  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 — any import/runtime failure = unavailable
        return False


def is_available() -> bool:
    """True when /scan can capture: a real picamera2 OR a fake-camera dir."""
    return _fake_camera_dir() is not None or _picamera2_importable()


def availability_detail() -> str:
    """Human-readable reason string for the 503 body / status line."""
    if _fake_camera_dir() is not None:
        return f"fake camera (SCAN_FAKE_CAMERA_DIR={os.environ.get('SCAN_FAKE_CAMERA_DIR')})"
    if _picamera2_importable():
        return "picamera2 available"
    return (
        "picamera2 not importable and SCAN_FAKE_CAMERA_DIR unset. On the Pi: "
        "sudo apt install -y python3-picamera2 and ensure the venv uses "
        "system-site-packages (deploy/pi-run-nosudo.sh does this)."
    )


# ---------------------------------------------------------------------------
# Fake camera (dev/off-Pi) — serves fixture image files as frames.
# ---------------------------------------------------------------------------

_FIXTURE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def _load_fixture_frames() -> List[Frame]:
    d = _fake_camera_dir()
    if d is None:
        return []
    files = sorted(p for p in d.iterdir()
                   if p.is_file() and p.suffix.lower() in _FIXTURE_SUFFIXES)
    frames: List[Frame] = []
    for p in files:
        try:
            frames.append((p.read_bytes(), _mime_for(p.suffix)))
        except OSError as exc:
            logger.warning("fixture frame unreadable %s: %s", p, exc)
    return frames


def _mime_for(suffix: str) -> str:
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
    }.get(suffix.lower(), "image/jpeg")


# ---------------------------------------------------------------------------
# Real camera — a lazily-created picamera2 singleton behind one lock.
# ---------------------------------------------------------------------------

class _PiCamera:
    """Thin singleton wrapper around one Picamera2 instance."""

    _instance: Optional["_PiCamera"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._cam = None
        self._started = False
        self._access = threading.Lock()  # serialises preview frames vs burst

    @classmethod
    def get(cls) -> "_PiCamera":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = _PiCamera()
            return cls._instance

    def _ensure_started(self) -> None:
        if self._started:
            return
        # picamera2 import is gated by is_available() upstream, but the hardware
        # init (Picamera2(), configure, start) can still fail on a misconfigured
        # or busy camera. Translate ANY init failure into CameraUnavailableError
        # (logged here) so the endpoint returns a clean 503 instead of a raw 500.
        try:
            from picamera2 import Picamera2  # lazy — only on the Pi
            cam = Picamera2()
            config = cam.create_video_configuration(
                main={"size": (_CAPTURE_W, _CAPTURE_H), "format": "RGB888"}
            )
            cam.configure(config)
            cam.start()
            # Let auto-exposure/AWB settle so the first frames aren't dark/green.
            time.sleep(0.5)
        except Exception as exc:  # noqa: BLE001 — surface as a clean 503
            logger.error("picamera2 init failed: %s", exc)
            raise CameraUnavailableError(f"camera init failed: {exc}") from exc
        self._cam = cam
        self._started = True
        logger.info("picamera2 started size=%sx%s", _CAPTURE_W, _CAPTURE_H)

    def _encode_jpeg(self, array, quality: int) -> Optional[bytes]:
        import cv2  # lazy
        # picamera2 RGB888 main stream is RGB-ordered; cv2 encodes BGR, so swap
        # to keep colours true (matters for the champagne-gold name detection).
        bgr = cv2.cvtColor(array, cv2.COLOR_RGB2BGR)
        ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes() if ok else None

    def capture_burst(self, n: int) -> List[Frame]:
        with self._access:
            self._ensure_started()
            frames: List[Frame] = []
            for i in range(n):
                arr = self._cam.capture_array("main")
                jpg = self._encode_jpeg(arr, _BURST_JPEG_QUALITY)
                if jpg is not None:
                    frames.append((jpg, "image/jpeg"))
                if i < n - 1:
                    time.sleep(_BURST_INTERVAL_S)
            return frames

    def grab_preview_jpeg(self) -> Optional[bytes]:
        # Per-frame lock acquire so a burst can preempt between preview frames.
        with self._access:
            self._ensure_started()
            arr = self._cam.capture_array("main")
            return self._encode_jpeg(arr, _PREVIEW_JPEG_QUALITY)


# ---------------------------------------------------------------------------
# Public capture API (used by scan_service + the /scan endpoints).
# ---------------------------------------------------------------------------

def capture_burst(n: int = _BURST_FRAMES) -> List[Frame]:
    """Capture a short still-burst (the user tilts the card during it).

    Returns a list of (jpeg_bytes, "image/jpeg"). Raises CameraUnavailableError
    when there's no camera + no fixture dir. Never returns an empty list
    silently — an empty capture raises so the caller surfaces a real error.
    """
    started = time.monotonic()
    if _fake_camera_dir() is not None:
        frames = _load_fixture_frames()
        source = "fake"
    elif _picamera2_importable():
        frames = _PiCamera.get().capture_burst(n)
        source = "picamera2"
    else:
        raise CameraUnavailableError(availability_detail())

    elapsed_ms = (time.monotonic() - started) * 1000.0
    logger.info(
        "scan.capture source=%s frames=%s ms=%.0f ok=%s",
        source, len(frames), elapsed_ms, bool(frames),
    )
    if not frames:
        raise CameraUnavailableError(
            f"camera produced no frames (source={source}); check the camera/fixtures"
        )
    return frames


def preview_mjpeg() -> Iterator[bytes]:
    """Yield multipart/x-mixed-replace JPEG chunks for an <img> preview.

    Generator: FastAPI's StreamingResponse pumps it until the client
    disconnects. Raises CameraUnavailableError before the first yield when no
    camera/fixtures exist (the endpoint translates that to a 503).
    """
    boundary = b"--frame\r\n"
    min_interval = 1.0 / max(1.0, _PREVIEW_MAX_FPS)

    if _fake_camera_dir() is not None:
        fixtures = _load_fixture_frames()
        if not fixtures:
            raise CameraUnavailableError("fake camera dir has no usable images")
        idx = 0
        while True:
            data, _mime = fixtures[idx % len(fixtures)]
            idx += 1
            yield boundary + b"Content-Type: image/jpeg\r\n\r\n" + data + b"\r\n"
            time.sleep(max(min_interval, 0.5))  # slow cycle for fixtures
        return  # unreachable, keeps intent explicit

    if not _picamera2_importable():
        raise CameraUnavailableError(availability_detail())

    cam = _PiCamera.get()
    while True:
        t0 = time.monotonic()
        jpg = cam.grab_preview_jpeg()
        if jpg is not None:
            yield boundary + b"Content-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
        # Throttle to the target FPS, accounting for capture+encode time.
        spent = time.monotonic() - t0
        if spent < min_interval:
            time.sleep(min_interval - spent)
