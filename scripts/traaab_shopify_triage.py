"""TR-AAB speck triage — Shopify side.

The _SHIP_2026-06-29 Shopify orders are the multi-tray "Gourmet Bites" boxes.
Classify each TR-AAB-bearing order by:
  - customized  -> order carries tag BOX_CUSTOMIZED_POST_CHECKOUT
  - first_order -> tag "Subscription First Order" (preset; not customized)
  - reship      -> tag contains "reship" (EXCLUDE from swappable)
  - logged_in   -> order email resolves to a Recharge customer that appears in
                   /events?verb=login since --since

Counts AAB UNITS (qty can be 2/box). Read-only.

Usage:
    /c/Users/Work/anaconda3/python.exe scripts/traaab_shopify_triage.py --since 2026-05-25
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "InventoryReorder" / "fulfillment_web"))
from appyhour_lib.credentials import get_shopify_credentials  # noqa: E402
from cut_order_server.app.creds import get_recharge_token  # noqa: E402
from shopify_bulk import _gql_post  # noqa: E402

SHIP_TAG = "_SHIP_2026-06-29"
TARGET = "TR-AAB"
CUSTOM_TAG = "box_customized_post_checkout"
FIRST_TAG = "subscription first order"
OUT = _ROOT.parent / "_outputs"
H = {"X-Recharge-Access-Token": get_recharge_token(), "X-Recharge-Version": "2021-11"}


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


def login_cids(since: str) -> set[int]:
    out: set[int] = set()
    cursor = None
    while True:
        params = {"cursor": cursor, "limit": 250} if cursor else {
            "verb": "login", "sort_by": "created_at-desc", "limit": 250}
        data = rc_get("/events", params)
        evs = data.get("events", [])
        if not evs:
            break
        stop = False
        for e in evs:
            if e.get("created_at", "") < since:
                stop = True
                break
            if e.get("customer_id"):
                out.add(e["customer_id"])
        if stop:
            break
        cursor = data.get("next_cursor")
        if not cursor:
            break
        time.sleep(0.5)
    print(f"login cids since {since}: {len(out)}")
    return out


def fetch_shopify_aab_orders() -> list[dict]:
    gql = """
    query($cursor:String,$q:String!){
      orders(first:100, after:$cursor, query:$q){
        edges{ node{
          name email tags
          customAttributes{ key value }
          lineItems(first:60){ edges{ node{ sku quantity }}}
        }}
        pageInfo{ hasNextPage endCursor }
      }
    }"""
    out = []
    cursor = None
    while True:
        data = _gql_post(*SHOP, gql, {"cursor": cursor, "q": f"(tag:{SHIP_TAG})"})
        conn = data.get("orders") or {}
        for e in conn.get("edges", []):
            n = e["node"]
            units = sum(
                (li["node"].get("quantity") or 0)
                for li in n["lineItems"]["edges"]
                if (li["node"].get("sku") or "").upper() == TARGET and (li["node"].get("quantity") or 0) > 0
            )
            if units <= 0:
                continue
            attrs = {a["key"]: a["value"] for a in (n.get("customAttributes") or [])}
            sub_ids = [s.strip() for s in (attrs.get("rc_subscription_ids") or "").split(",") if s.strip()]
            out.append(
                {
                    "name": n["name"],
                    "email": (n.get("email") or "").strip().lower(),
                    "tags": [t.strip().lower() for t in (n.get("tags") or [])],
                    "aab_units": units,
                    "rc_charge_id": attrs.get("rc_charge_id"),
                    "rc_subscription_ids": sub_ids,
                }
            )
        page = conn.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        cursor = page.get("endCursor")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-03-01")  # generous login look-back
    args = ap.parse_args()

    orders = fetch_shopify_aab_orders()
    n_units = sum(o["aab_units"] for o in orders)
    print(f"Shopify TR-AAB orders: {len(orders)}  units: {n_units}")

    logins = login_cids(args.since)

    # resolve email -> recharge customer_id (dedupe), then logged_in
    email_cid: dict[str, int | None] = {}
    for o in orders:
        em = o["email"]
        if em and em not in email_cid:
            data = rc_get("/customers", {"email": em})
            cs = data.get("customers", [])
            email_cid[em] = cs[0]["id"] if cs else None
            time.sleep(0.3)

    # customization signal #2: Recharge bundle EDITED (portal customization that
    # the Shopify tag often misses). Resolve each order's subscription(s).
    bundle_edited_cache: dict[str, bool] = {}

    def sub_edited(sub_id: str) -> bool:
        if sub_id in bundle_edited_cache:
            return bundle_edited_cache[sub_id]
        edited = False
        try:
            bs = rc_get("/bundle_selections", {"purchase_item_ids": sub_id}).get("bundle_selections", [])
            if bs:
                s = bs[0]
                ca, ua = s.get("created_at"), s.get("updated_at")
                edited = bool(ca and ua and ua > ca)
            time.sleep(0.3)
        except Exception:
            edited = False
        bundle_edited_cache[sub_id] = edited
        return edited

    for o in orders:
        cid = email_cid.get(o["email"])
        o["rc_customer_id"] = cid
        o["logged_in"] = cid in logins if cid else False
        o["tag_customized"] = CUSTOM_TAG in o["tags"]
        o["bundle_edited"] = any(sub_edited(s) for s in o.get("rc_subscription_ids", []))
        o["customized"] = o["tag_customized"] or o["bundle_edited"]
        o["first_order"] = FIRST_TAG in o["tags"]
        o["reship"] = any("reship" in t for t in o["tags"])
        # RULE: protected if logged-in OR customized. Swap ONLY if neither.
        o["protected"] = o["logged_in"] or o["customized"]
        o["swappable"] = (not o["protected"]) and (not o["reship"])

    def usum(rows):
        return sum(r["aab_units"] for r in rows)

    non_reship = [o for o in orders if not o["reship"]]
    protected = [o for o in non_reship if o["protected"]]
    b1 = [o for o in non_reship if o["swappable"]]  # neither logged-in nor customized
    only_login = [o for o in non_reship if o["logged_in"] and not o["customized"]]
    only_cust = [o for o in non_reship if o["customized"] and not o["logged_in"]]
    both = [o for o in non_reship if o["logged_in"] and o["customized"]]
    reships = [o for o in orders if o["reship"]]
    tag_miss = [o for o in non_reship if o["bundle_edited"] and not o["tag_customized"]]

    print("\n=== SHOPIFY TR-AAB (units / orders) — RULE: swap only if NOT login AND NOT customized ===")
    print(f"customized (tag OR bundle-edit): {usum([o for o in non_reship if o['customized']]):>4}u  {len([o for o in non_reship if o['customized']]):>4}o")
    print(f"  of which tag MISSED (bundle-edit only): {usum(tag_miss):>4}u  {len(tag_miss):>4}o  <- would be wrongly swapped tag-only")
    print(f"logged-in:                       {usum([o for o in non_reship if o['logged_in']]):>4}u  {len([o for o in non_reship if o['logged_in']]):>4}o")
    print("--- buckets ---")
    print(f"PROTECTED login-only:            {usum(only_login):>4}u  {len(only_login):>4}o")
    print(f"PROTECTED cust-only:             {usum(only_cust):>4}u  {len(only_cust):>4}o")
    print(f"PROTECTED both:                  {usum(both):>4}u  {len(both):>4}o")
    print(f"PROTECTED total:                 {usum(protected):>4}u  {len(protected):>4}o")
    print(f"SWAPPABLE (neither):             {usum(b1):>4}u  {len(b1):>4}o")
    print(f"reships (excluded):              {usum(reships):>4}u  {len(reships):>4}o")
    print(f"\n>>> Shopify SWAPPABLE units: {usum(b1)}")

    OUT.joinpath("reports").mkdir(parents=True, exist_ok=True)
    p = OUT / "reports" / "traaab_shopify_triage.json"
    p.write_text(json.dumps({"orders": orders}, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {p}")


SHOP = get_shopify_credentials()
if __name__ == "__main__":
    main()
