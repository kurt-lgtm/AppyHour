"""Outbound Gorgias notice: MT-PSS (Sweet & Spicy Prosciutto) out of stock.

One unassigned ticket per recipient, one outbound message each. Dry-run by default.

🔴 Live customer write. `~/.claude/rules/live-writes.md`: a customer message is NEVER
sent without Kurt's explicit go to send. --apply exists so the go is a deliberate act,
not a side effect of running the script.
🔴 Persist the sent-log per send, never at end of loop (batch-sender burn 2026-09-08:
an end-of-loop write produced 18 duplicate emails on re-run).

Usage:
    python scripts/cs/send_pss_oos_notice.py            # dry-run
    python scripts/cs/send_pss_oos_notice.py --apply    # send
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent / "AppyHourMCP"))
from tools._gorgias_internal import get_auth  # noqa: E402

SENT_LOG = Path(r"C:\Users\Work\Claude Projects\_outputs\logs\pss_oos_notice_sent.jsonl")
FROM_EMAIL = "hi@appyhourbox.com"
SUBJECT = "A quick swap needed in your next AppyHour box"
LOGIN_URL = "https://appyhourbox.com/account/login"

RECIPIENTS = [
    # first names pulled from Recharge customer records, not inferred from the handle
    {"email": "mikeferretti@mac.com", "name": "Mike"},
    {"email": "dodeehill@gmail.com", "name": "Dodee"},
    {"email": "patriciashroyer7@icloud.com", "name": "Patricia"},
]


def body_html(first: str) -> str:
    return (
        f"<p>Hi {first},</p>"
        "<p>The Sweet &amp; Spicy Prosciutto in your upcoming box is out of stock, "
        "so we won't be able to include it.</p>"
        f'<p>Swap it for anything you like &mdash; takes about a minute: '
        f'<a href="{LOGIN_URL}">Login Here</a></p>'
        "<p>&mdash; The AppyHour Team</p>"
    )


def already_sent() -> set[str]:
    if not SENT_LOG.exists():
        return set()
    return {json.loads(l)["email"] for l in SENT_LOG.read_text(encoding="utf-8").splitlines() if l.strip()}


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    auth, base_url = get_auth()
    done = already_sent()
    todo = [r for r in RECIPIENTS if r["email"] not in done]
    print(f"{len(RECIPIENTS)} recipients | {len(done)} already sent | {len(todo)} to send | APPLY={apply}")
    for r in todo:
        print(f"  -> {r['email']}  subject={SUBJECT!r}")
    if not apply:
        print("\nDRY-RUN (no messages sent). Re-run with --apply.")
        return 0

    SENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    ok = err = 0
    for r in todo:
        payload = {
            "subject": SUBJECT,
            "channel": "email",
            "via": "api",
            "assignee_user": None,          # unassigned, per Kurt 2026-09-09
            "customer": {"email": r["email"]},
            "messages": [{
                "channel": "email",
                "via": "api",
                "from_agent": True,
                "sender": {"email": FROM_EMAIL},
                "receiver": {"email": r["email"]},
                "subject": SUBJECT,
                "body_html": body_html(r["name"]),
                "source": {
                    "type": "email",
                    "from": {"address": FROM_EMAIL},
                    "to": [{"address": r["email"]}],
                },
            }],
        }
        resp = requests.post(f"{base_url}/tickets", auth=auth, json=payload, timeout=30)
        if resp.status_code in (200, 201):
            tid = resp.json().get("id")
            # per-send persistence (see module docstring)
            with open(SENT_LOG, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"email": r["email"], "ticket": tid,
                                     "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}) + "\n")
            print(f"  SENT {r['email']} ticket={tid}")
            ok += 1
        else:
            print(f"  ERR  {r['email']} {resp.status_code} {resp.text[:200]}")
            err += 1
        time.sleep(1.0)
    print(f"\nsent={ok} err={err} log={SENT_LOG}")
    return 1 if err else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
