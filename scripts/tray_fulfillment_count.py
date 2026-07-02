"""This-week tray (TR-) fulfillment count.

Read-only. Joins two demand sources for the current ship week:
  - Shopify orders tagged _SHIP_2026-06-29  -> "Shopify" column
      sub-broken-out: orders also tagged "Subscription First Order"
  - Recharge queued charges scheduled <= Saturday 2026-06-27 -> "Recharge Pending"

Counts TR-* line item UNITS (fulfillable_quantity for Shopify, quantity for
Recharge), grouped by SKU + product name.

Usage:
    /c/Users/Work/anaconda3/python.exe scripts/tray_fulfillment_count.py
"""
from __future__ import annotations

import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from appyhour_lib.credentials import get_shopify_credentials  # noqa: E402
from cut_order_server.app.creds import get_recharge_token  # noqa: E402
sys.path.insert(0, str(_ROOT / "InventoryReorder" / "fulfillment_web"))
from shopify_bulk import _gql_post, gql_orders_by_tag  # noqa: E402

SHIP_TAG = "_SHIP_2026-06-29"
FIRST_ORDER_TAG = "subscription first order"  # case-insensitive match
RC_SCHEDULED_MAX = "2026-06-27"  # Saturday (inclusive)
TRAY_PREFIX = "TR-"

RC_HEADERS = {
    "X-Recharge-Access-Token": get_recharge_token(),
    "X-Recharge-Version": "2021-11",
    "Content-Type": "application/json",
}


def is_tray(sku: str | None) -> bool:
    return bool(sku) and sku.strip().upper().startswith(TRAY_PREFIX)


def resolve_tray_names(skus: list[str]) -> dict[str, str]:
    """Authoritative SKU -> product title via Shopify GraphQL variant lookup."""
    store, token = get_shopify_credentials()
    names: dict[str, str] = {}
    gql = """query($q:String!){productVariants(first:25,query:$q){
      edges{node{sku product{title}}}}}"""
    for sku in skus:
        try:
            data = _gql_post(store, token, gql, {"q": f"sku:{sku}"})
            for e in (data.get("productVariants") or {}).get("edges", []):
                n = e.get("node") or {}
                if (n.get("sku") or "").strip() == sku:
                    names[sku] = ((n.get("product") or {}).get("title") or "").strip()
                    break
        except Exception:
            pass
    return names


# ---------- Shopify ----------
def shopify_tray_counts() -> tuple[dict, dict, dict, int]:
    """Returns (ship_counts, first_counts, names, n_orders).

    Server-side tag filter via GraphQL (orders query: "tag:_SHIP_..."). Pulls
    every order carrying the ship tag regardless of fulfillment/open status so
    nothing already-actioned is silently dropped.
    """
    store, token = get_shopify_credentials()
    orders = gql_orders_by_tag(
        store, token, [SHIP_TAG], status="", fulfillment_status=""
    )
    ship_counts: dict[str, int] = defaultdict(int)
    first_counts: dict[str, int] = defaultdict(int)
    names: dict[str, str] = {}
    n_orders = 0
    for o in orders:
        tags = [t.strip().lower() for t in (o.get("tags") or "").split(",")]
        if SHIP_TAG.lower() not in tags:
            continue
        n_orders += 1
        is_first = FIRST_ORDER_TAG in tags
        for li in o.get("line_items", []):
            sku = li.get("sku")
            if not is_tray(sku):
                continue
            qty = li.get("fulfillable_quantity")
            if qty is None:
                qty = li.get("quantity", 0)
            if qty <= 0:
                continue
            sku = sku.strip()
            ship_counts[sku] += qty
            names.setdefault(sku, li.get("title") or li.get("name") or "")
            if is_first:
                first_counts[sku] += qty
    return ship_counts, first_counts, names, n_orders


# ---------- Recharge ----------
def fetch_recharge_charges() -> list[dict]:
    url = "https://api.rechargeapps.com/charges"
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
        r = requests.get(url, headers=RC_HEADERS, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        batch = data.get("charges", [])
        if not batch:
            break
        out.extend(batch)
        cursor = data.get("next_cursor")
        if not cursor:
            break
        time.sleep(0.5)
    return out


def recharge_tray_counts() -> tuple[dict, dict, int]:
    counts: dict[str, int] = defaultdict(int)
    names: dict[str, str] = {}
    n_charges = 0
    for c in fetch_recharge_charges():
        had_tray = False
        for li in c.get("line_items", []):
            sku = li.get("sku")
            if not is_tray(sku):
                continue
            had_tray = True
            qty = li.get("quantity", 0) or 0
            sku = sku.strip()
            counts[sku] += qty
            names.setdefault(sku, li.get("title") or "")
        if had_tray:
            n_charges += 1
    return counts, names, n_charges


def main() -> None:
    print(f"Shopify ship tag: {SHIP_TAG}")
    print(f"Recharge queued <= {RC_SCHEDULED_MAX} (Sat)\n")

    ship, first, snames, n_orders = shopify_tray_counts()
    print(f"Shopify orders w/ tag: {n_orders}")

    rc, rnames, n_charges = recharge_tray_counts()
    print(f"Recharge queued charges w/ tray: {n_charges}\n")

    all_skus = sorted(set(ship) | set(rc) | set(first))
    names = resolve_tray_names(all_skus)
    # fall back to whatever Recharge/Shopify line items carried
    for sku in all_skus:
        if not names.get(sku):
            names[sku] = rnames.get(sku) or snames.get(sku) or ""

    rows = []
    for sku in all_skus:
        rows.append(
            {
                "SKU": sku,
                "Name": names.get(sku, ""),
                "Shopify": ship.get(sku, 0),
                "Recharge Pending": rc.get(sku, 0),
                "Subscription First Order": first.get(sku, 0),
            }
        )

    w_sku = max([len(r["SKU"]) for r in rows] + [3])
    w_name = max([len(r["Name"]) for r in rows] + [4])
    hdr = f"{'SKU':<{w_sku}}  {'Name':<{w_name}}  {'Shopify':>7}  {'RC Pend':>7}  {'1stOrd':>6}"
    print(hdr)
    print("-" * len(hdr))
    tot_s = tot_r = tot_f = 0
    for r in rows:
        print(
            f"{r['SKU']:<{w_sku}}  {r['Name']:<{w_name}}  "
            f"{r['Shopify']:>7}  {r['Recharge Pending']:>7}  {r['Subscription First Order']:>6}"
        )
        tot_s += r["Shopify"]
        tot_r += r["Recharge Pending"]
        tot_f += r["Subscription First Order"]
    print("-" * len(hdr))
    print(
        f"{'TOTAL':<{w_sku}}  {'':<{w_name}}  {tot_s:>7}  {tot_r:>7}  {tot_f:>6}"
    )

    # write xlsx
    try:
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "Tray Fulfillment"
        ws.append(["SKU", "Name", "Shopify", "Recharge Pending", "Subscription First Order"])
        for r in rows:
            ws.append([r["SKU"], r["Name"], r["Shopify"], r["Recharge Pending"], r["Subscription First Order"]])
        ws.append(["TOTAL", "", tot_s, tot_r, tot_f])
        out_dir = _ROOT.parent / "_outputs" / "reports"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "tray_fulfillment_2026-06-29.xlsx"
        wb.save(out_path)
        print(f"\nWrote {out_path}")
    except ImportError:
        print("\n(openpyxl missing — skipped xlsx)")


if __name__ == "__main__":
    main()
