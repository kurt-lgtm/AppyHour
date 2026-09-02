"""Best-effort operational notifications for AppyHour jobs.

Delivery order: Slack chat.postMessage to #kurt-ops (AH_SLACK_BOT_TOKEN ->
AH_SLACK_CHANNEL, default C0BT47XG8CW); else for level=critical, email via the
Gmail app password already in AppyHour/.env (IMAP_SMTP_USER /
IMAP_SMTP_APP_PASSWORD -> smtp.gmail.com:587, recipient AH_ALERT_EMAIL or the
sending account itself). Everything undeliverable lands in notify_fallback.log.
Added 2026-07-08: shipping.db healthcheck criticals sat silent in the fallback
log for a week because no webhook was ever configured.

AH_SLACK_WEBHOOK IS NO LONGER READ (Kurt 2026-08-27). That property is an
incoming webhook bound to the PUBLIC #reships channel -- a webhook's destination
lives in the URL, so it can never be re-pointed from code, and every consumer
that read it published ops noise to a channel Dan and RMFG are in. All AppyHour
ops alerting now goes to the private #kurt-ops (Kurt + appyhour-ops-reader
only), which chat.postMessage CAN re-point via AH_SLACK_CHANNEL without a code
change. Do not reintroduce a webhook path here: a fallback to the public channel
is exactly the silent-degrade this replaced.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

_ENV_FILE = Path(r"C:\Users\Work\Claude Projects\AppyHour\.env")

# Private #kurt-ops (Kurt + appyhour-ops-reader). Override with AH_SLACK_CHANNEL.
KURT_OPS_CHANNEL = "C0BT47XG8CW"


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
    token = os.environ.get("AH_SLACK_BOT_TOKEN", "").strip() or _load_env_file().get(
        "AH_SLACK_BOT_TOKEN", ""
    ).strip()
    channel = os.environ.get("AH_SLACK_CHANNEL", "").strip() or KURT_OPS_CHANNEL
    if not token:
        if level == "critical" and _email(msg, level):
            _fallback(msg, level, "no bot token - delivered by email")
            return
        _fallback(msg, level, "missing AH_SLACK_BOT_TOKEN")
        return

    try:
        import requests

        resp = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json={"channel": channel, "text": f"[{level.upper()}] {msg}"},
            timeout=10,
        )
        # Slack returns HTTP 200 with {"ok": false, "error": ...} on a refused
        # post (not_in_channel, invalid_auth). A status check alone would report
        # every one of those as delivered.
        if resp.status_code >= 400:
            _fallback(msg, level, f"slack http {resp.status_code}: {resp.text[:200]}")
            return
        body = resp.json()
        if not body.get("ok"):
            _fallback(msg, level, f"slack api: {body.get('error')}")
    except Exception as exc:
        _fallback(msg, level, f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
# CLI  (added 2026-09-01)
# --------------------------------------------------------------------------
# 🔴 WHY THIS EXISTS. Every scheduled Claude routine that wanted to reach Kurt
# had to hand-roll its own Slack call, because this module was importable but
# not RUNNABLE. Prose in ~14 SKILL.md files each carried a literal destination
# (Kurt's DM id U08R19137UL) -- the same defect as a webhook URL: the target is
# baked into the caller, so re-pointing it means editing every caller, and the
# two conventions drifted (some routines notify()'d to #kurt-ops, the rest DM'd).
# This gives an agent-composed message the SAME single poster and the SAME
# single destination the scripts already use. Do NOT add a --channel flag: the
# destination is AH_SLACK_CHANNEL / KURT_OPS_CHANNEL and nowhere else, which is
# the entire point.
#
# 🔴 AND IT EXITS LOUD. notify() is deliberately fail-SILENT so a scheduled job
# never dies because alerting is down -- right for a job doing other work, WRONG
# for a caller whose only purpose is delivery, because "posted" and "written to
# a log nobody reads" would look identical (the 2026-08-26 freshness-sweep
# outage). So we measure notify_fallback.log across the call: if it GREW, the
# post did not land, and we exit non-zero naming the reason notify recorded.
# Same detection as freshness_sweep._notify_slack.


def _main(argv: list[str] | None = None) -> int:
    """Deliver ONE message file to the canonical ops channel. 0 = confirmed."""
    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m appyhour_lib.notify",
        description="Post a message to the canonical AppyHour ops channel.",
    )
    ap.add_argument("--file", required=True,
                    help="UTF-8 file holding the message body (compose it first, then send).")
    ap.add_argument("--level", default="info",
                    choices=["info", "warn", "error", "critical"],
                    help="critical also buys the SMTP fallback when no bot token exists.")
    args = ap.parse_args(argv)

    try:
        text = Path(args.file).read_text(encoding="utf-8").strip()
    except Exception as exc:
        print(f"NOT DELIVERED: cannot read --file {args.file} ({type(exc).__name__}: {exc})")
        return 2
    if not text:
        print(f"NOT DELIVERED: --file {args.file} is empty (nothing to post)")
        return 2

    log = _app_dir() / "notify_fallback.log"
    try:
        before = log.stat().st_size if log.exists() else 0
    except Exception:
        before = None

    notify(text, level=args.level)

    if before is None:
        print("UNVERIFIED: notify() called but notify_fallback.log is unreadable, "
              "so delivery could not be confirmed. Treat as NOT delivered.")
        return 1
    try:
        after = log.stat().st_size if log.exists() else 0
    except Exception:
        after = before
    if after > before:
        reason = ""
        try:
            with log.open("r", encoding="utf-8", errors="replace") as fp:
                fp.seek(before)
                for line in fp:
                    if line.strip():
                        reason = json.loads(line).get("reason", "")
        except Exception:
            reason = "reason unreadable in notify_fallback.log"
        print(f"NOT DELIVERED: notify() fell back to notify_fallback.log ({reason or 'no reason recorded'})")
        return 1

    print(f"delivered to the ops channel ({len(text)} chars, level={args.level})")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by tests via _main()
    raise SystemExit(_main())
