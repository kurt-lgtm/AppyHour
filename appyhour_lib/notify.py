"""Best-effort operational notifications for AppyHour jobs.

Two delivery channels, both optional and both fail-silent:

* **Email (Gmail SMTP)** — the primary channel. Alerts land in the ops inbox
  with an ``[AppyHour <LEVEL>]`` subject prefix (the same channel verified by
  hand on 2026-07-08). Configured via ``AH_ALERT_EMAIL_TO`` plus Gmail SMTP
  credentials (env vars, or reused from the InventoryReorder settings JSON).
* **Slack** — posts to ``AH_SLACK_WEBHOOK`` when that webhook exists.

Historically only Slack was wired, and ``AH_SLACK_WEBHOOK`` was never set
(KURT-TODO #4), so every alert silently fell through to ``notify_fallback.log``
and was never seen. Email closes that gap without depending on the webhook.
"""
from __future__ import annotations

import json
import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.utils import formatdate
from pathlib import Path


def _app_dir() -> Path:
    base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    path = base / "AppyHour"
    path.mkdir(parents=True, exist_ok=True)
    return path


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
    except Exception:  # noqa: S110 — logging is itself best-effort; never raise
        pass


def _settings() -> dict:
    """Load the InventoryReorder settings JSON, if present.

    Lets email reuse the Gmail SMTP credentials already configured for
    depletion reports instead of duplicating them. An explicit
    ``AH_SETTINGS_JSON`` path wins; otherwise the shared
    ``%APPDATA%/AppyHour/`` location is tried.
    """
    candidates = []
    override = os.environ.get("AH_SETTINGS_JSON", "").strip()
    if override:
        candidates.append(Path(override))
    candidates.append(_app_dir() / "inventory_reorder_settings.json")
    for path in candidates:
        try:
            if path.is_file():
                with path.open("r", encoding="utf-8") as fp:
                    data = json.load(fp)
                if isinstance(data, dict):
                    return data
        except Exception:  # noqa: S112 — unreadable settings must not block alerts
            continue
    return {}


def _email_config() -> dict | None:
    """Resolve SMTP config from env vars, falling back to settings JSON.

    Returns ``None`` when email isn't configured (no recipient or no
    credentials) so the caller can skip the channel quietly.
    """
    settings = None

    def resolve(env_key: str, settings_key: str, default: str = "") -> str:
        nonlocal settings
        val = os.environ.get(env_key, "").strip()
        if val:
            return val
        if settings is None:
            settings = _settings()
        return str(settings.get(settings_key, default) or "").strip()

    user = resolve("AH_SMTP_USER", "smtp_user")
    password = resolve("AH_SMTP_PASSWORD", "smtp_password")
    to_addr = resolve("AH_ALERT_EMAIL_TO", "depletion_email_to") or user
    if not (user and password and to_addr):
        return None

    return {
        "host": resolve("AH_SMTP_HOST", "smtp_host", "smtp.gmail.com"),
        "port": int(resolve("AH_SMTP_PORT", "smtp_port", "587") or "587"),
        "user": user,
        "password": password,
        "from_addr": resolve("AH_ALERT_EMAIL_FROM", "depletion_email_from") or user,
        "to_addrs": [a.strip() for a in to_addr.replace(";", ",").split(",") if a.strip()],
    }


def _send_email(msg: str, level: str) -> tuple[bool, str]:
    """Send the alert via Gmail SMTP. Returns (sent, reason_if_not)."""
    cfg = _email_config()
    if cfg is None:
        return False, "email not configured (need AH_ALERT_EMAIL_TO + SMTP creds)"

    subject_prefix = f"[AppyHour {level.upper()}] "
    # Keep the subject to a sane length; the full message is always in the body.
    subject = subject_prefix + msg.splitlines()[0][:200]

    email = MIMEText(msg, "plain", "utf-8")
    email["Subject"] = subject
    email["From"] = cfg["from_addr"]
    email["To"] = ", ".join(cfg["to_addrs"])
    email["Date"] = formatdate(localtime=True)

    try:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as server:
            server.starttls()
            server.login(cfg["user"], cfg["password"])
            server.sendmail(cfg["from_addr"], cfg["to_addrs"], email.as_string())
        return True, ""
    except Exception as exc:
        return False, f"smtp {type(exc).__name__}: {exc}"


def _send_slack(msg: str, level: str) -> tuple[bool, str]:
    """Post the alert to the Slack webhook. Returns (sent, reason_if_not)."""
    webhook = os.environ.get("AH_SLACK_WEBHOOK", "").strip()
    if not webhook:
        return False, "missing AH_SLACK_WEBHOOK"

    try:
        import requests

        resp = requests.post(
            webhook,
            json={"text": f"[{level.upper()}] {msg}"},
            timeout=10,
        )
        if resp.status_code >= 400:
            return False, f"slack http {resp.status_code}: {resp.text[:200]}"
        return True, ""
    except Exception as exc:
        return False, f"slack {type(exc).__name__}: {exc}"


def notify(msg: str, level: str = "info") -> None:
    """Deliver an operational alert to email and/or Slack, best-effort.

    Both channels are attempted when configured. This function is
    intentionally fail-silent so scheduled jobs never fail solely because
    alerting is unavailable. If no channel accepts the message, it is
    appended to ``notify_fallback.log`` so nothing is lost.
    """
    reasons = []
    delivered = False

    for send in (_send_email, _send_slack):
        sent, reason = send(msg, level)
        if sent:
            delivered = True
        elif reason:
            reasons.append(reason)

    if not delivered:
        _fallback(msg, level, "; ".join(reasons) or "no channel configured")
