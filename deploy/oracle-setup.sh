#!/usr/bin/env bash
# =============================================================================
# oracle-setup.sh — put NBS Signal Tool on a fresh Oracle Cloud VM
# =============================================================================
# Run it on the VM, as the default user (ubuntu / opc), not as root:
#
#     curl -fsSL https://raw.githubusercontent.com/Snsureshkumar97/NBS/main/deploy/oracle-setup.sh | bash
#
# or, if you have the repo already:
#
#     bash deploy/oracle-setup.sh
#
# It is idempotent — running it twice changes nothing the second time, so it is
# safe to re-run after a failure rather than starting the VM again.
#
# WHAT IT DOES
#   * installs python3, pip, venv and git
#   * clones (or updates) the repo into ~/nbs
#   * builds a virtualenv and installs requirements.txt
#   * creates ~/.trading-tool/.env with 0600 permissions, leaving the secrets
#     blank for you to fill in
#   * installs a systemd service so the tool starts on boot and restarts if it
#     dies — which is the entire reason for moving off a laptop
#   * installs Tailscale and turns on Funnel, for a stable HTTPS address
#     without buying a domain
#
# WHAT IT DELIBERATELY DOES NOT DO
#   * put any secret in this file or in the repo. The .env it writes is empty
#     and the script tells you what to fill in.
#   * open ports in Oracle's security list. Funnel needs no inbound ports at
#     all — the VM holds an outbound connection and traffic returns down it.
#     If you would rather use a domain and Caddy, that needs 80/443 opened in
#     BOTH Oracle's security list and the VM's own iptables, and Oracle's
#     Ubuntu images block them by default. Funnel avoids that whole class of
#     problem, which is why it is the default here.
set -euo pipefail

REPO="${REPO:-https://github.com/Snsureshkumar97/NBS.git}"
APP_DIR="${APP_DIR:-$HOME/nbs}"
DATA_DIR="${DATA_DIR:-$HOME/.trading-tool}"
PORT="${PORT:-5055}"
TS_HOSTNAME="${TS_HOSTNAME:-nbs-tool}"

say() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\n\033[1;33m!!\033[0m %s\n' "$*"; }

if [ "$(id -u)" = "0" ]; then
  warn "Run this as the normal user (ubuntu or opc), not root."
  exit 1
fi

# --- packages ----------------------------------------------------------------
say "Installing packages"
if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update -qq
  # build-essential and python3-dev are needed because pandas/numpy may build
  # from source on ARM if no wheel matches the interpreter.
  sudo apt-get install -y -qq python3 python3-pip python3-venv git curl \
                             build-essential python3-dev
elif command -v dnf >/dev/null 2>&1; then
  sudo dnf install -y -q python3 python3-pip git curl gcc python3-devel
else
  warn "Unknown package manager. Install python3, pip, venv and git by hand."
  exit 1
fi

# --- code --------------------------------------------------------------------
if [ -d "$APP_DIR/.git" ]; then
  say "Updating the existing checkout in $APP_DIR"
  git -C "$APP_DIR" pull --ff-only
else
  say "Cloning into $APP_DIR"
  git clone --depth 50 "$REPO" "$APP_DIR"
fi

# --- python ------------------------------------------------------------------
say "Building the virtualenv"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip wheel
say "Installing requirements (pandas/numpy can take a few minutes on ARM)"
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

# --- data + env --------------------------------------------------------------
say "Preparing $DATA_DIR"
mkdir -p "$DATA_DIR"
chmod 700 "$DATA_DIR"

ENV_FILE="$DATA_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
  ADMIN_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
  cat > "$ENV_FILE" <<ENVEOF
# NBS Signal Tool — this machine's configuration.
# Nothing here is in the repo, and nothing here should be.

# Your Kite Connect app. Fill these in from developers.kite.trade.
KITE_API_KEY=
KITE_API_SECRET=

# The public address, filled in below once Tailscale reports the hostname.
# Zerodha redirects here, and it must match your Kite app's Redirect URL
# exactly — no trailing slash.
WEB_PUBLIC_URL=

# The port the process binds. Kept separate from WEB_PUBLIC_URL because behind
# a tunnel the public address is https on 443 while the process binds this.
WEB_PORT=$PORT

# Guards /admin, which is the only way accounts are created. Generated here.
WEB_ADMIN_KEY=$ADMIN_KEY

# Accounts are made by you on /admin, not by strangers signing up.
WEB_ALLOW_SIGNUP=0
ENVEOF
  chmod 600 "$ENV_FILE"
  say "Wrote $ENV_FILE (mode 600) with a generated admin key"
else
  say "Keeping the existing $ENV_FILE"
fi

# --- systemd -----------------------------------------------------------------
say "Installing the systemd service"
sudo tee /etc/systemd/system/nbs-signal-tool.service >/dev/null <<UNITEOF
[Unit]
Description=NBS Signal Tool
Documentation=https://github.com/Snsureshkumar97/NBS
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/.venv/bin/python web_server.py --host 127.0.0.1 --port $PORT
# Restart on any exit. The whole point of leaving a laptop behind is that a
# crash at 09:20 does not mean the day is lost.
Restart=always
RestartSec=5
# Kill the WebSocket cleanly so Zerodha is not left holding a dead session.
KillSignal=SIGINT
TimeoutStopSec=20

# It reads market data and writes two files. It needs none of the rest.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$DATA_DIR $HOME/trading-tool-logs

[Install]
WantedBy=multi-user.target
UNITEOF
mkdir -p "$HOME/trading-tool-logs"
sudo systemctl daemon-reload
sudo systemctl enable nbs-signal-tool >/dev/null 2>&1 || true

# --- tailscale ---------------------------------------------------------------
if ! command -v tailscale >/dev/null 2>&1; then
  say "Installing Tailscale"
  curl -fsSL https://tailscale.com/install.sh | sh
fi

cat <<DONEEOF

============================================================================
  Installed. Four steps left, in this order.
============================================================================

  1. Join Tailscale and note the hostname it gives you:

       sudo tailscale up --hostname=$TS_HOSTNAME
       tailscale status --json | grep -m1 DNSName

  2. Put that address in the env file, with no trailing slash:

       nano $ENV_FILE
       # WEB_PUBLIC_URL=https://$TS_HOSTNAME.<your-tailnet>.ts.net
       # KITE_API_KEY=...
       # KITE_API_SECRET=...

  3. Start it, and check it came up:

       sudo systemctl restart nbs-signal-tool
       systemctl status nbs-signal-tool --no-pager
       curl -s localhost:$PORT/healthz

  4. Open the Funnel, then set your Kite Redirect URL to
     <WEB_PUBLIC_URL>/kite/callback

       sudo tailscale funnel --bg $PORT
       tailscale funnel status

  Your admin page — the only way to create accounts:

       <WEB_PUBLIC_URL>/admin?key=\$(grep WEB_ADMIN_KEY $ENV_FILE | cut -d= -f2)

  Logs:      journalctl -u nbs-signal-tool -f
  Update:    cd $APP_DIR && git pull && sudo systemctl restart nbs-signal-tool

============================================================================
DONEEOF
