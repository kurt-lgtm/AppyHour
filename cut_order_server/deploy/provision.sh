#!/usr/bin/env bash
# One-shot provision for cut-order droplet (Ubuntu 22.04 LTS).
# Run as root: bash provision.sh
set -euo pipefail

APP_USER="cutorder"
APP_DIR="/opt/cut-order-server"
REPO_URL="https://github.com/kurt-lgtm/AppyHour.git"
DOMAIN="${DOMAIN:-cut.elevatefoods.co}"   # override before running if different

echo "[1/9] apt update + base packages"
apt-get update -y
apt-get install -y \
  python3.11 python3.11-venv python3.11-dev \
  build-essential git curl ufw nginx certbot python3-certbot-nginx

echo "[2/9] create app user"
if ! id -u "$APP_USER" >/dev/null 2>&1; then
  useradd -r -s /bin/bash -m -d "/home/$APP_USER" "$APP_USER"
fi

echo "[3/9] clone repo"
mkdir -p "$APP_DIR"
chown "$APP_USER:$APP_USER" "$APP_DIR"
if [ ! -d "$APP_DIR/.git" ]; then
  sudo -u "$APP_USER" git clone "$REPO_URL" "$APP_DIR"
else
  sudo -u "$APP_USER" git -C "$APP_DIR" pull
fi

echo "[4/9] venv + install deps"
sudo -u "$APP_USER" python3.11 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/cut_order_server/requirements.txt"

echo "[5/9] write env file (fill in secrets manually after this)"
ENV_FILE=/etc/cut-order.env
if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<EOF
FLASK_SECRET_KEY=CHANGE_ME_$(openssl rand -hex 32)
FLASK_ENV=production
APP_PASSWORD=CHANGE_ME

SHOPIFY_STORE_URL=elevatefoods
SHOPIFY_ACCESS_TOKEN=CHANGE_ME
SHOPIFY_API_VERSION=2026-04

RECHARGE_TOKEN=CHANGE_ME

LTF_SHEET_ID=1Obz-Ib6KsjhB83NiRlFFCLFk9UjHr_YZl8lONl6VzKw
GOOGLE_SVC_ACCOUNT_JSON=/etc/cut-order/google-svc.json

DO_SPACES_ENDPOINT=https://nyc3.digitaloceanspaces.com
DO_SPACES_REGION=nyc3
DO_SPACES_BUCKET=cut-orders
DO_SPACES_KEY=CHANGE_ME
DO_SPACES_SECRET=CHANGE_ME

GUNICORN_WORKERS=2
GUNICORN_HOST=127.0.0.1
PORT=8000
EOF
  chmod 640 "$ENV_FILE"
  chown root:"$APP_USER" "$ENV_FILE"
  echo "  → Wrote $ENV_FILE — edit it to fill in real values"
fi

echo "[6/9] place google service account JSON"
mkdir -p /etc/cut-order
chown root:"$APP_USER" /etc/cut-order
chmod 750 /etc/cut-order
if [ ! -f /etc/cut-order/google-svc.json ]; then
  echo "  → Place service account JSON at /etc/cut-order/google-svc.json (chmod 640)"
fi

echo "[7/9] install systemd unit"
cp "$APP_DIR/cut_order_server/deploy/cut-order.service" /etc/systemd/system/cut-order.service
systemctl daemon-reload
systemctl enable cut-order.service

echo "[8/9] install nginx config"
cp "$APP_DIR/cut_order_server/deploy/nginx.conf" /etc/nginx/sites-available/cut-order
sed -i "s|__DOMAIN__|$DOMAIN|g" /etc/nginx/sites-available/cut-order
ln -sf /etc/nginx/sites-available/cut-order /etc/nginx/sites-enabled/cut-order
nginx -t

echo "[9/9] firewall + restart"
ufw allow 'Nginx Full' || true
ufw allow OpenSSH || true
systemctl restart nginx

cat <<DONE

✅ Provision complete. Next manual steps:

1. Edit $ENV_FILE with real secret values.
2. Drop Google service account JSON at /etc/cut-order/google-svc.json (chmod 640, owner root:$APP_USER).
3. Point DNS $DOMAIN → this droplet's public IP (A record).
4. Get TLS cert:
     certbot --nginx -d $DOMAIN --non-interactive --agree-tos -m kurt@elevatefoods.co
5. Start the app:
     systemctl start cut-order.service
     systemctl status cut-order.service
6. Test: curl https://$DOMAIN/healthz

DONE
