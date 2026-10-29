#!/usr/bin/env bash
# Install the FitPlus agent platform on an Ubuntu/Debian server as two systemd services (web + worker).
# Touches only /opt/fitplus-agents and two unit files; does not stop or edit anything else on the server.
#   sudo bash deploy/install.sh            (run from the repo checkout: .../agent-platform)
set -euo pipefail
APP=/opt/fitplus-agents
SRC="$(cd "$(dirname "$0")/.." && pwd)"

apt-get update -qq && apt-get install -y -qq python3 python3-venv >/dev/null
mkdir -p "$APP/data"
cp -r "$SRC/app" "$SRC/scripts" "$SRC/requirements.txt" "$APP/"
python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install -q -r "$APP/requirements.txt"

if [ ! -f "$APP/.env" ]; then
  cat > "$APP/.env" <<ENV
APP_DB=$APP/data/agents.db
APP_TOKENS=$(openssl rand -hex 16)
APP_LLM_PROVIDER=openai
APP_IMAGE_MODEL=gpt-image-2
APPROVER_IDS=186920726
OPENAI_API_KEY=
BALE_TOKEN=
ENV
  chmod 600 "$APP/.env"
  echo ">> Fill OPENAI_API_KEY and BALE_TOKEN in $APP/.env, then: systemctl restart fitplus-web fitplus-worker"
fi

unit() { # name, command
cat > "/etc/systemd/system/$1.service" <<UNIT
[Unit]
Description=FitPlus agents ($1)
After=network-online.target
[Service]
WorkingDirectory=$APP
EnvironmentFile=$APP/.env
ExecStart=$APP/venv/bin/$2
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
UNIT
}
unit fitplus-web "uvicorn app.main:app --host 127.0.0.1 --port 8000"
unit fitplus-worker "python -m app.worker"
systemctl daemon-reload
systemctl enable --now fitplus-web fitplus-worker
echo "done. logs: journalctl -u fitplus-worker -f"
