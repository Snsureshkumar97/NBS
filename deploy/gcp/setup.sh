#!/usr/bin/env bash
# deploy/gcp/setup.sh — set the tool up on a Debian/Ubuntu VM (Google Cloud, Mumbai)
# ==============================================================================
# Adapted from deploy/oracle-setup.sh on 20 Sep 2026, when the tool moved off
# the user's Mac so that its orders leave from ONE fixed IP registered at
# Zerodha and Delta Exchange. Run as the normal login user on the VM:
#
#     bash setup.sh
#
# Idempotent: safe to run again after a git pull. It installs packages, the
# checkout, a virtualenv, the systemd service, two timers (the morning check
# at 08:00 IST and the option recorder at 16:00 IST, Mon-Fri), and Tailscale.
# It never writes your keys: copy ~/.trading-tool/.env from the Mac before the
# first start (see deploy/GCP.md), or fill in the template it leaves.
set -euo pipefail

REPO="${REPO:-https://github.com/Snsureshkumar97/NBS.git}"
APP_DIR="${APP_DIR:-$HOME/nbs}"
DATA_DIR="${DATA_DIR:-$HOME/.trading-tool}"
LOG_DIR="${LOG_DIR:-$HOME/trading-tool-logs}"
PORT="${PORT:-5055}"
TS_HOSTNAME="${TS_HOSTNAME:-nbs-signal-tool}"

say()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\n\033[1;33m!!\033[0m %s\n' "$*"; }

if [ "$(id -u)" = "0" ]; then
  warn "Run this as the normal user, not root."
  exit 1
fi

say "Installing packages"
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-pip python3-venv python3-tk git curl rsync \
                           build-essential python3-dev tzdata
# python3-tk: web_server imports chart_panel, which imports tkinter (a separate
# package on Debian) - the first VM start on 20 Sep 2026 died without it.

if [ -d "$APP_DIR/.git" ]; then
  say "Updating the existing checkout in $APP_DIR"
  git -C "$APP_DIR" pull --ff-only
else
  say "Cloning into $APP_DIR"
  git clone --depth 50 "$REPO" "$APP_DIR"
fi

say "Building the virtualenv"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip wheel
say "Installing requirements"
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt" anthropic certifi

say "Preparing $DATA_DIR and $LOG_DIR"
mkdir -p "$DATA_DIR" "$LOG_DIR"
chmod 700 "$DATA_DIR"
ENV_FILE="$DATA_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
  ADMIN_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
  cat > "$ENV_FILE" <<ENVEOF
KITE_API_KEY=
KITE_API_SECRET=
WEB_PUBLIC_URL=
WEB_PORT=$PORT
WEB_ADMIN_KEY=$ADMIN_KEY
WEB_REQUIRE_LOGIN=1
WEB_ALLOW_SIGNUP=0
ANTHROPIC_API_KEY=
ENABLE_CRYPTO=1
ENVEOF
  chmod 600 "$ENV_FILE"
  say "Wrote a template $ENV_FILE (mode 600). Copy the Mac's .env over it before starting."
else
  say "Keeping the existing $ENV_FILE"
fi

say "Installing the systemd service"
sudo tee /etc/systemd/system/nbs-signal-tool.service >/dev/null <<UNITEOF
[Unit]
Description=NBS Signal Tool (TradePicker)
Documentation=https://github.com/Snsureshkumar97/NBS
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
Environment=ENABLE_CRYPTO=1
Environment=TZ=Asia/Kolkata
Environment=PYTHONUNBUFFERED=1
ExecStart=$APP_DIR/.venv/bin/python web_server.py --host 127.0.0.1 --port $PORT
Restart=always
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=20
StandardOutput=append:$LOG_DIR/nbs-signal-tool.log
StandardError=append:$LOG_DIR/nbs-signal-tool.log
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$DATA_DIR $LOG_DIR

[Install]
WantedBy=multi-user.target
UNITEOF

say "Installing the two timers (IST, weekdays)"
sudo tee /etc/systemd/system/nbs-morning-check.service >/dev/null <<UNITEOF
[Unit]
Description=NBS morning check - did the tool run the open on its own
[Service]
Type=oneshot
User=$USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
Environment=TZ=Asia/Kolkata
Environment=NBS_APP=$APP_DIR
Environment=NBS_SERVER_LOG=$LOG_DIR/nbs-signal-tool.log
Environment=NBS_LOG_DIR=$LOG_DIR
ExecStart=$APP_DIR/.venv/bin/python deploy/macos/morning-check.py
StandardOutput=append:$LOG_DIR/nbs-morning-check.log
StandardError=append:$LOG_DIR/nbs-morning-check.log
UNITEOF
sudo tee /etc/systemd/system/nbs-morning-check.timer >/dev/null <<UNITEOF
[Unit]
Description=Run the NBS morning check at 08:00 IST on weekdays
[Timer]
OnCalendar=Mon..Fri 08:00 Asia/Kolkata
Persistent=false
[Install]
WantedBy=timers.target
UNITEOF
sudo tee /etc/systemd/system/nbs-option-recorder.service >/dev/null <<UNITEOF
[Unit]
Description=NBS option recorder - keep the real option prices after the close
[Service]
Type=oneshot
User=$USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
Environment=TZ=Asia/Kolkata
ExecStart=$APP_DIR/.venv/bin/python record_options.py --wait
StandardOutput=append:$LOG_DIR/nbs-option-recorder.log
StandardError=append:$LOG_DIR/nbs-option-recorder.log
UNITEOF
sudo tee /etc/systemd/system/nbs-option-recorder.timer >/dev/null <<UNITEOF
[Unit]
Description=Run the NBS option recorder at 16:00 IST on weekdays
[Timer]
OnCalendar=Mon..Fri 16:00 Asia/Kolkata
Persistent=false
[Install]
WantedBy=timers.target
UNITEOF

sudo systemctl daemon-reload
sudo systemctl enable nbs-signal-tool nbs-morning-check.timer nbs-option-recorder.timer >/dev/null 2>&1 || true
sudo systemctl start nbs-morning-check.timer nbs-option-recorder.timer || true

if ! command -v tailscale >/dev/null 2>&1; then
  say "Installing Tailscale"
  curl -fsSL https://tailscale.com/install.sh | sh
fi

cat <<DONEEOF
============================================================================
  Installed. The cut-over, in this order (details in deploy/GCP.md):
============================================================================
  1. Copy the Mac's data here (run FROM THE MAC): .env, the users file, the
     trade logs and the history cache. Then:  chmod 600 $ENV_FILE
  2. On the Mac, stop the tool and log its Tailscale node out, so this VM can
     take the name "$TS_HOSTNAME" and keep the same public address.
  3. Join Tailscale here with that name and open the Funnel:
       sudo tailscale up --hostname=$TS_HOSTNAME
       sudo tailscale funnel --bg $PORT
       tailscale funnel status
  4. Start the tool and check it:
       sudo systemctl restart nbs-signal-tool
       systemctl status nbs-signal-tool --no-pager
       curl -s localhost:$PORT/ -o /dev/null -w '%{http_code}\n'
  5. Register this VM's static IP at Zerodha (developers.kite.trade, your app,
     IP whitelist) and on your Delta Exchange API key.
  Logs:     tail -f $LOG_DIR/nbs-signal-tool.log
  Update:   cd $APP_DIR && git pull && sudo systemctl restart nbs-signal-tool
============================================================================
DONEEOF
