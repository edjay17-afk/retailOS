#!/usr/bin/env bash
# Run ONCE on the Oracle VM (Ubuntu), as the normal user (not root):  bash setup_vm.sh yourname.duckdns.org
# It installs Retail OS as a background service with automatic HTTPS (Caddy) and a nightly backup.
set -euo pipefail
DOMAIN="${1:?Usage: bash setup_vm.sh yourname.duckdns.org}"
APP="$HOME/retailos"
cd "$HOME"

echo "== 1/7 System packages"
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip sqlite3 unzip curl debian-keyring debian-archive-keyring apt-transport-https gpg
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update -y && sudo apt-get install -y caddy
fi

echo "== 2/7 Unpack the application"
[ -f "$HOME/retailos-cloud.tar.gz" ] || { echo "retailos-cloud.tar.gz is not in $HOME. Upload it first (see the guide)."; exit 1; }
mkdir -p "$APP"
tar -xzf "$HOME/retailos-cloud.tar.gz" -C "$APP"
cd "$APP"

echo "== 3/7 Python environment"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo "== 4/7 Browser for the StoreHub robot"
.venv/bin/python -m playwright install --with-deps chromium || echo "(robot browser could not be installed: the system still works, the robot will not)"

echo "== 5/7 Settings"
sed -i "s|^DATA_DIR=.*|DATA_DIR=$APP/data|; s|^HOST=.*|HOST=127.0.0.1|; s|^PORT=.*|PORT=8000|" .env
grep -q '^BEHIND_PROXY=' .env && sed -i 's|^BEHIND_PROXY=.*|BEHIND_PROXY=true|' .env || echo 'BEHIND_PROXY=true' >> .env
grep -q '^BASE_URL=' .env && sed -i "s|^BASE_URL=.*|BASE_URL=https://$DOMAIN|" .env || echo "BASE_URL=https://$DOMAIN" >> .env
grep -q '^REQUIRE_HTTPS=' .env && sed -i 's|^REQUIRE_HTTPS=.*|REQUIRE_HTTPS=true|' .env || echo 'REQUIRE_HTTPS=true' >> .env
chmod 600 .env

echo "== 6/7 Background service"
sed "s|__USER__|$USER|g; s|__APP__|$APP|g" deploy/retailos.service | sudo tee /etc/systemd/system/retailos.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now retailos

echo "== 7/7 HTTPS front door and firewall"
sed "s|__DOMAIN__|$DOMAIN|g" deploy/Caddyfile | sudo tee /etc/caddy/Caddyfile >/dev/null
sudo systemctl enable caddy && sudo systemctl restart caddy
# Oracle's Ubuntu image blocks web ports in its own firewall: open 80 and 443
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo apt-get install -y iptables-persistent >/dev/null 2>&1 || true
sudo netfilter-persistent save || true

echo "== Nightly backup (02:30)"
chmod +x deploy/backup.sh
( crontab -l 2>/dev/null | grep -v retailos-backup; echo "30 2 * * * $APP/deploy/backup.sh # retailos-backup" ) | crontab -

echo
echo "Done. Open https://$DOMAIN in a few minutes (the certificate takes a moment the first time)."
echo "Check the service with:  sudo systemctl status retailos     Logs:  journalctl -u retailos -n 50"
