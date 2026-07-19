"""Best-effort operational notifications for AppyHour jobs.

Delivery order: Slack webhook (AH_SLACK_WEBHOOK) if set; else for level=critical,
email via the Gmail app password already in AppyHour/.env (IMAP_SMTP_USER /
IMAP_SMTP_APP_PASSWORD -> smtp.gmail.com:587, recipient AH_ALERT_EMAIL or the
sending account itself). Everything undeliverable lands in notify_fallback.log.
Added 2026-07-08: shipping.db healthcheck criticals sat silent in the fallback
log for a week because no webhook was ever configured.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

_ENV_FILE = Path(r"C:\Users\Work\Claude Projects\AppyHour\.env")


def _app_dir() -> Path:
    base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    path = base / "AppyHour"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_env_file() -> dict:
    out: dict[str, str] = {}
    try:
        for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return out


def _email(msg: str, level: str) -> bool:
    """Send a critical alert by email. Returns True on success."""
    env = _load_env_file()
    user = env.get("IMAP_SMTP_USER", "")
    pwd = env.get("IMAP_SMTP_APP_PASSWORD", "")
    if not user or not pwd:
        return False
    to_addr = os.environ.get("AH_ALERT_EMAIL", "").strip() or user
    try:
        import smtplib
        from email.message import EmailMessage
        from email.utils import formatdate

        m = EmailMessage()
        m["From"] = user
        m["To"] = to_addr
        m["Subject"] = f"[AppyHour {level.upper()}] {msg[:120]}"
        m["Date"] = formatdate(localtime=True)
        m.set_content(msg)
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as s:
            s.starttls()
            s.login(user, pwd)
            s.send_message(m)
        return True
    except Exception:
        return False


def _fallback(message: str, level: str, reason: str) -> None:
    try:
        payload = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "level": level,
            "message": message,
            "reason": reason,
        }
        with (_app_dir() / "notify_fallback.log").open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(payload, ensure_ascii=True) + "\n")
    except Exception:
        pass


def notify(msg: str, level: str = "info") -> None:
    """Post to Slack when configured; otherwise append to fallback log.

    This function is intentionally fail-silent so scheduled jobs never fail
    solely because alerting is unavailable.
    """
    webhook = os.environ.get("AH_SLACK_WEBHOOK", "").strip()
    if not webhook:
        if level == "critical" and _email(msg, level):
            _fallback(msg, level, "no webhook - delivered by email")
            return
        _fallback(msg, level, "missing AH_SLACK_WEBHOOK")
        return

    try:
        import requests

        resp = requests.post(
            webhook,
            json={"text": f"[{level.upper()}] {msg}"},
            timeout=10,
        )
        if resp.status_code >= 400:
            _fallback(msg, level, f"slack http {resp.status_code}: {resp.text[:200]}")
    except Exception as exc:
        _fallback(msg, level, f"{type(exc).__name__}: {exc}")
