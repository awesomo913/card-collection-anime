#!/usr/bin/env bash
# One-shot installer for Raspberry Pi (Pi OS 64-bit, Bookworm or newer).
#
# Run as the user that should own the service (typically `pi` or your login):
#   curl -fsSL https://raw.githubusercontent.com/awesomo913/card-collection-anime/main/deploy/pi-setup.sh | bash
# or after cloning:
#   bash deploy/pi-setup.sh
#
# Idempotent — re-running upgrades the install in place.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/awesomo913/card-collection-anime.git}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/card-collection-anime}"
SERVICE_NAME="${SERVICE_NAME:-card-collection}"
SERVICE_USER="${SERVICE_USER:-$USER}"
PORT="${PORT:-8000}"

log()  { printf "\033[1;36m[setup]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn ]\033[0m %s\n" "$*"; }

# --- 1. System packages -----------------------------------------------------
log "installing apt packages (python venv, nodejs, git)"
sudo apt update -y
sudo apt install -y --no-install-recommends \
  python3 python3-venv python3-pip nodejs npm git curl ca-certificates

# Pi OS Bookworm ships Node 18 which is fine for CRA 5; if you hit an older
# Node, NodeSource is the cleanest upgrade path:
#   curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
#   sudo apt install -y nodejs
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]' || echo 0)"
if [ "${NODE_MAJOR}" -lt 18 ]; then
  warn "Node ${NODE_MAJOR} detected — CRA 5 wants Node 18+. Consider upgrading."
fi

# --- 2. Source code ---------------------------------------------------------
if [ -d "$INSTALL_DIR/.git" ]; then
  log "updating existing checkout at $INSTALL_DIR"
  git -C "$INSTALL_DIR" pull --ff-only
else
  log "cloning $REPO_URL into $INSTALL_DIR"
  git clone "$REPO_URL" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"

# --- 3. Backend venv --------------------------------------------------------
log "creating backend venv + installing requirements"
python3 -m venv backend/venv
backend/venv/bin/pip install --upgrade pip wheel >/dev/null
backend/venv/bin/pip install -r backend/requirements.txt

# --- 4. Frontend build (single-port mode) -----------------------------------
log "building frontend (CRA production bundle)"
pushd frontend >/dev/null
# Same-origin: API base is just '' so the static bundle calls /cards/, /status, etc.
echo 'REACT_APP_API_BASE_URL=' > .env.production
npm install --silent --no-audit --no-fund
CI=false npm run build
popd >/dev/null

# --- 5. systemd unit --------------------------------------------------------
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
log "writing $SERVICE_FILE"
sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Card Collection (FastAPI + static UI)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR/backend
Environment=PRICE_UPDATE_INTERVAL_HOURS=6
Environment=LOG_RING_SIZE=400
ExecStart=$INSTALL_DIR/backend/venv/bin/uvicorn main:app --host 0.0.0.0 --port $PORT
Restart=on-failure
RestartSec=5
# Light hardening
NoNewPrivileges=yes
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$INSTALL_DIR/backend
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

# --- 6. Friendly summary ----------------------------------------------------
sleep 2
LAN_IP="$(hostname -I | awk '{print $1}')"
log "service status:"
systemctl --no-pager --quiet status "$SERVICE_NAME" || true
echo
log "open the app at:  http://${LAN_IP}:${PORT}"
log "status dashboard: http://${LAN_IP}:${PORT}/status"
log "tail logs with:   sudo journalctl -fu ${SERVICE_NAME}"
