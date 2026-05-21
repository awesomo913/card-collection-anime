#!/usr/bin/env bash
# Drop a double-clickable "Camera Check" icon on the Pi desktop. Run once:
#   bash tools/install-camera-check.sh
# Idempotent — re-running rewrites the launcher with current paths.
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
chmod +x "$DIR/tools/run-camera-check.sh" "$DIR/tools/camera_check.py"

# Desktop folder (respect a localized XDG desktop dir if configured).
DESKTOP_DIR="${XDG_DESKTOP_DIR:-$HOME/Desktop}"
if command -v xdg-user-dir >/dev/null 2>&1; then
  DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$DESKTOP_DIR")"
fi
mkdir -p "$DESKTOP_DIR"

TARGET="$DESKTOP_DIR/CameraCheck.desktop"
cat > "$TARGET" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Camera Check
Comment=Verify the Pi camera loads for the card scanner
Exec=$DIR/tools/run-camera-check.sh
Icon=camera-photo
Terminal=false
Categories=Utility;
EOF
chmod +x "$TARGET"

# Pi OS (Wayland/labwc + PCManFM) refuses to run .desktop files it doesn't
# "trust"; this metadata key marks it trusted so the first double-click runs
# instead of popping the "untrusted application launcher" prompt.
gio set "$TARGET" metadata::trusted true 2>/dev/null || true

echo "Installed launcher: $TARGET"
echo "Double-click 'Camera Check' on the desktop to test the camera."
echo "Headless check (over SSH):  $DIR/tools/run-camera-check.sh --headless"
