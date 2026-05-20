# Cut Order Server

Stage-1 demand generator + Stage-2 calculator. Flask on DigitalOcean.

Plan: `Claude-Projects/.claude/plans/2026-05-20-cut-order-server.md`

## Phase 1 (current): skeleton + auth

- Flask factory (`app/__init__.py`)
- Password gate via `APP_PASSWORD` env var (`app/auth.py`)
- Wedge mascot placeholder (`templates/base.html` inline SVG)
- Stub routes: `/`, `/login`, `/logout`, `/healthz`, `/demand` (501), `/calc` (501), `/history` (501)

## Local dev

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
cp .env.example .env              # then fill FLASK_SECRET_KEY + APP_PASSWORD
python -c "from app import create_app; create_app().run(debug=True, port=5000)"
```

Visit `http://localhost:5000` → redirected to `/login` → enter `APP_PASSWORD` → index.

## Production

Phase 7 — see plan section 4. systemd + gunicorn + nginx + certbot on Ubuntu 22.04 droplet.
