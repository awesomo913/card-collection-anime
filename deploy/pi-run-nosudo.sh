#!/usr/bin/env bash
# No-sudo runner: build the frontend (single-port mode) and launch uvicorn.
# Designed for environments where sudo isn't available — no apt, no systemd.
# Idempotent — re-running rebuilds the frontend and restarts uvicorn.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"

log() { printf "\033[1;36m[run]\033[0m %s\n" "$*"; }

cd "$ROOT"
log "git pull"
git pull --ff-only || true

# --- Backend venv (idempotent) ---
if [ ! -x backend/venv/bin/uvicorn ]; then
  log "creating backend venv"
  python3 -m venv backend/venv
  backend/venv/bin/pip install --upgrade --quiet pip wheel
  backend/venv/bin/pip install -r backend/requirements.txt
fi

# --- Frontend build (single-port mode: API base = same origin) ---
log "frontend build"
pushd frontend >/dev/null
echo 'REACT_APP_API_BASE_URL=' > .env.production
if [ ! -d node_modules ]; then
  npm install --silent --no-audit --no-fund
fi
CI=false npm run build
popd >/dev/null

# --- Free the port if anything's bound to it ---
if command -v fuser >/dev/null 2>&1; then
  fuser -k "${PORT}/tcp" 2>/dev/null || true
fi

# --- Launch uvicorn in foreground; Ctrl+C to stop ---
log "starting uvicorn on http://${HOST}:${PORT}"
LAN_IP="$(hostname --all-ip-addresses | awk '{print $1}')"
log "share with iPhone:  http://${LAN_IP}:${PORT}"
log "status dashboard:   http://${LAN_IP}:${PORT}/status"
exec backend/venv/bin/uvicorn main:app \
  --app-dir backend \
  --host "$HOST" \
  --port "$PORT"
