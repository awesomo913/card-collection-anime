#!/data/data/com.termux/files/usr/bin/bash
# Termux bootstrap + launcher for card-collection-anime.
# One-shot: installs system deps, sets up venv, builds frontend, launches uvicorn.
# Re-runs are idempotent — only does work that's not already done.
#
# First-time setup (run once on the phone):
#   1. Install Termux from F-Droid (NOT the Play Store version — it's outdated).
#   2. pkg update -y && pkg install -y git
#   3. git clone https://github.com/awesomo913/card-collection-anime ~/card-collection-anime
#   4. bash ~/card-collection-anime/deploy/termux-run.sh
#
# Subsequent launches (daily): just re-run the same script.
# Optional: install Termux:Widget from F-Droid + symlink this script into
#   ~/.shortcuts/CardCollection.sh for a one-tap home-screen launcher.

set -e

REPO_DIR="${REPO_DIR:-$HOME/card-collection-anime}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

log() { printf '[termux-run] %s\n' "$*"; }

if [ ! -d /data/data/com.termux ]; then
  log "ERROR: not running inside Termux. Use deploy/pi-run-nosudo.sh on a Pi or pi-setup.sh on a server."
  exit 1
fi

cd "$REPO_DIR"

# 1) System packages — Termux pkg manager (apt-based, no sudo).
need_pkgs=()
for p in python rust nodejs-lts libjpeg-turbo clang make pkg-config; do
  if ! command -v "$p" >/dev/null 2>&1 && ! pkg list-installed 2>/dev/null | grep -q "^$p/"; then
    need_pkgs+=("$p")
  fi
done
if [ ${#need_pkgs[@]} -gt 0 ]; then
  log "installing system packages: ${need_pkgs[*]}"
  pkg install -y "${need_pkgs[@]}"
fi

# 2) Backend venv + Python deps.
if [ ! -x backend/venv/bin/python ]; then
  log "creating Python venv"
  python -m venv backend/venv
fi
log "installing/refreshing Python deps"
backend/venv/bin/pip install --upgrade pip wheel >/dev/null
backend/venv/bin/pip install -r backend/requirements.txt

# 3) Frontend build — only if missing or stale relative to package.json.
need_build=false
if [ ! -f frontend/build/index.html ]; then
  need_build=true
elif [ frontend/package.json -nt frontend/build/index.html ]; then
  need_build=true
fi
if $need_build; then
  log "building frontend (one-time, ~5-10 min on phone)"
  cd frontend
  if [ ! -d node_modules ]; then
    npm install --no-audit --no-fund
  fi
  npm run build
  cd ..
fi

# 4) Wake the screen + open Chrome to the app, if termux-api is present.
if command -v termux-open-url >/dev/null 2>&1; then
  ( sleep 3; termux-open-url "http://$HOST:$PORT" ) &
else
  log "tip: pkg install termux-api  +  install Termux:API from F-Droid for auto-open"
fi

# 5) Launch uvicorn in the foreground (Ctrl+C to stop).
log "starting uvicorn on http://$HOST:$PORT"
log "Chrome → http://$HOST:$PORT  (or wait for auto-open)"
cd backend
exec ./venv/bin/uvicorn main:app --host "$HOST" --port "$PORT"
