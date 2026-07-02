"""TR-AAB speck triage — step 2 of 2: join cohort to portal logins.

Reads the cohort cache from step 1 (traaab_cohort.json), pulls Recharge
portal login events (/events?verb=login, newest-first, stop at cutoff), then
cross-tabs customized x logged_in so we know the protected vs swappable counts.

Usage:
    /c/Users/Work/anaconda3/python.exe scripts/traaab_logins_join.py --since 2026-05-25
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from cut_order_server.app.creds import get_recharge_token  # noqa: E402

OUT_DIR = _ROOT.parent / "_outputs" / "cache"
REPORT_DIR = _ROOT.parent / "_outputs" / "reports"
COHORT = OUT_DIR / "traaab_cohort.json"

H = {
    "X-Recharge-Access-Token": get_recharge_token(),
    "X-Recharge-Version": "2021-11",
    "Content-Type": "application/json",
}


def rc_get(path, params=None, retries=5, timeout=30):
    last = None
    for a in range(retries):
        try:
            r = requests.get(f"https://api.rechargeapps.com{path}", headers=H, params=params, timeout=timeout)
            if r.status_code == 429:
                time.sleep(int(r.headers.get("retry-after", "5")))
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout as e:
            last = e
            time.sleep(2 * (a + 1))
        except Exception as e:
            last = e
            time.sleep(2)
    raise last


def login_map(since: str) -> dict[int, str]:
    """{customer_id: last_login_iso} for logins >= since (cutoff)."""
    out: dict[int, str] = {}
    cursor = None
    pages = 0
    while True:
        if cursor:
            params = {"cursor": cursor, "limit": 250}
        else:
            params = {"verb": "login", "sort_by": "created_at-desc", "limit": 250}
        data = rc_get("/events", params)
        evs = data.get("events", [])
        if not evs:
            break
        stop = False
        for e in evs:
            ca = e.get("created_at", "")
            if ca < since:
                stop = True
                break
            cid = e.get("customer_id")
            if cid and cid not in out:
                out[cid] = ca
        pages += 1
        if stop:
            break
        cursor = data.get("next_cursor")
        if not cursor:
            break
        time.sleep(0.5)
    print(f"login events scanned: {pages} pages, {len(out)} distinct customers since {since}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-05-25", help="login cutoff (ISO date)")
    args = ap.parse_args()

    rows = json.loads(COHORT.read_text(encoding="utf-8"))
    logins = login_map(args.since)

    for r in rows:
        cid = r.get("customer_id")
        r["logged_in"] = cid in logins
        r["last_login"] = logins.get(cid, "")

    n = len(rows)
    cust = [r for r in rows if r["customized"]]
    nocust = [r for r in rows if not r["customized"]]
    cust_login = [r for r in cust if r["logged_in"]]
    cust_nologin = [r for r in cust if not r["logged_in"]]
    nocust_login = [r for r in nocust if r["logged_in"]]
    nocust_nologin = [r for r in nocust if not r["logged_in"]]

    print(f"\nTR-AAB queued charges: {n}")
    print("                       logged-in   not       total")
    print(f"customized            {len(cust_login):>9}  {len(cust_nologin):>6}  {len(cust):>8}")
    print(f"NOT customized        {len(nocust_login):>9}  {len(nocust_nologin):>6}  {len(nocust):>8}")
    print(f"total                 {len(cust_login)+len(nocust_login):>9}  "
          f"{len(cust_nologin)+len(nocust_nologin):>6}  {n:>8}")
    print(f"\nPROTECTED (customized AND logged-in): {len(cust_login)}")
    print(f"SWAPPABLE (the rest):                 {n - len(cust_login)}")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / "traaab_speck_triage.json"
    out.write_text(
        json.dumps(
            {
                "summary": {
                    "total_charges": n,
                    "customized_logged_in": len(cust_login),
                    "customized_not_logged_in": len(cust_nologin),
                    "not_customized_logged_in": len(nocust_login),
                    "not_customized_not_logged_in": len(nocust_nologin),
                    "protected": len(cust_login),
                    "swappable": n - len(cust_login),
                },
                "rows": rows,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
