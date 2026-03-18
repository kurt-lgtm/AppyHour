#!/usr/bin/env python
"""
Inventory & Demand Report -- Weeks of 3/21 and 3/28
===================================================
1. Load 3/14 inventory snapshot (Total column)
2. Subtract depletions: 03-16 Sat + 03-17 Tue shipments
3. Week 1 demand (_SHIP_2026-03-23): Recharge queued charges + first orders
4. Week 2 demand (_SHIP_2026-03-30): Recharge queued charges + first orders
5. Output: SKU | Available | Wk1 | After Wk1 | Wk2
6. PR-CJAM and CEX-EC totals at bottom

Recharge API: MUST use X-Recharge-Version: 2021-11 for cursor pagination.
Without it, v1 silently caps at 250 results with no next_cursor.
"""

import csv
import json
import os
import sys
import time
from collections import defaultdict
from datetime import date

# -- Paths --
BASE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(BASE, "dist", "inventory_reorder_settings.json")
INV_CSV = os.path.join(BASE, "Orders RMFG_20260314 - 3_14 Inv.csv")
SHIPMENTS = os.path.join(BASE, "Shipments")
SAT_DEPLETION = os.path.join(SHIPMENTS, "AHB_WeeklyProductionQuery_03-16-26_vF.xlsx")
TUE_DEPLETION = os.path.join(SHIPMENTS, "AHB_WeeklyProductionQuery_03-17-26_vF.xlsx")

# Ship week boundaries
WK1_START = date(2026, 3, 18)
WK1_END = date(2026, 3, 23)
WK2_START = date(2026, 3, 24)
WK2_END = date(2026, 3, 30)

PICKABLE_PREFIXES = ("CH-", "MT-", "AC-")

# Curation resolution
KNOWN_CURATIONS = {
    "MONG", "MDT", "OWC", "SPN", "ALPN", "ALPT",
    "ISUN", "HHIGH", "NMS", "BYO", "SS", "GEN", "MS",
}
_MONTHLY_PATTERNS = {"AHB-MED", "AHB-LGE", "AHB-CMED", "AHB-CUR-MS",
                      "AHB-BVAL", "AHB-MCUST-MS", "AHB-MCUST-NMS"}


def resolve_curation(sku):
    if not sku:
        return None
    sku = sku.strip().upper()
    if sku in _MONTHLY_PATTERNS:
        return "MONTHLY"
    if "MCUST-NMS" in sku:
        return "NMS"
    if "MCUST-MS" in sku or "CUR-MS" in sku or "BVAL" in sku:
        return "MS"
    for cur in KNOWN_CURATIONS:
        if cur in sku:
            return cur
    return None


def is_large_box(sku):
    """Large boxes get CEX-EC extra cheese."""
    s = (sku or "").strip().upper()
    return s.startswith("AHB-L") or s.startswith("AHB-LCUST")


def load_settings():
    with open(SETTINGS_PATH, "r") as f:
        return json.load(f)


# -- Step 1: Load inventory --

def load_inventory_csv(path):
    inv = {}
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sku = (row.get("Product SKU") or "").strip()
            if not sku:
                continue
            total = row.get("Total", "0") or "0"
            try:
                inv[sku] = int(float(total))
            except (ValueError, TypeError):
                inv[sku] = 0
    return inv


# -- Step 2: Parse depletion XLSX --

def parse_depletion_xlsx(path, sku_translations):
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb.active
    headers = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]

    product_cols = []
    for i, h in enumerate(headers):
        if h and "AHB (S_REG):" in str(h):
            name = str(h).split(": ", 1)[1].strip() if ": " in str(h) else str(h)
            product_cols.append((i, name))

    tags_idx = None
    for i, h in enumerate(headers):
        if h and str(h).strip().lower() == "tags":
            tags_idx = i
            break

    totals = defaultdict(int)
    first_order_totals = defaultdict(int)
    order_count = 0
    first_order_count = 0

    for row in ws.iter_rows(min_row=2, values_only=True):
        order_count += 1
        is_first = False
        if tags_idx is not None:
            tags_val = row[tags_idx] if tags_idx < len(row) else None
            if tags_val and "Subscription First Order" in str(tags_val):
                is_first = True
                first_order_count += 1

        for idx, name in product_cols:
            val = row[idx] if idx < len(row) else None
            if val and isinstance(val, (int, float)) and val > 0:
                totals[name] += int(val)
                if is_first:
                    first_order_totals[name] += int(val)

    wb.close()

    sku_totals = defaultdict(int)
    first_sku_totals = defaultdict(int)
    unmatched = []

    for product, qty in totals.items():
        if "tasting guide" in product.lower():
            continue
        sku = sku_translations.get(product)
        if sku:
            sku_totals[sku] += qty
        else:
            unmatched.append((product, qty))

    for product, qty in first_order_totals.items():
        if "tasting guide" in product.lower():
            continue
        sku = sku_translations.get(product)
        if sku:
            first_sku_totals[sku] += qty

    return dict(sku_totals), order_count, first_order_count, dict(first_sku_totals), unmatched


# -- Step 3: Fetch Recharge via API (v2021-11) --

def fetch_recharge_api(api_token):
    """Fetch queued charges. Returns pickable SKU demand + curation counts per week."""
    import requests

    session = requests.Session()
    session.headers.update({
        "X-Recharge-Access-Token": api_token,
        "Accept": "application/json",
        "X-Recharge-Version": "2021-11",
    })

    all_charges = []
    params = {
        "status": "queued",
        "limit": 250,
        "sort_by": "id-asc",
        "scheduled_at_min": WK1_START.isoformat(),
        "scheduled_at_max": WK2_END.isoformat(),
    }
    page = 0

    while True:
        page += 1
        for attempt in range(3):
            try:
                resp = session.get("https://api.rechargeapps.com/charges",
                                   params=params, timeout=60)
                resp.raise_for_status()
                break
            except Exception:
                if attempt < 2:
                    time.sleep(2)
                else:
                    raise
        data = resp.json()
        charges = data.get("charges", [])
        if not charges:
            break
        all_charges.extend(charges)
        sys.stdout.write(f"\r  Fetched {len(all_charges)} charges (page {page})...")
        sys.stdout.flush()

        next_cursor = data.get("next_cursor")
        if not next_cursor:
            break
        # Cursor requests: ONLY cursor + limit
        params = {"cursor": next_cursor, "limit": 250}
        time.sleep(0.5)

    print(f"\r  Fetched {len(all_charges)} total queued charges.     ")

    # Per-week: pickable SKU demand + curation counts
    wk1_skus = defaultdict(int)
    wk2_skus = defaultdict(int)
    wk1_curations = defaultdict(int)  # {curation: charge_count}
    wk2_curations = defaultdict(int)
    wk1_large = defaultdict(int)      # {curation: large_box_count}
    wk2_large = defaultdict(int)
    wk1_med_total = 0                 # total AHB-MED/MCUST boxes (excludes CMED)
    wk2_med_total = 0
    wk1_cmed_total = 0                # total AHB-CMED boxes
    wk2_cmed_total = 0
    wk1_lge_total = 0                 # total AHB-LGE/LCUST boxes
    wk2_lge_total = 0
    charges_per_date = defaultdict(int)

    for charge in all_charges:
        sched = (charge.get("scheduled_at") or "")[:10]
        if not sched:
            continue
        try:
            d = date.fromisoformat(sched)
        except ValueError:
            continue

        charges_per_date[sched] += 1
        is_wk1 = WK1_START <= d <= WK1_END
        is_wk2 = WK2_START <= d <= WK2_END
        if not is_wk1 and not is_wk2:
            continue

        # Find box SKU and curation for this charge
        box_sku = None
        for item in charge.get("line_items", []):
            sku = (item.get("sku") or "").strip()
            if sku.upper().startswith("AHB-"):
                box_sku = sku
                break

        if box_sku:
            upper_box = box_sku.upper()
            cur = resolve_curation(box_sku)
            is_lg = is_large_box(box_sku)

            # Count MONTHLY-only MED/LGE boxes (plain AHB-MED, AHB-LGE, AHB-CMED, etc.)
            # Custom curations (AHB-MCUST-MDT, AHB-LCUST-OWC) are already in per-curation tables
            if cur == "MONTHLY":
                is_cmed = "CMED" in upper_box
                if is_lg:
                    if is_wk1:
                        wk1_lge_total += 1
                    else:
                        wk2_lge_total += 1
                elif is_cmed:
                    if is_wk1:
                        wk1_cmed_total += 1
                    else:
                        wk2_cmed_total += 1
                else:
                    if is_wk1:
                        wk1_med_total += 1
                    else:
                        wk2_med_total += 1

            # Curation counts (non-MONTHLY only)
            if cur and cur != "MONTHLY":
                if is_wk1:
                    wk1_curations[cur] += 1
                    if is_lg:
                        wk1_large[cur] += 1
                else:
                    wk2_curations[cur] += 1
                    if is_lg:
                        wk2_large[cur] += 1

        # Sum pickable SKU quantities
        # For MONTHLY boxes, skip pickable items — they flow through slot
        # assignment SUMIF tables in the cut order xlsx instead.
        cur = resolve_curation(box_sku) if box_sku else None
        is_monthly = (cur == "MONTHLY")

        for item in charge.get("line_items", []):
            sku = (item.get("sku") or "").strip()
            if not sku or not any(sku.startswith(p) for p in PICKABLE_PREFIXES):
                continue
            if is_monthly:
                continue  # items flow through slot tables instead
            qty = int(float(item.get("quantity", 1)))
            if is_wk1:
                wk1_skus[sku] += qty
            else:
                wk2_skus[sku] += qty

    return (dict(wk1_skus), dict(wk2_skus),
            dict(wk1_curations), dict(wk2_curations),
            dict(wk1_large), dict(wk2_large),
            len(all_charges), dict(charges_per_date),
            wk1_med_total, wk2_med_total,
            wk1_cmed_total, wk2_cmed_total,
            wk1_lge_total, wk2_lge_total)


# -- Step 4: Fetch Shopify orders for upcoming ship weeks --

# Ship week tag dates (Monday of each week)
WK1_SHIP_TAG = "_SHIP_2026-03-23"
WK2_SHIP_TAG = "_SHIP_2026-03-30"


def fetch_shopify_orders(settings):
    """Fetch open Shopify orders for WK1/WK2 ship tags.

    Returns per-week:
      addon_skus   — pickable SKU demand from non-subscription orders (no AHB-* box)
      curations    — {curation: count} from subscription orders (has AHB-* box)
      large_boxes  — {curation: count} where box is AHB-L*
      med_counts   — {curation: count} where box is AHB-M* (MED/MCUST)
      lge_counts   — {curation: count} where box is AHB-L* (LGE/LCUST)
    """
    import requests

    store = settings.get("shopify_store_url", "").strip()
    token = settings.get("shopify_access_token", "").strip()
    if not store or not token:
        print("  WARNING: Shopify credentials not configured, skipping")
        empty = {}
        return (empty, empty, empty, empty, empty, empty, empty, empty, empty, empty)

    if not store.startswith("http"):
        store = f"https://{store}.myshopify.com"

    session = requests.Session()
    session.headers.update({
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    })

    # Fetch recent open/unfulfilled orders
    import re
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=21)).isoformat()
    url = f"{store}/admin/api/2024-01/orders.json"
    params = {"status": "open", "fulfillment_status": "unfulfilled",
              "limit": 250, "created_at_min": cutoff}

    all_orders = []
    while url:
        print(f"  Shopify: fetching page {len(all_orders) // 250 + 1}...", flush=True)
        resp = session.get(url, params=params, timeout=60)
        if resp.status_code != 200:
            print(f"  ERROR: Shopify API {resp.status_code}: {resp.text[:200]}")
            empty = {}
            return (empty, empty, empty, empty, empty, empty, empty, empty, empty, empty)
        data = resp.json()
        all_orders.extend(data.get("orders", []))
        url = None
        params = None
        link = resp.headers.get("Link", "")
        if 'rel="next"' in link:
            m = re.search(r'<([^>]+)>;\s*rel="next"', link)
            if m:
                url = m.group(1)
        time.sleep(0.3)

    print(f"  Shopify: {len(all_orders)} open orders fetched")

    # Classify orders by ship week
    wk1_addon = defaultdict(int)
    wk2_addon = defaultdict(int)
    wk1_curations = defaultdict(int)
    wk2_curations = defaultdict(int)
    wk1_large = defaultdict(int)
    wk2_large = defaultdict(int)
    wk1_med = defaultdict(int)
    wk2_med = defaultdict(int)
    wk1_lge = defaultdict(int)
    wk2_lge = defaultdict(int)
    wk1_count = 0
    wk2_count = 0

    for order in all_orders:
        tags = order.get("tags", "") or ""

        is_wk1 = WK1_SHIP_TAG in tags
        is_wk2 = WK2_SHIP_TAG in tags
        if not is_wk1 and not is_wk2:
            continue

        if is_wk1:
            wk1_count += 1
        else:
            wk2_count += 1

        # Extract line items
        line_items = order.get("line_items", [])
        item_skus = {}
        box_sku = None
        for item in line_items:
            sku = (item.get("sku") or "").strip()
            if not sku:
                continue
            qty = int(float(item.get("quantity", 1)))
            item_skus[sku] = item_skus.get(sku, 0) + qty
            if sku.upper().startswith("AHB-"):
                box_sku = sku.upper()

        if box_sku:
            # Subscription order — count curation + box type, NO pickable SKU demand
            cur = resolve_curation(box_sku)
            is_lg = is_large_box(box_sku)

            if cur == "MONTHLY":
                # Plain AHB-MED/AHB-LGE/AHB-CMED — count as box totals
                is_cmed = "CMED" in box_sku
                if is_lg:
                    if is_wk1:
                        wk1_lge["MONTHLY"] = wk1_lge.get("MONTHLY", 0) + 1
                    else:
                        wk2_lge["MONTHLY"] = wk2_lge.get("MONTHLY", 0) + 1
                elif is_cmed:
                    if is_wk1:
                        wk1_med["CMED"] = wk1_med.get("CMED", 0) + 1
                    else:
                        wk2_med["CMED"] = wk2_med.get("CMED", 0) + 1
                else:
                    if is_wk1:
                        wk1_med["MONTHLY"] = wk1_med.get("MONTHLY", 0) + 1
                    else:
                        wk2_med["MONTHLY"] = wk2_med.get("MONTHLY", 0) + 1
            elif cur:
                # Custom curation — count in per-curation tables
                if is_wk1:
                    wk1_curations[cur] += 1
                    if is_lg:
                        wk1_large[cur] += 1
                        wk1_lge[cur] += 1
                    else:
                        wk1_med[cur] += 1
                else:
                    wk2_curations[cur] += 1
                    if is_lg:
                        wk2_large[cur] += 1
                        wk2_lge[cur] += 1
                    else:
                        wk2_med[cur] += 1
        else:
            # Non-subscription order — count pickable SKUs as addon demand
            target = wk1_addon if is_wk1 else wk2_addon
            for sku, qty in item_skus.items():
                upper = sku.upper()
                if any(upper.startswith(p) for p in PICKABLE_PREFIXES):
                    target[sku] += qty

    print(f"  Shopify WK1: {wk1_count} orders ({sum(wk1_curations.values())} subs, "
          f"{wk1_count - sum(wk1_curations.values())} addon-only)")
    print(f"  Shopify WK2: {wk2_count} orders ({sum(wk2_curations.values())} subs, "
          f"{wk2_count - sum(wk2_curations.values())} addon-only)")

    return (dict(wk1_addon), dict(wk2_addon),
            dict(wk1_curations), dict(wk2_curations),
            dict(wk1_large), dict(wk2_large),
            dict(wk1_med), dict(wk2_med),
            dict(wk1_lge), dict(wk2_lge))


# -- Main --

def main():
    settings = load_settings()
    sku_translations = settings.get("sku_translations", {})
    recharge_token = settings.get("recharge_api_token", "")
    pr_cjam = settings.get("pr_cjam", {})
    cex_ec = settings.get("cex_ec", {})

    # 1. Load inventory
    print("Loading inventory from 3/14 snapshot...")
    inventory = load_inventory_csv(INV_CSV)
    print(f"  {len(inventory)} SKUs loaded.")

    # 2. Parse depletions
    print("\nParsing Saturday 3/16 depletion...")
    sat_dep, sat_orders, sat_first, sat_first_skus, sat_unmatched = parse_depletion_xlsx(
        SAT_DEPLETION, sku_translations
    )
    print(f"  {sat_orders} orders, {sum(sat_dep.values())} items, {sat_first} first orders.")

    print("Parsing Tuesday 3/17 depletion...")
    tue_dep, tue_orders, tue_first, tue_first_skus, tue_unmatched = parse_depletion_xlsx(
        TUE_DEPLETION, sku_translations
    )
    print(f"  {tue_orders} orders, {sum(tue_dep.values())} items, {tue_first} first orders.")

    all_unmatched = set()
    for name, qty in sat_unmatched + tue_unmatched:
        all_unmatched.add(name)
    if all_unmatched:
        print(f"\n  WARNING: {len(all_unmatched)} unmatched product names:")
        for name in sorted(all_unmatched):
            print(f"    - {name}")

    # 3. Available inventory
    available = {}
    all_skus = set(inventory.keys()) | set(sat_dep.keys()) | set(tue_dep.keys())
    for sku in all_skus:
        available[sku] = inventory.get(sku, 0) - sat_dep.get(sku, 0) - tue_dep.get(sku, 0)

    # 4. Recharge demand
    if not recharge_token:
        print("\nNo Recharge API token!")
        return

    print("\nFetching Recharge demand from API (v2021-11)...")
    (rc_wk1, rc_wk2,
     wk1_curations, wk2_curations,
     wk1_large, wk2_large,
     total_charges, charges_per_date,
     wk1_med_monthly, wk2_med_monthly,
     _wk1_cmed, _wk2_cmed,
     wk1_lge_monthly, wk2_lge_monthly) = fetch_recharge_api(recharge_token)

    print(f"\n  Charges per date:")
    wk1_ct = wk2_ct = 0
    for d in sorted(charges_per_date.keys()):
        dd = date.fromisoformat(d)
        if WK1_START <= dd <= WK1_END:
            wk1_ct += charges_per_date[d]
            marker = " <-- WK1"
        elif WK2_START <= dd <= WK2_END:
            wk2_ct += charges_per_date[d]
            marker = " <-- WK2"
        else:
            marker = ""
        print(f"    {d}: {charges_per_date[d]:>5} charges{marker}")
    print(f"  WK1 charges: {wk1_ct:,}  |  WK2 charges: {wk2_ct:,}")

    # 5. First order projections
    wk1_first = dict(sat_first_skus)   # project Sat 3/16 first order profile
    wk2_first = dict(tue_first_skus)   # project Tue 3/17 first order profile

    # 6. Combine demands
    wk1_total = defaultdict(int)
    for sku, qty in rc_wk1.items():
        wk1_total[sku] += qty
    for sku, qty in wk1_first.items():
        wk1_total[sku] += qty

    wk2_total = defaultdict(int)
    for sku, qty in rc_wk2.items():
        wk2_total[sku] += qty
    for sku, qty in wk2_first.items():
        wk2_total[sku] += qty

    wk1_total = dict(wk1_total)
    wk2_total = dict(wk2_total)

    # -- Report --
    report_skus = set()
    for d in (available, wk1_total, wk2_total):
        report_skus.update(d.keys())
    report_skus = sorted(
        sku for sku in report_skus
        if any(sku.startswith(p) for p in PICKABLE_PREFIXES)
    )

    inv_settings = settings.get("inventory", {})

    def sku_name(sku):
        data = inv_settings.get(sku, {})
        return data.get("name", "") if isinstance(data, dict) else ""

    print("\n" + "=" * 100)
    print(f"  {'SKU':<14} {'Name':<35} {'Avail':>7} {'Wk1':>7} {'AftWk1':>7} {'Wk2':>7}")
    print(f"  {'':14} {'':35} {'(now)':>7} {'(3/23)':>7} {'':>7} {'(3/30)':>7}")
    print("  " + "-" * 96)

    categories = {"CH-": [], "MT-": [], "AC-": []}
    for sku in report_skus:
        for prefix in PICKABLE_PREFIXES:
            if sku.startswith(prefix):
                categories[prefix].append(sku)
                break

    cat_labels = {"CH-": "CHEESE", "MT-": "MEAT", "AC-": "ACCOMPANIMENTS"}

    for prefix in PICKABLE_PREFIXES:
        skus = categories[prefix]
        if not skus:
            continue
        active = [s for s in skus
                  if available.get(s, 0) != 0
                  or wk1_total.get(s, 0) > 0
                  or wk2_total.get(s, 0) > 0]
        if not active:
            continue

        print(f"\n  -- {cat_labels[prefix]} " + "-" * 85)
        for sku in active:
            avail = available.get(sku, 0)
            w1 = wk1_total.get(sku, 0)
            after_w1 = avail - w1
            w2 = wk2_total.get(sku, 0)

            flag = ""
            if after_w1 - w2 < 0:
                flag = " *** SHORT"
            elif avail < 0:
                flag = " (deficit)"

            name = sku_name(sku)[:35]
            print(f"  {sku:<14} {name:<35} {avail:>7,} {w1:>7,} {after_w1:>7,} {w2:>7,}{flag}")

    # -- PR-CJAM Totals --
    print(f"\n  {'=' * 96}")
    print(f"  PR-CJAM TOTALS (1 per box, all curations)")
    print(f"  {'-' * 96}")
    print(f"  {'Curation':<12} {'Cheese':<14} {'Name':<35} {'Wk1':>7} {'Wk2':>7}")
    print(f"  {'-' * 96}")

    cjam_wk1_totals = defaultdict(int)  # {cheese_sku: total}
    cjam_wk2_totals = defaultdict(int)

    for cur in sorted(set(list(wk1_curations.keys()) + list(wk2_curations.keys()))):
        cjam = pr_cjam.get(cur, {})
        cheese = cjam.get("cheese", "")
        if not cheese:
            continue
        w1 = wk1_curations.get(cur, 0)
        w2 = wk2_curations.get(cur, 0)
        cjam_wk1_totals[cheese] += w1
        cjam_wk2_totals[cheese] += w2
        name = sku_name(cheese)[:35]
        print(f"  {cur:<12} {cheese:<14} {name:<35} {w1:>7,} {w2:>7,}")

    print(f"  {'-' * 96}")
    print(f"  {'TOTALS BY CHEESE':}")
    for cheese in sorted(set(list(cjam_wk1_totals.keys()) + list(cjam_wk2_totals.keys()))):
        w1 = cjam_wk1_totals[cheese]
        w2 = cjam_wk2_totals[cheese]
        name = sku_name(cheese)[:35]
        print(f"  {'':12} {cheese:<14} {name:<35} {w1:>7,} {w2:>7,}")

    # -- CEX-EC Totals --
    print(f"\n  {'=' * 96}")
    print(f"  CEX-EC TOTALS (1 per large box only)")
    print(f"  {'-' * 96}")
    print(f"  {'Curation':<12} {'Cheese':<14} {'Name':<35} {'Wk1':>7} {'Wk2':>7}")
    print(f"  {'-' * 96}")

    cexec_wk1_totals = defaultdict(int)
    cexec_wk2_totals = defaultdict(int)

    for cur in sorted(set(list(wk1_large.keys()) + list(wk2_large.keys()))):
        cheese = cex_ec.get(cur, "")
        if not cheese:
            continue
        w1 = wk1_large.get(cur, 0)
        w2 = wk2_large.get(cur, 0)
        cexec_wk1_totals[cheese] += w1
        cexec_wk2_totals[cheese] += w2
        name = sku_name(cheese)[:35]
        print(f"  {cur:<12} {cheese:<14} {name:<35} {w1:>7,} {w2:>7,}")

    print(f"  {'-' * 96}")
    print(f"  {'TOTALS BY CHEESE':}")
    for cheese in sorted(set(list(cexec_wk1_totals.keys()) + list(cexec_wk2_totals.keys()))):
        w1 = cexec_wk1_totals[cheese]
        w2 = cexec_wk2_totals[cheese]
        name = sku_name(cheese)[:35]
        print(f"  {'':12} {cheese:<14} {name:<35} {w1:>7,} {w2:>7,}")

    # -- Summary --
    print("\n" + "=" * 100)
    print(f"Inventory: 3/14 RMFG snapshot")
    print(f"Depletions: Sat 3/16 ({sat_orders:,} orders) + Tue 3/17 ({tue_orders:,} orders)")
    print(f"Wk1: RC={sum(rc_wk1.values()):,} + FO={sum(wk1_first.values()):,}"
          f" = {sum(wk1_total.values()):,} items")
    print(f"Wk2: RC={sum(rc_wk2.values()):,} + FO={sum(wk2_first.values()):,}"
          f" = {sum(wk2_total.values()):,} items")

    shortages = []
    for sku in report_skus:
        avail = available.get(sku, 0)
        net = avail - wk1_total.get(sku, 0) - wk2_total.get(sku, 0)
        if net < 0:
            shortages.append((sku, net))
    if shortages:
        print(f"\n*** {len(shortages)} SKUs SHORT after 2 weeks:")
        for sku, deficit in sorted(shortages, key=lambda x: x[1]):
            print(f"    {sku:<14} {sku_name(sku)[:30]:<30} {deficit:>+8,}")

    # -- CSV --
    csv_path = os.path.join(BASE, "inventory_demand_report.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["SKU", "Name", "Available", "Wk1_Total", "After_Wk1", "Wk2_Total"])
        for sku in report_skus:
            avail = available.get(sku, 0)
            w1 = wk1_total.get(sku, 0)
            after = avail - w1
            w2 = wk2_total.get(sku, 0)
            writer.writerow([sku, sku_name(sku), avail, w1, after, w2])

        # PR-CJAM section
        writer.writerow([])
        writer.writerow(["PR-CJAM", "Curation", "Cheese", "Wk1", "Wk2"])
        for cur in sorted(set(list(wk1_curations.keys()) + list(wk2_curations.keys()))):
            cheese = pr_cjam.get(cur, {}).get("cheese", "")
            if cheese:
                writer.writerow(["", cur, cheese, wk1_curations.get(cur, 0),
                                 wk2_curations.get(cur, 0)])
        writer.writerow(["PR-CJAM TOTALS"])
        for cheese in sorted(set(list(cjam_wk1_totals.keys()) + list(cjam_wk2_totals.keys()))):
            writer.writerow(["", "", cheese, cjam_wk1_totals[cheese], cjam_wk2_totals[cheese]])

        # CEX-EC section
        writer.writerow([])
        writer.writerow(["CEX-EC", "Curation", "Cheese", "Wk1", "Wk2"])
        for cur in sorted(set(list(wk1_large.keys()) + list(wk2_large.keys()))):
            cheese = cex_ec.get(cur, "")
            if cheese:
                writer.writerow(["", cur, cheese, wk1_large.get(cur, 0),
                                 wk2_large.get(cur, 0)])
        writer.writerow(["CEX-EC TOTALS"])
        for cheese in sorted(set(list(cexec_wk1_totals.keys()) + list(cexec_wk2_totals.keys()))):
            writer.writerow(["", "", cheese, cexec_wk1_totals[cheese], cexec_wk2_totals[cheese]])

    print(f"\nCSV written to: {csv_path}")


if __name__ == "__main__":
    main()
