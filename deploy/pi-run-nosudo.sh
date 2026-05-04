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

# --- Apply pending Alembic migrations (idempotent: no-op if already at head) ---
log "alembic upgrade head"
(cd backend && ../backend/venv/bin/alembic upgrade head) || \
  log "alembic upgrade failed (continuing — likely no migrations or fresh DB)"

# --- Node.js (user-space via fnm if apt-installed npm is missing) ---
# Pi OS Bookworm doesn't ship npm by default, and we have no sudo. fnm is a
# single static binary that drops Node into ~/.local/share/fnm/, no root needed.
FNM_DIR="$HOME/.local/share/fnm"
export PATH="$FNM_DIR:$PATH"
if ! command -v npm >/dev/null 2>&1; then
  if [ ! -x "$FNM_DIR/fnm" ]; then
    log "installing fnm (user-space Node manager)"
    mkdir -p "$FNM_DIR"
    # Resolve fnm's latest arm64 release tarball off GitHub
    ARCH="$(uname -m)"
    case "$ARCH" in
      aarch64|arm64) FNM_ASSET="fnm-arm64.zip" ;;
      x86_64) FNM_ASSET="fnm-linux.zip" ;;
      *) echo "unsupported arch: $ARCH"; exit 1 ;;
    esac
    TMP="$(mktemp -d)"
    curl -fsSL -o "$TMP/fnm.zip" \
      "https://github.com/Schniz/fnm/releases/latest/download/${FNM_ASSET}"
    if command -v unzip >/dev/null 2>&1; then
      unzip -q -o "$TMP/fnm.zip" -d "$FNM_DIR"
    else
      python3 -m zipfile -e "$TMP/fnm.zip" "$FNM_DIR"
    fi
    chmod +x "$FNM_DIR/fnm"
    rm -rf "$TMP"
  fi
  log "installing Node 20 via fnm"
  eval "$("$FNM_DIR/fnm" env --shell bash)"  # safe: fnm env outputs fixed shell vars from a trusted CLI
  "$FNM_DIR/fnm" install 20 >/dev/null
  "$FNM_DIR/fnm" use 20 >/dev/null
fi

# Re-evaluate fnm env so node/npm are on PATH for the rest of the script
if [ -x "$FNM_DIR/fnm" ]; then
  eval "$("$FNM_DIR/fnm" env --shell bash 2>/dev/null || true)"  # safe: fnm env outputs fixed shell vars from a trusted CLI
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
