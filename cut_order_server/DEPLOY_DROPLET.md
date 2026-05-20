# Deploy on a DigitalOcean Droplet

For when Anik provisions a fresh droplet for this app.

## Anik provisions (~15 min)

1. Create Ubuntu 22.04 LTS droplet, NYC3 region, 2GB RAM ($12/mo)
2. SSH in as root
3. Run provision script:
   ```bash
   curl -sSL https://raw.githubusercontent.com/kurt-lgtm/AppyHour/main/cut_order_server/deploy/provision.sh -o provision.sh
   DOMAIN=cut.elevatefoods.co bash provision.sh
   ```
   This installs python3.11, nginx, certbot; clones repo; creates `cutorder` user; sets up systemd + nginx configs.

## Kurt sets secrets (~5 min)

4. SSH in (Anik adds your SSH key first)
5. Edit `/etc/cut-order.env` — replace every `CHANGE_ME` with real value:
   - `FLASK_SECRET_KEY` — auto-generated, fine
   - `APP_PASSWORD` — pick one
   - `SHOPIFY_ACCESS_TOKEN` — from existing settings
   - `RECHARGE_TOKEN` — from existing settings
   - `DO_SPACES_KEY` / `DO_SPACES_SECRET` — generate in DO Console → Spaces

6. Place Google service account JSON:
   ```bash
   sudo nano /etc/cut-order/google-svc.json
   # paste contents of shipping-perfomance-review-accd39ac4b78.json
   sudo chmod 640 /etc/cut-order/google-svc.json
   sudo chown root:cutorder /etc/cut-order/google-svc.json
   ```

## DNS + TLS (~5 min, Anik)

7. Point DNS: `cut.elevatefoods.co` A record → droplet's public IP
8. Get TLS cert:
   ```bash
   sudo certbot --nginx -d cut.elevatefoods.co --non-interactive --agree-tos -m kurt@elevatefoods.co
   ```

## Start (~1 min)

9. ```bash
   sudo systemctl start cut-order.service
   sudo systemctl status cut-order.service     # should show "active (running)"
   curl https://cut.elevatefoods.co/healthz    # should return {"ok": true}
   ```

10. Visit `https://cut.elevatefoods.co` in browser → log in with `APP_PASSWORD` → run a cut order.

## DO Spaces bucket (Kurt, ~2 min)

Before first run:
- DO Console → Spaces → Create bucket `cut-orders` in NYC3
- File listing: Restricted
- Generate Spaces access key, paste into `/etc/cut-order.env`

## Auto-deploy on git push (optional)

Add a deploy webhook or cron polling `git pull`:
```bash
# crontab as cutorder user
*/5 * * * * cd /opt/cut-order-server && git pull -q && cd cut_order_server && /opt/cut-order-server/.venv/bin/pip install -q -r requirements.txt && sudo systemctl restart cut-order
```

Or just SSH and `git pull && sudo systemctl restart cut-order` when needed.

## Troubleshooting

- **Logs:** `sudo journalctl -u cut-order -f`
- **Restart:** `sudo systemctl restart cut-order`
- **nginx logs:** `sudo tail -f /var/log/nginx/error.log`
- **Test gunicorn directly:** `sudo -u cutorder /opt/cut-order-server/.venv/bin/gunicorn -c /opt/cut-order-server/cut_order_server/gunicorn_conf.py --chdir /opt/cut-order-server/cut_order_server wsgi:application` and check it binds
