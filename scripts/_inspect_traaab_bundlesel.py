"""Throwaway: inspect bundle_selection internals + charge metadata for TR-AAB
subs to find a real 'customer-edited' signal (created_at vs updated_at, item
variance vs a default board, note/order_attributes, orders_count)."""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

import requests

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
from cut_order_server.app.creds import get_recharge_token  # noqa: E402

H = {"X-Recharge-Access-Token": get_recharge_token(), "X-Recharge-Version": "2021-11"}
COHORT = json.loads((_ROOT.parent / "_outputs" / "cache" / "traaab_cohort.json").read_text())


def rc(path, params=None):
    r = requests.get(f"https://api.rechargeapps.com{path}", headers=H, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


# 1) dump one full bundle_selection to see available fields
sample_cid = None
first = COHORT[0]
print("charge_id", first["charge_id"], "customer", first["customer_id"])
ch = rc(f"/charges/{first['charge_id']}")["charge"]
tray_li = next(li for li in ch["line_items"] if (li.get("sku") or "").upper() == "TR-AAB")
pid = tray_li["purchase_item_id"]
bs = rc("/bundle_selections", {"purchase_item_ids": pid})["bundle_selections"]
print("\n--- bundle_selection[0] full ---")
print(json.dumps(bs[0], indent=2, default=str)[:3000])

# 2) across 15 subs: compare created_at vs updated_at (edited?) + item signature
print("\n--- edited-flag + item signature across 15 subs ---")
sigs = Counter()
edited = 0
for r in COHORT[:15]:
    try:
        ch = rc(f"/charges/{r['charge_id']}")["charge"]
        li = next(x for x in ch["line_items"] if (x.get("sku") or "").upper() == "TR-AAB")
        pid = li["purchase_item_id"]
        sel = rc("/bundle_selections", {"purchase_item_ids": pid})["bundle_selections"]
        if not sel:
            print(r["charge_id"], "NO SELECTION")
            continue
        s = sel[0]
        items = tuple(sorted((it.get("external_variant_id"), it.get("quantity")) for it in s.get("items", [])))
        sig = hash(items)
        sigs[sig] += 1
        ca, ua = s.get("created_at"), s.get("updated_at")
        was_edited = bool(ca and ua and ua > ca)
        edited += was_edited
        print(f"sub {pid} items={len(s.get('items', []))} edited={was_edited} ca={ca} ua={ua}")
        time.sleep(0.3)
    except Exception as e:
        print(r["charge_id"], "ERR", e)
print(f"\ndistinct item signatures among 15: {len(sigs)}  (1 = all identical board)")
print(f"edited (updated_at>created_at): {edited}/15")
