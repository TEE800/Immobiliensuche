#!/usr/bin/env bash
# Richtet einen frischen Ubuntu-Server ein: Docker, HTTPS über Caddy und
# die Mietsuche-App. Kann gefahrlos erneut ausgeführt werden, z. B. für Updates.
#
# Aufruf (als root):
#   curl -fsSL https://raw.githubusercontent.com/TEE800/Immobiliensuche/main/deploy/setup.sh | bash
set -euo pipefail

REPO=https://github.com/TEE800/Immobiliensuche.git
SERVER=/opt/server
APP=$SERVER/apps/immobiliensuche

export DEBIAN_FRONTEND=noninteractive
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
apt-get install -y -q git ufw unattended-upgrades >/dev/null

ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

mkdir -p "$SERVER/apps" "$SERVER/data/immobiliensuche"
if [ -d "$APP/.git" ]; then
  git -C "$APP" pull -q
else
  git clone -q "$REPO" "$APP"
fi

# Ohne eigene Domain: kostenlose Adresse über sslip.io aus der Server-IP.
if [ ! -f "$SERVER/.env" ]; then
  IP=$(curl -fsS4 https://api.ipify.org)
  echo "MIETSUCHE_HOST=${IP//./-}.sslip.io" > "$SERVER/.env"
fi
cp -n "$APP/deploy/Caddyfile" "$SERVER/Caddyfile" 2>/dev/null || true
cp "$APP/deploy/docker-compose.yml" "$SERVER/docker-compose.yml"

cd "$SERVER"
docker compose up -d --build --remove-orphans

# Jede Nacht um 4 Uhr neuen Code holen und neu starten.
cat > /etc/cron.d/mietsuche-update <<CRON
0 4 * * * root bash $APP/deploy/setup.sh >> /var/log/mietsuche-update.log 2>&1
CRON

. "$SERVER/.env"
echo
echo "Fertig. Die App ist gleich erreichbar unter: https://$MIETSUCHE_HOST"
