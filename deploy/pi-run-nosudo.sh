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

# --- Load secrets/config from ~/.bashrc when invoked non-interactively ---
# Bashrc has an interactive-shell guard near the top (`case $- in *i*) ;; *) return;; esac`)
# that no-ops `source ~/.bashrc` under setsid/nohup/systemd. To survive those launch
# modes, extract whitelisted `export NAME=value` lines directly and eval them.
# Whitelist (regex below) ensures we only pull known config vars — never aliases,
# functions, PROMPT_COMMAND, or unrelated assignments.
if [ -f "$HOME/.bashrc" ]; then
  while IFS= read -r line; do
    eval "$line" 2>/dev/null || true
  done < <(grep -E '^[[:space:]]*export[[:space:]]+(DEEPSEEK_API_KEY|DEEPSEEK_MODEL|TCGPLAYER_API_KEY|IDENTIFY_WORKERS|PRICE_UPDATE_INTERVAL_HOURS|FORECAST_CACHE_TTL)=' "$HOME/.bashrc" 2>/dev/null)
  if [ -n "${DEEPSEEK_API_KEY:-}" ]; then
    log "loaded DEEPSEEK_API_KEY from ~/.bashrc"
  else
    log "WARNING: DEEPSEEK_API_KEY not found in ~/.bashrc — /forecast and /identify will return 503"
  fi
fi

log "git pull"
git pull --ff-only || true

# --- Backend venv (idempotent) ---
if [ ! -x backend/venv/bin/uvicorn ]; then
  log "creating backend venv"
  python3 -m venv backend/venv
  backend/venv/bin/pip install --upgrade --quiet pip wheel
fi
# Always refresh deps on boot — pip is a no-op when everything's satisfied,
# but this picks up new requirements (e.g. python-multipart added for /identify)
# without forcing the user to nuke the venv. Runs in ~2-5s when up-to-date.
log "ensuring backend deps"
backend/venv/bin/pip install --quiet -r backend/requirements.txt

# --- ffmpeg (user-space static build for /identify/video frame extraction) ---
# Pi OS Bookworm has ffmpeg in apt, but we run without sudo. John Van Sickle's
# static aarch64 build drops a single binary into ~/.local/bin/ — no root,
# no shared-lib install. Skip download if a system-wide ffmpeg already exists
# (e.g., the user installed it with sudo separately).
LOCAL_BIN="$HOME/.local/bin"
export PATH="$LOCAL_BIN:$PATH"
if ! command -v ffmpeg >/dev/null 2>&1; then
  if [ ! -x "$LOCAL_BIN/ffmpeg" ]; then
    log "installing static ffmpeg (~50MB, one-time) for video identify"
    mkdir -p "$LOCAL_BIN"
    ARCH="$(uname -m)"
    case "$ARCH" in
      aarch64|arm64) FFMPEG_ASSET="ffmpeg-release-arm64-static.tar.xz" ;;
      x86_64)        FFMPEG_ASSET="ffmpeg-release-amd64-static.tar.xz" ;;
      *) echo "unsupported arch for ffmpeg static build: $ARCH"; FFMPEG_ASSET="" ;;
    esac
    if [ -n "$FFMPEG_ASSET" ]; then
      TMP="$(mktemp -d)"
      if curl -fsSL -o "$TMP/ffmpeg.tar.xz" \
          "https://johnvansickle.com/ffmpeg/releases/${FFMPEG_ASSET}"; then
        tar -xJf "$TMP/ffmpeg.tar.xz" -C "$TMP" --strip-components=1 \
            --wildcards '*/ffmpeg' 2>/dev/null || true
        if [ -f "$TMP/ffmpeg" ]; then
          mv "$TMP/ffmpeg" "$LOCAL_BIN/ffmpeg"
          chmod +x "$LOCAL_BIN/ffmpeg"
          log "ffmpeg installed at $LOCAL_BIN/ffmpeg"
        else
          log "ffmpeg extraction failed; video identify will return an error"
        fi
        rm -rf "$TMP"
      else
        log "ffmpeg download failed; video identify will return an error"
      fi
    fi
  fi
fi
if command -v ffmpeg >/dev/null 2>&1; then
  log "ffmpeg available: $(command -v ffmpeg)"
else
  log "WARNING: ffmpeg unavailable — /identify/video will return error per-call"
fi

# --- Apply pending Alembic migrations (idempotent: no-op if already at head) ---
log "alembic upgrade head"
(cd backend && "$ROOT/backend/venv/bin/alembic" upgrade head) || \
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

# --- Phase E: 4×/day price refresh by default ---
# Override by exporting PRICE_UPDATE_INTERVAL_HOURS before running this script.
export PRICE_UPDATE_INTERVAL_HOURS="${PRICE_UPDATE_INTERVAL_HOURS:-6}"
log "price refresh interval: ${PRICE_UPDATE_INTERVAL_HOURS}h"

# --- Launch uvicorn in foreground; Ctrl+C to stop ---
log "starting uvicorn on http://${HOST}:${PORT}"
LAN_IP="$(hostname --all-ip-addresses | awk '{print $1}')"
log "share with iPhone:  http://${LAN_IP}:${PORT}"
log "status dashboard:   http://${LAN_IP}:${PORT}/status"
exec backend/venv/bin/uvicorn main:app \
  --app-dir backend \
  --host "$HOST" \
  --port "$PORT"
