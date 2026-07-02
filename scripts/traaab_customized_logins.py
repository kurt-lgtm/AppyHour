"""TR-AAB (American Artisan Board) speck-shortage triage.

Read-only. For the current ship week, find queued Recharge charges that carry
TR-AAB and classify each by:
  - customized?  -> bundle_selections present for the tray subscription
  - logged_in?   -> customer appears in /events?verb=login within the window

Goal: size the PROTECTED set (customized AND logged-in -> leave alone) vs the
SWAPPABLE set (the rest), so we can shed TR-AAB demand to cover a speck shortage.

Step 1 of 2 — this builds + caches the TR-AAB cohort with a customization flag
and dumps one raw charge so we can confirm the customization signal.

Usage:
    /c/Users/Work/anaconda3/python.exe scripts/traaab_customized_logins.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from cut_order_server.app.creds import get_recharge_token  # noqa: E402

TARGET_SKU = "TR-AAB"
RC_SCHEDULED_MAX = "2026-06-27"  # Saturday inclusive
OUT_DIR = _ROOT.parent / "_outputs" / "cache"

H = {
    "X-Recharge-Access-Token": get_recharge_token(),
    "X-Recharge-Version": "2021-11",
    "Content-Type": "application/json",
}


def rc_get(path: str, params: dict | None = None, retries: int = 5, timeout: int = 30):
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


def fetch_traaab_charges() -> list[dict]:
    out: list[dict] = []
    cursor = None
    while True:
        if cursor:
            params = {"cursor": cursor, "limit": 250}
        else:
            params = {
                "status": "queued",
                "scheduled_at_max": RC_SCHEDULED_MAX,
                "limit": 250,
                "sort_by": "id-asc",
            }
        data = rc_get("/charges", params)
        batch = data.get("charges", [])
        if not batch:
            break
        for c in batch:
            if any((li.get("sku") or "").strip().upper() == TARGET_SKU for li in c.get("line_items", [])):
                out.append(c)
        cursor = data.get("next_cursor")
        if not cursor:
            break
        time.sleep(0.5)
    return out


def tray_sub_ids(charge: dict) -> list[int]:
    """purchase_item_id(s) backing the TR-AAB line(s) on this charge."""
    ids = []
    for li in charge.get("line_items", []):
        if (li.get("sku") or "").strip().upper() == TARGET_SKU:
            pid = li.get("purchase_item_id") or li.get("subscription_id")
            if pid:
                ids.append(pid)
    return ids


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    charges = fetch_traaab_charges()
    print(f"TR-AAB queued charges (<= {RC_SCHEDULED_MAX}): {len(charges)}")
    units = sum(
        (li.get("quantity") or 0)
        for c in charges
        for li in c.get("line_items", [])
        if (li.get("sku") or "").strip().upper() == TARGET_SKU
    )
    print(f"TR-AAB units: {units}")

    # dump one raw charge for signal confirmation
    if charges:
        sample = charges[0]
        keys = sorted(sample.keys())
        print("\n--- sample charge top-level keys ---")
        print(keys)
        tray_li = next(
            li for li in sample["line_items"] if (li.get("sku") or "").strip().upper() == TARGET_SKU
        )
        print("\n--- sample TR-AAB line_item ---")
        print(json.dumps(tray_li, indent=2, default=str)[:2500])

    # build cohort rows with REAL customization signal.
    # Trays are fixed bundles, so bundle_selections always exist (meaningless).
    # Customized = customer edited the selection (updated_at > created_at) OR
    #              item-count differs from the default board (DEFAULT_ITEMS).
    DEFAULT_ITEMS = 6
    rows = []
    seen_cid = set()
    for i, c in enumerate(charges):
        cust = c.get("customer") or {}
        cid = cust.get("id")
        email = cust.get("email")
        customized = False
        edited = False
        n_items = None
        for pid in tray_sub_ids(c):
            bs = rc_get("/bundle_selections", {"purchase_item_ids": pid})
            sels = bs.get("bundle_selections", [])
            if not sels:
                continue
            s = sels[0]
            items = s.get("items", [])
            n_items = len(items)
            ca, ua = s.get("created_at"), s.get("updated_at")
            edited = bool(ca and ua and ua > ca)
            if edited or n_items != DEFAULT_ITEMS:
                customized = True
            time.sleep(0.3)
            break
        rows.append(
            {
                "charge_id": c.get("id"),
                "customer_id": cid,
                "email": email,
                "scheduled_at": c.get("scheduled_at"),
                "customized": customized,
                "edited": edited,
                "n_items": n_items,
            }
        )
        seen_cid.add(cid)
        if (i + 1) % 25 == 0:
            print(f"  ...{i + 1}/{len(charges)} classified")

    cust_path = OUT_DIR / "traaab_cohort.json"
    cust_path.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
    n_cust = sum(1 for r in rows if r["customized"])
    print(f"\ncustomized charges: {n_cust}/{len(rows)}")
    print(f"unique customers: {len(seen_cid)}")
    print(f"Wrote {cust_path}")


if __name__ == "__main__":
    main()
