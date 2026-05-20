# Deploy to DigitalOcean App Platform

Pattern matches your existing `appyhourbox-app-cluxg.ondigitalocean.app`.

## Prereqs (one-time)

1. **GitHub:** `AppyHour` repo is already on GitHub (`kurt-lgtm/AppyHour`). Push current `cut_order_server/` directory:
   ```bash
   cd "C:/Users/Work/Claude Projects/AppyHour"
   git add cut_order_server/
   git commit -m "feat: cut order server (Flask app for DO App Platform)"
   git push origin main
   ```

2. **DO Spaces bucket (~2min):**
   - DO Console → Spaces → Create Bucket
   - Name: `cut-orders`
   - Region: `NYC3`
   - File listing: Restricted
   - Generate Spaces access key — save `DO_SPACES_KEY` + `DO_SPACES_SECRET`

3. **Google service account JSON content:**
   - Open `C:\Users\Work\Claude Projects\AppyHour\shipping-perfomance-review-accd39ac4b78.json`
   - Copy entire contents (the JSON blob) — you'll paste as a secret in App Platform.

4. **LTF sheet share:** confirm the service account email (in the JSON, `client_email` field) has Viewer access to the LTF Google Sheet.

## Deploy

1. **DO Console → Apps → Create App**
2. Choose **GitHub** → select `kurt-lgtm/AppyHour` → branch `main`
3. **Source Directory:** `cut_order_server`
4. App Platform should auto-detect Python; if not, use:
   - Environment: Python
   - Build command: `pip install -r requirements.txt`
   - Run command: `gunicorn -c gunicorn_conf.py wsgi:application`
   - HTTP port: `8080`
5. Plan: **Basic ($5/mo, 512MB)** is enough for current load.
6. **Set environment variables** (use `.do/app.yaml` as reference). Mark these as **SECRET**:
   - `FLASK_SECRET_KEY` (generate: `python -c "import secrets;print(secrets.token_hex(32))"`)
   - `APP_PASSWORD` (pick something memorable)
   - `SHOPIFY_ACCESS_TOKEN`
   - `RECHARGE_TOKEN`
   - `GOOGLE_SVC_ACCOUNT_JSON_CONTENT` (paste entire JSON file content)
   - `DO_SPACES_KEY`
   - `DO_SPACES_SECRET`

   Non-secret:
   - `FLASK_ENV=production`
   - `SHOPIFY_STORE_URL=elevatefoods`
   - `SHOPIFY_API_VERSION=2026-04`
   - `LTF_SHEET_ID=1Obz-Ib6KsjhB83NiRlFFCLFk9UjHr_YZl8lONl6VzKw`
   - `DO_SPACES_ENDPOINT=https://nyc3.digitaloceanspaces.com`
   - `DO_SPACES_REGION=nyc3`
   - `DO_SPACES_BUCKET=cut-orders`
   - `GUNICORN_WORKERS=2`

7. **Deploy.** First build takes ~3-5 min.

## Verify

- DO assigns a URL like `cut-order-xxxxx.ondigitalocean.app`
- Visit it → expect redirect to `/login`
- Enter `APP_PASSWORD` → land on index
- Click **Compute ratios** — should return JSON of empirical first-order ratios from trailing 90d Shopify (takes ~30-60s)
- Fill some AHB-X numbers, hit **Generate cut order** → after ~60-90s a download link to the xlsx in DO Spaces

## Custom domain (optional)

- App Platform → Settings → Domains → Add Domain
- Add `cut.elevatefoods.co` (or similar)
- DO auto-issues TLS via Let's Encrypt

## Auto-redeploy

`deploy_on_push: true` in `.do/app.yaml` means any push to `main` triggers a rebuild. To disable, edit in console.

## Troubleshooting

- **LTF read fails:** check service account is shared on the sheet (Viewer is enough)
- **Spaces upload fails:** verify `DO_SPACES_KEY` has write access to bucket
- **Recharge timeout:** retry — Recharge has 30min outages; the client retries 5x with backoff
- **App returns 500:** check Runtime Logs in DO Console
