#!/usr/bin/env python3
"""Camera Check — a one-click "does the Pi camera load?" self-test.

Why this exists:
- The /scan card scanner depends on the Pi camera (picamera2). Before chasing
  scanner bugs, you want a dead-simple yes/no on the HARDWARE itself. This app
  talks to picamera2 DIRECTLY (no FastAPI server in the loop), so it isolates
  the variable: camera works here but /scan fails → server problem; fails here
  → camera / ribbon / driver problem.

Design for maximum "it just launches":
- GUI is Tkinter only (ships with Pi OS desktop). The live view uses Tk 8.6's
  built-in PNG support fed by picamera2's own encoder — NO Pillow / OpenCV / Qt,
  so a missing wheel can't stop it from opening.
- "Live" preview is a capture-every-N-ms loop on Tk's event loop (no second GUI
  toolkit, no thread-safety minefield). Good enough to confirm the sensor is
  alive and showing motion.

Modes:
    python3 camera_check.py              # GUI
    python3 camera_check.py --headless   # no window: capture once, print
                                         # PASS/FAIL, save a PNG, exit 0/1
                                         # (use over SSH where there's no display)

Telemetry: a self-contained file logger (the workspace crash-logger lives at a
Windows home path that doesn't exist on the Pi) writes newline events to
logs/camera_check_*.log — state / boundary / perf / decision / crash — mirroring
camera_service.py's logging approach.
"""
from __future__ import annotations

import argparse
import base64
import io
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

APP_NAME = "CameraCheck"
PREVIEW_SIZE = (1024, 576)        # 16:9, displays without scaling on most screens
LIVE_INTERVAL_MS = 800            # capture cadence for the pseudo-live view
SNAPSHOT_NAME = "camera_check_last.png"

_PROJECT_ROOT = Path(__file__).resolve().parent.parent  # repo root
_LOG_DIR = _PROJECT_ROOT / "logs"


# ---------------------------------------------------------------------------
# Minimal self-contained logger (newline events). Never raises.
# ---------------------------------------------------------------------------

class _Log:
    def __init__(self) -> None:
        self._fh = None
        try:
            _LOG_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._fh = open(_LOG_DIR / f"camera_check_{stamp}.log", "a", encoding="utf-8")
        except OSError:
            self._fh = None  # logging is best-effort; never block the test
        self.event("state", "startup", {"argv": sys.argv})

    def event(self, level: str, msg: str, ctx: dict | None = None) -> None:
        line = f"{datetime.now().isoformat()} [{level}] {msg}"
        if ctx:
            line += f" {ctx}"
        print(line, file=sys.stderr)
        if self._fh:
            try:
                self._fh.write(line + "\n")
                self._fh.flush()
            except OSError:
                pass


LOG = _Log()


# ---------------------------------------------------------------------------
# Camera wrapper — picamera2 init + frame capture, with clear failure messages.
# ---------------------------------------------------------------------------

INSTALL_HINT = (
    "Camera not available. Checklist:\n"
    "  1. Ribbon cable seated (CSI port), Pi powered off when you re-seat it.\n"
    "  2. picamera2 installed:  sudo apt install -y python3-picamera2\n"
    "  3. Camera detected:      rpicam-hello --list-cameras   (or libcamera-hello)\n"
    "  4. If you just enabled it, reboot the Pi."
)


class CameraProbe:
    """Owns one Picamera2 instance. capture_png() returns PNG bytes."""

    def __init__(self) -> None:
        self._cam = None
        self.model = "unknown"

    def start(self) -> None:
        t0 = time.monotonic()
        # Lazy import so the GUI can still open and show a clean error if the
        # picamera2 package itself is missing.
        from picamera2 import Picamera2
        infos = Picamera2.global_camera_info()
        if not infos:
            raise RuntimeError("no cameras detected by libcamera")
        self.model = infos[0].get("Model", "camera")
        cam = Picamera2()
        cam.configure(cam.create_preview_configuration(
            main={"size": PREVIEW_SIZE, "format": "RGB888"}
        ))
        cam.start()
        time.sleep(0.4)  # let AE/AWB settle so the first frame isn't dark/green
        self._cam = cam
        LOG.event("boundary", "camera.start",
                  {"ok": True, "model": self.model,
                   "ms": round((time.monotonic() - t0) * 1000)})

    def capture_png(self) -> bytes:
        t0 = time.monotonic()
        buf = io.BytesIO()
        self._cam.capture_file(buf, format="png")  # picamera2 encodes PNG itself
        data = buf.getvalue()
        LOG.event("perf", "capture", {"bytes": len(data),
                                      "ms": round((time.monotonic() - t0) * 1000)})
        return data

    def close(self) -> None:
        if self._cam is not None:
            try:
                self._cam.close()
            except Exception:  # noqa: BLE001 — closing is best-effort
                pass
            self._cam = None


# ---------------------------------------------------------------------------
# Headless mode — capture once, print PASS/FAIL, exit code 0/1.
# ---------------------------------------------------------------------------

def run_headless() -> int:
    probe = CameraProbe()
    try:
        probe.start()
        png = probe.capture_png()
        out = _PROJECT_ROOT / "logs" / SNAPSHOT_NAME
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(png)
        print(f"CAMERA OK: {probe.model}, {PREVIEW_SIZE[0]}x{PREVIEW_SIZE[1]}, "
              f"snapshot saved to {out}")
        LOG.event("decision", "headless result", {"chose": "PASS"})
        return 0
    except Exception as exc:  # noqa: BLE001 — report any failure as FAIL
        LOG.event("crash", "headless capture failed",
                  {"error": str(exc), "trace": traceback.format_exc()})
        print("CAMERA FAILED: " + str(exc), file=sys.stderr)
        print(INSTALL_HINT, file=sys.stderr)
        return 1
    finally:
        probe.close()


# ---------------------------------------------------------------------------
# GUI mode — Tkinter window with a live-ish preview + OK/FAIL banner.
# ---------------------------------------------------------------------------

def run_gui() -> int:
    try:
        import tkinter as tk
        from tkinter import font as tkfont
    except Exception as exc:  # noqa: BLE001
        # No Tk → fall back to headless so a click still produces a result.
        LOG.event("decision", "no tkinter, falling back to headless",
                  {"error": str(exc)})
        return run_headless()

    probe = CameraProbe()
    state = {"live": True, "started": False, "img": None, "after_id": None}

    root = tk.Tk()
    root.title("Camera Check — Pi card scanner")
    root.configure(bg="#101418")
    root.geometry("1080x760")

    banner = tk.Label(root, text="Starting camera…", bg="#101418", fg="#dddddd",
                      font=tkfont.Font(size=20, weight="bold"))
    banner.pack(pady=(14, 4))

    detail = tk.Label(root, text="", bg="#101418", fg="#9fb3c8",
                      font=tkfont.Font(size=11), justify="center", wraplength=1000)
    detail.pack(pady=(0, 8))

    image_label = tk.Label(root, bg="#000000")
    image_label.pack(padx=12, pady=8)

    controls = tk.Frame(root, bg="#101418")
    controls.pack(pady=10)

    def set_ok(extra: str = "") -> None:
        banner.config(text="✅  CAMERA OK", fg="#39d98a")
        detail.config(text=f"{probe.model} · {PREVIEW_SIZE[0]}x{PREVIEW_SIZE[1]} · "
                           f"live preview below{(' · ' + extra) if extra else ''}")

    def set_fail(err: str) -> None:
        banner.config(text="❌  CAMERA FAILED", fg="#ff6b6b")
        detail.config(text=f"{err}\n\n{INSTALL_HINT}")
        image_label.config(image="", text="(no image)", fg="#666", height=10)

    def tick() -> None:
        """Capture one frame and show it; reschedule while live."""
        if not state["started"]:
            try:
                probe.start()
                state["started"] = True
                set_ok()
            except Exception as exc:  # noqa: BLE001
                LOG.event("crash", "gui camera start failed",
                          {"error": str(exc), "trace": traceback.format_exc()})
                set_fail(str(exc))
                return  # do not reschedule — nothing to capture
        try:
            png = probe.capture_png()
            photo = tk.PhotoImage(data=base64.b64encode(png).decode("ascii"))
            state["img"] = photo  # keep a ref so Tk doesn't GC it
            image_label.config(image=photo, text="")
        except Exception as exc:  # noqa: BLE001
            LOG.event("failure", "gui frame capture failed", {"error": str(exc)})
            set_fail(str(exc))
            return
        if state["live"]:
            state["after_id"] = root.after(LIVE_INTERVAL_MS, tick)

    def toggle_live() -> None:
        state["live"] = not state["live"]
        live_btn.config(text="Pause" if state["live"] else "Resume live")
        LOG.event("state", "live toggled", {"live": state["live"]})
        if state["live"]:
            tick()
        elif state["after_id"]:
            root.after_cancel(state["after_id"])

    def snap_now() -> None:
        """Force one capture + save a snapshot to disk for the record."""
        try:
            png = probe.capture_png()
            out = _PROJECT_ROOT / "logs" / SNAPSHOT_NAME
            out.write_bytes(png)
            photo = tk.PhotoImage(data=base64.b64encode(png).decode("ascii"))
            state["img"] = photo
            image_label.config(image=photo, text="")
            set_ok(f"snapshot saved → {out.name}")
        except Exception as exc:  # noqa: BLE001
            set_fail(str(exc))

    def open_scanner() -> None:
        """Bonus: open the full /scan page in the browser (server must be up)."""
        import webbrowser
        webbrowser.open("http://localhost:8000/scan")
        LOG.event("decision", "open scanner page", {"chose": "/scan"})

    def on_close() -> None:
        state["live"] = False
        if state["after_id"]:
            try:
                root.after_cancel(state["after_id"])
            except Exception:  # noqa: BLE001
                pass
        probe.close()
        LOG.event("state", "shutdown", None)
        root.destroy()

    live_btn = tk.Button(controls, text="Pause", width=12, command=toggle_live)
    live_btn.grid(row=0, column=0, padx=6)
    tk.Button(controls, text="Capture snapshot", width=16, command=snap_now).grid(row=0, column=1, padx=6)
    tk.Button(controls, text="Open Scanner (/scan)", width=18, command=open_scanner).grid(row=0, column=2, padx=6)
    tk.Button(controls, text="Close", width=10, command=on_close).grid(row=0, column=3, padx=6)

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(150, tick)  # start the capture loop once the window is up
    root.mainloop()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the Pi camera loads.")
    parser.add_argument("--headless", action="store_true",
                        help="No window: capture once, print PASS/FAIL, exit 0/1.")
    args = parser.parse_args()
    return run_headless() if args.headless else run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
