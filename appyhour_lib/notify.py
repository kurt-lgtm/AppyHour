"""Best-effort operational notifications for AppyHour jobs."""
from __future__ import annotations

import json
import os
from datetime import datetime
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
    except Exception:
        pass


def notify(msg: str, level: str = "info") -> None:
    """Post to Slack when configured; otherwise append to fallback log.

    This function is intentionally fail-silent so scheduled jobs never fail
    solely because alerting is unavailable.
    """
    webhook = os.environ.get("AH_SLACK_WEBHOOK", "").strip()
    if not webhook:
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
