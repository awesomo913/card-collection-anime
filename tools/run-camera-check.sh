#!/usr/bin/env bash
# Launcher for the Camera Check app (called by the desktop icon).
#
# Picks the interpreter the SCANNER actually uses when possible: if the backend
# venv can import picamera2 (i.e. it was created with system-site-packages, as
# pi-run-nosudo.sh ensures), a green result here proves THAT exact path sees the
# camera — not just "some Python can". Falls back to the system python3 (which
# has the apt python3-picamera2) otherwise.
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PY="$DIR/backend/venv/bin/python"

PY="python3"
if [ -x "$VENV_PY" ] && "$VENV_PY" -c "import picamera2" >/dev/null 2>&1; then
  PY="$VENV_PY"
fi

exec "$PY" "$DIR/tools/camera_check.py" "$@"
