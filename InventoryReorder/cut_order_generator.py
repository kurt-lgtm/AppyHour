"""
Cut Order Generator — Week of 2026-03-16
Reads Recharge queued charges CSV + Shopify order-dashboard CSV,
combines demand, subtracts inventory (sliced + wheel bulk),
applies PR-CJAM and CEX-EC assignments, produces cut order CSV.

Usage:
  python cut_order_generator.py          # Use local CSV files from RMFG_20260310/
  python cut_order_generator.py --live   # Pull fresh data from APIs
"""

import argparse
import csv
import datetime
import json
import math
import os
import re
import sys
from collections import defaultdict

# ── Paths ──────────────────────────────────────────────────────────────
BASE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(BASE, "dist", "inventory_reorder_settings.json")
TEMPLATE_CSV = os.path.join(BASE, "Orders RMFG_20260310 - Template Check.csv")
RMFG_DIR = os.path.join(BASE, "RMFG_20260310")
OUTPUT_DIR = os.path.join(BASE, "production_orders")
OUTPUT_CSV = os.path.join(OUTPUT_DIR, "cut_order_20260311.csv")

WHEEL_TO_SLICE = 2.67

# ── SKU helpers (from app.py) ──────────────────────────────────────────
EQUIV = {"CH-BRIE": "CH-EBRIE"}
SKIP_PREFIXES = ("AHB-", "BL-", "PK-", "TR-", "EX-")


def normalize_sku(sku):
    return EQUIV.get(sku.upper(), sku.upper()) if sku else sku


def is_pickable(sku):
    upper = sku.upper()
    if any(upper.startswith(p) for p in SKIP_PREFIXES):
        return False
    if upper.startswith("PR-CJAM"):
        return False
    if upper.startswith("CEX-"):
        return False
    return bool(sku.strip())


KNOWN_CURATIONS = {
    "MONG", "MDT", "OWC", "SPN", "ALPN", "ALPT",
    "ISUN", "HHIGH", "NMS", "BYO", "SS", "GEN", "MS",
}
_MONTHLY_PATTERNS = {"AHB-MED", "AHB-LGE", "AHB-CMED", "AHB-CUR-MS",
                      "AHB-BVAL", "AHB-MCUST-MS", "AHB-MCUST-NMS"}


def resolve_curation_from_box_sku(sku):
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


# ── PR-CJAM overrides for this week ───────────────────────────────────
# All curations → CH-MCPC, except ISUN → CH-BAP, MONG → CH-BLR (3000 incoming)
PR_CJAM_OVERRIDES = {
    "ISUN": "CH-BAP",
    "MDT": "CH-TTBRIE",
    "SPN": "CH-MCPC",
    "MONG": "CH-BLR",
    "OWC": "CH-MCPC",
    "ALPN": "CH-MCPC",
    "ALPT": "CH-MCPC",
    "HHIGH": "CH-TIP",
    "BYO": "CH-TIP",
    "GEN": "CH-MCPC",
    "NMS": "CH-MCPC",
    "SS": "CH-TIP",
}

# ── CEX-EC overrides for this week ────────────────────────────────────
# Override settings JSON — these are the final agreed assignments
CEX_EC_OVERRIDES = {
    "MONG": "CH-WWDI",
    "MDT": None,       # handled by splits override below
    "OWC": "CH-WMANG",
    "SPN": "CH-MSMG",
    "ALPN": "CH-UCONE",
    "ISUN": "CH-CTGOD",
    "HHIGH": "CH-HCGU",
    "BYO": "CH-HCGU",
    "SS": "CH-MSMG",
    "NMS": "CH-MCPC",
    "MS": "CH-6COM",
}

# MDT CEX-EC split: 64% MCPC, 36% MSMG
CEXEC_SPLITS_OVERRIDES = {
    "MDT": {"CH-MCPC": 0.64, "CH-MSMG": 0.36},
}

# ── Wheel inventory (from Dropbox 3/1 + 3/10 snapshot) ────────────────
WHEEL_INVENTORY = {
    "CH-MCPC":   {"name": "McCalls Irish Porter",        "wheels": 92,   "wt": 10,   "slices": 2456},
    "CH-6COM":   {"name": "Comte 6 Months",              "wheels": 6,    "wt": 11,   "slices": 176},
    "CH-LEON":   {"name": "Leonora",                     "wheels": 250,  "wt": 5,    "slices": 3338},
    "CH-WWDI":   {"name": "Wooly Wooly Diablo",          "wheels": 77,   "wt": 7,    "slices": 1439},
    "CH-LOSC":   {"name": "Los Cameros de Romero",       "wheels": 19,   "wt": 15.4, "slices": 782},
    "CH-MAU3":   {"name": "Manchego Aurora",             "wheels": 2,    "wt": 14,   "slices": 74},
    "CH-RQCAV":  {"name": "Drunken Goat Reserva",        "wheels": 8,    "wt": 9.5,  "slices": 202},
    "CH-HCGU":   {"name": "Honey Clover Gouda",          "wheels": 20,   "wt": 20,   "slices": 1068},
    "CH-MSMG":   {"name": "Farmstead Smoked Gouda",      "wheels": 26,   "wt": 18,   "slices": 1249},
    "CH-BAP":    {"name": "Barricato Al Pepe",           "wheels": 10,   "wt": 14.1, "slices": 377},
    "CH-SOT":    {"name": "Sottocenere w Truffles",      "wheels": 30,   "wt": 11,   "slices": 881},
    "CH-BRZ":    {"name": "Prairie Breeze",              "wheels": 6,    "wt": 40,   "slices": 640},
    "CH-SHADOW": {"name": "Shadow Blossom",              "wheels": 8,    "wt": 13.5, "slices": 288},
    "CH-PVEC":   {"name": "Piave Vecchio",               "wheels": 29,   "wt": 12.1, "slices": 939},
    "CH-RACL":   {"name": "Raclette Livradios",          "wheels": 20,   "wt": 15,   "slices": 801},
    "CH-ALP":    {"name": "Alp Blossom",                 "wheels": 6,    "wt": 14,   "slices": 224},
    "CH-BARI":   {"name": "Barista",                     "wheels": 22,   "wt": 5,    "slices": 294},
    "CH-FONTAL": {"name": "Fontal",                      "wheels": 3,    "wt": 10.5, "slices": 84},
    "CH-KM39":   {"name": "KM39",                        "wheels": 5,    "wt": 24,   "slices": 320},
    "CH-UCONE":  {"name": "Ubriacone",                   "wheels": 7,    "wt": 11.5, "slices": 214},
    "CH-UROSE":  {"name": "Ubriaco al Pinot Rose",       "wheels": 2,    "wt": 13.2, "slices": 71},
    "CH-CTUF":   {"name": "Caciotta Al Tartufo",         "wheels": 9,    "wt": 15,   "slices": 360},
    "CH-FEMM":   {"name": "Entremont Emmentaler",        "wheels": 4.5,  "wt": 14,   "slices": 168},
}


def load_settings():
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_sliced_inventory():
    """Load 'Available 3/08' column from Template Check CSV as sliced inventory."""
    inv = {}
    with open(TEMPLATE_CSV, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sku = (row.get("SKU") or "").strip().upper()
            if not sku or not sku.startswith("CH-"):
                continue
            avail_str = (row.get("Available 3/08") or "").strip()
            try:
                avail = int(float(avail_str))
            except (ValueError, TypeError):
                avail = 0
            inv[sku] = max(avail, 0)  # floor at 0
    return inv


# ── CSV-based demand (from already-downloaded RMFG folder) ─────────────

def load_recharge_csv(settings):
    """Load queued charges from local CSV, resolve PR-CJAM/CEX-EC to cheese demand."""
    # Find charges CSV in RMFG folder
    charges_file = None
    for f in os.listdir(RMFG_DIR):
        if f.startswith("charges_queued") and f.endswith(".csv"):
            charges_file = os.path.join(RMFG_DIR, f)
            break
    if not charges_file:
        print("  ERROR: No charges_queued CSV found in RMFG_20260310/")
        return {}

    print(f"  Reading: {os.path.basename(charges_file)}")

    # CSV has flat rows: charge_id, customer_id, scheduled_at, email,
    # shipping_province, line_item_title, line_item_quantity, line_item_sku
    # Group by charge_id to get box context
    charges = defaultdict(list)
    with open(charges_file, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cid = row.get("charge_id", "")
            charges[cid].append(row)

    cex_ec = CEX_EC_OVERRIDES
    splits = CEXEC_SPLITS_OVERRIDES
    demand = defaultdict(float)

    for cid, items in charges.items():
        # Find box SKU for curation context
        box_sku = None
        for item in items:
            sku = (item.get("line_item_sku") or "").strip().upper()
            if sku.startswith("AHB-"):
                box_sku = sku
                break
        curation = resolve_curation_from_box_sku(box_sku)

        for item in items:
            sku = (item.get("line_item_sku") or "").strip()
            if not sku:
                continue
            upper = sku.upper()
            try:
                qty = int(float(item.get("line_item_quantity", 1)))
            except (ValueError, TypeError):
                qty = 1

            # PR-CJAM resolution with overrides
            if upper.startswith("PR-CJAM-"):
                suffix = upper.split("PR-CJAM-", 1)[1]
                if suffix == "GEN":
                    if curation and curation not in ("MONTHLY", None):
                        suffix = curation
                    # else keep suffix as "GEN" — resolves via overrides/settings
                ch = PR_CJAM_OVERRIDES.get(suffix)
                if not ch:
                    info = settings.get("pr_cjam", {}).get(suffix, {})
                    ch = info.get("cheese", "") if isinstance(info, dict) else str(info)
                if ch:
                    demand[normalize_sku(ch)] += qty
                continue

            # CEX-EC resolution
            if upper.startswith("CEX-EC-"):
                suffix = upper.split("CEX-EC-", 1)[1]
                if suffix in splits:
                    for ssku, pct in splits[suffix].items():
                        demand[normalize_sku(ssku)] += qty * pct
                else:
                    ec = cex_ec.get(suffix, "")
                    if ec:
                        demand[normalize_sku(ec)] += qty
                continue

            if upper == "CEX-EC":
                if curation and curation not in ("MONTHLY", None):
                    if curation in splits:
                        for ssku, pct in splits[curation].items():
                            demand[normalize_sku(ssku)] += qty * pct
                    else:
                        ec = cex_ec.get(curation, "")
                        if isinstance(ec, str) and ec:
                            demand[normalize_sku(ec)] += qty
                continue

            # Resolve global extras (bare EX-EC, CEX-EM, EX-EM, etc.)
            ge = settings.get("global_extras", {}).get(upper)
            if ge:
                demand[normalize_sku(ge)] += qty
                continue

            if not is_pickable(sku):
                continue

            demand[normalize_sku(sku)] += qty

    print(f"  {len(charges)} unique charges processed")
    return {k: int(round(v)) for k, v in demand.items() if round(v) > 0}


def load_shopify_csv(settings):
    """Load Shopify orders from local order-dashboard CSV, resolve to cheese demand."""
    # Find order dashboard CSV in RMFG folder
    orders_file = None
    for f in os.listdir(RMFG_DIR):
        if f.startswith("order-dashboard") and f.endswith(".csv"):
            orders_file = os.path.join(RMFG_DIR, f)
            break
    if not orders_file:
        print("  ERROR: No order-dashboard CSV found in RMFG_20260310/")
        return {}

    print(f"  Reading: {os.path.basename(orders_file)}")

    # CSV format: "Order Number","Order ID","Order Total","Customer ID","Name",
    # "Email","Phone","Billing Address","Shipping Address","Order Date",
    # "Item Count","Order Tags","All SKUs"
    # All SKUs is comma-separated list of SKUs per order
    demand = defaultdict(float)
    first_order_demand = defaultdict(float)  # tracked separately for ×3 projection
    order_count = 0
    first_order_count = 0

    def _resolve_order_skus(sku_counts, curation, target):
        """Resolve PR-CJAM/CEX-EC SKUs into cheese demand, accumulate into target dict."""
        cex_ec_map = CEX_EC_OVERRIDES
        splits = CEXEC_SPLITS_OVERRIDES
        for sku, qty in sku_counts.items():
            upper = sku.upper()

            # PR-CJAM resolution
            if upper.startswith("PR-CJAM-"):
                suffix = upper.split("PR-CJAM-", 1)[1]
                if suffix == "GEN":
                    if curation and curation not in ("MONTHLY", None):
                        suffix = curation
                ch = PR_CJAM_OVERRIDES.get(suffix)
                if not ch:
                    info = settings.get("pr_cjam", {}).get(suffix, {})
                    ch = info.get("cheese", "") if isinstance(info, dict) else str(info)
                if ch:
                    target[normalize_sku(ch)] += qty
                continue

            # CEX-EC resolution
            if upper.startswith("CEX-EC-"):
                suffix = upper.split("CEX-EC-", 1)[1]
                if suffix in splits:
                    for ssku, pct in splits[suffix].items():
                        target[normalize_sku(ssku)] += qty * pct
                else:
                    ec = cex_ec_map.get(suffix, "")
                    if ec:
                        target[normalize_sku(ec)] += qty
                continue

            if upper == "CEX-EC":
                if curation and curation not in ("MONTHLY", None):
                    if curation in splits:
                        for ssku, pct in splits[curation].items():
                            target[normalize_sku(ssku)] += qty * pct
                    else:
                        ec = cex_ec_map.get(curation, "")
                        if isinstance(ec, str) and ec:
                            target[normalize_sku(ec)] += qty
                continue

            # Resolve global extras (bare EX-EC, CEX-EM, EX-EM, etc.)
            ge = settings.get("global_extras", {}).get(upper)
            if ge:
                target[normalize_sku(ge)] += qty
                continue

            if not is_pickable(sku):
                continue

            target[normalize_sku(sku)] += qty

    with open(orders_file, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tags = row.get("Order Tags", "")
            if "_SHIP_2026-03-16" not in tags:
                continue

            order_count += 1
            is_first = "Subscription First Order" in tags
            if is_first:
                first_order_count += 1

            all_skus = row.get("All SKUs", "")
            sku_list = [s.strip() for s in all_skus.split(",") if s.strip()]

            box_sku = None
            for sku in sku_list:
                if sku.upper().startswith("AHB-"):
                    box_sku = sku.upper()
                    break
            curation = resolve_curation_from_box_sku(box_sku)

            sku_counts = defaultdict(int)
            for sku in sku_list:
                sku_counts[sku.strip()] += 1

            # All orders go into main demand
            _resolve_order_skus(sku_counts, curation, demand)

            # First orders: only MONG gets ×3 projection (other curations close Sat 2am)
            if is_first and curation == "MONG":
                _resolve_order_skus(sku_counts, curation, first_order_demand)

    # ×3 projection: first orders already counted once in demand,
    # add ×2 more to reach ×3 total.
    # Only project standard MONG items (recipe + PR-CJAM + CEX-EC), not custom swaps.
    FIRST_ORDER_MULTIPLIER = 2  # additional multiplier (×3 total = 1 existing + 2 projected)
    MONG_PROJECT_SKUS = {
        # Recipe cheeses
        "CH-MAFT", "CH-BRZ",
        # PR-CJAM-MONG → BLR
        "CH-BLR",
        # CEX-EC-MONG → WWDI
        "CH-WWDI",
    }
    projected_extra = {}
    for sku, qty in first_order_demand.items():
        if sku not in MONG_PROJECT_SKUS:
            continue
        extra = qty * FIRST_ORDER_MULTIPLIER
        demand[sku] += extra
        projected_extra[sku] = int(round(extra))

    print(f"  {order_count} orders with _SHIP_2026-03-16 tag ({first_order_count} first orders)")
    if projected_extra:
        total_extra = sum(projected_extra.values())
        print(f"  First order ×3 projection adds {total_extra} units across {len(projected_extra)} SKUs")
    return {k: int(round(v)) for k, v in demand.items() if round(v) > 0}


# ── API-based demand (live pull) ───────────────────────────────────────

def pull_recharge_api(settings):
    """Pull queued charges from Recharge API, resolve to cheese demand."""
    import requests
    api_token = settings.get("recharge_api_token", "")
    if not api_token:
        print("  ERROR: No Recharge API token in settings")
        return {}

    session = requests.Session()
    session.headers.update({
        "X-Recharge-Access-Token": api_token,
        "Accept": "application/json",
    })

    url = "https://api.rechargeapps.com/charges"
    params = {"status": "queued", "limit": 250,
              "scheduled_at_min": "2026-03-11", "scheduled_at_max": "2026-03-14"}

    all_charges = []
    page = 1
    while True:
        print(f"  Recharge API page {page}...", flush=True)
        resp = session.get(url, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        charges = data.get("charges", [])
        if not charges:
            break
        all_charges.extend(charges)

        next_cursor = data.get("next_cursor")
        if next_cursor:
            params = {"cursor": next_cursor, "limit": 250}
            page += 1
            continue

        link = resp.headers.get("Link", "")
        if 'rel="next"' in link:
            m = re.search(r'<([^>]+)>;\s*rel="next"', link)
            if m:
                url = m.group(1)
                params = {}
                page += 1
                continue

        if len(charges) == 250:
            page += 1
            params = {"status": "queued", "limit": 250, "page": page}
            continue
        break

    print(f"  Recharge: {len(all_charges)} queued charges fetched")

    cex_ec = CEX_EC_OVERRIDES
    splits = CEXEC_SPLITS_OVERRIDES
    demand = defaultdict(float)

    for charge in all_charges:
        items = charge.get("line_items", [])
        box_sku = None
        for item in items:
            sku = (item.get("sku") or "").strip().upper()
            if sku.startswith("AHB-"):
                box_sku = sku
                break
        curation = resolve_curation_from_box_sku(box_sku)

        for item in items:
            sku = (item.get("sku") or "").strip()
            if not sku:
                continue
            upper = sku.upper()
            qty = int(float(item.get("quantity", 1)))

            if upper.startswith("PR-CJAM-"):
                suffix = upper.split("PR-CJAM-", 1)[1]
                if suffix == "GEN":
                    if curation and curation not in ("MONTHLY", None):
                        suffix = curation
                    # else keep suffix as "GEN" — resolves via overrides/settings
                ch = PR_CJAM_OVERRIDES.get(suffix)
                if not ch:
                    info = settings.get("pr_cjam", {}).get(suffix, {})
                    ch = info.get("cheese", "") if isinstance(info, dict) else str(info)
                if ch:
                    demand[normalize_sku(ch)] += qty
                continue

            if upper.startswith("CEX-EC-"):
                suffix = upper.split("CEX-EC-", 1)[1]
                if suffix in splits:
                    for ssku, pct in splits[suffix].items():
                        demand[normalize_sku(ssku)] += qty * pct
                else:
                    ec = cex_ec.get(suffix, "")
                    if ec:
                        demand[normalize_sku(ec)] += qty
                continue

            if upper == "CEX-EC":
                if curation and curation not in ("MONTHLY", None):
                    if curation in splits:
                        for ssku, pct in splits[curation].items():
                            demand[normalize_sku(ssku)] += qty * pct
                    else:
                        ec = cex_ec.get(curation, "")
                        if isinstance(ec, str) and ec:
                            demand[normalize_sku(ec)] += qty
                continue

            # Resolve global extras (bare EX-EC, CEX-EM, EX-EM, etc.)
            ge = settings.get("global_extras", {}).get(upper)
            if ge:
                demand[normalize_sku(ge)] += qty
                continue

            if not is_pickable(sku):
                continue

            demand[normalize_sku(sku)] += qty

    return {k: int(round(v)) for k, v in demand.items() if round(v) > 0}


def pull_shopify_api(settings):
    """Pull unfulfilled Shopify orders tagged _SHIP_2026-03-16."""
    import requests
    store = settings.get("shopify_store_url", "").strip()
    token = settings.get("shopify_access_token", "").strip()
    if not store or not token:
        print("  ERROR: Shopify store URL or access token not configured")
        return {}
    if not store.startswith("http"):
        store = f"https://{store}.myshopify.com"

    session = requests.Session()
    session.headers.update({
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    })

    cutoff = (datetime.datetime.now() - datetime.timedelta(days=14)).isoformat()
    url = f"{store}/admin/api/2024-01/orders.json"
    params = {"status": "open", "fulfillment_status": "unfulfilled",
              "limit": 250, "created_at_min": cutoff}

    all_orders = []
    while url:
        print(f"  Shopify API fetching...", flush=True)
        resp = session.get(url, params=params, timeout=60)
        if resp.status_code != 200:
            print(f"  ERROR: Shopify API {resp.status_code}: {resp.text[:200]}")
            return {}
        data = resp.json()
        all_orders.extend(data.get("orders", []))
        url = None
        params = None
        link = resp.headers.get("Link", "")
        if 'rel="next"' in link:
            m = re.search(r'<([^>]+)>;\s*rel="next"', link)
            if m:
                url = m.group(1)

    target_date = datetime.date(2026, 3, 16)
    demand = defaultdict(float)
    matched = 0
    for order in all_orders:
        tags = order.get("tags", "")
        m = re.search(r'_SHIP_(\d{4}-\d{2}-\d{2})', tags or "")
        if not m:
            continue
        try:
            ship_monday = datetime.date.fromisoformat(m.group(1))
        except (ValueError, TypeError):
            continue
        if ship_monday != target_date:
            continue
        matched += 1
        for item in order.get("line_items", []):
            sku = (item.get("sku") or "").strip()
            if not sku:
                continue
            qty = int(float(item.get("quantity", 1)))
            nsku = normalize_sku(sku)
            if is_pickable(nsku):
                demand[nsku] += qty

    print(f"  Shopify: {len(all_orders)} total, {matched} tagged _SHIP_2026-03-16")
    return {k: int(round(v)) for k, v in demand.items() if round(v) > 0}


# ── Main Logic ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Cut Order Generator — Week of 2026-03-16")
    parser.add_argument("--live", action="store_true",
                        help="Pull fresh data from Recharge/Shopify APIs instead of local CSVs")
    args = parser.parse_args()

    print("=" * 70)
    print("  CUT ORDER GENERATOR — Week of 2026-03-16")
    print(f"  Mode: {'LIVE API' if args.live else 'LOCAL CSV'}")
    print("=" * 70)
    sys.stdout.flush()

    settings = load_settings()
    inventory_names = {}
    for sku, info in settings.get("inventory", {}).items():
        if isinstance(info, dict):
            inventory_names[sku.upper()] = info.get("name", sku)

    # ── Step 1: Pull demand ────────────────────────────────────────────
    print("\n[1] Loading Recharge queued charges...", flush=True)
    if args.live:
        rc_demand = pull_recharge_api(settings)
    else:
        rc_demand = load_recharge_csv(settings)
    rc_cheese = {k: v for k, v in rc_demand.items() if k.startswith("CH-")}
    print(f"    -> {sum(rc_demand.values())} total units, {sum(rc_cheese.values())} cheese units across {len(rc_cheese)} SKUs")

    print("\n[2] Loading Shopify _SHIP_2026-03-16 orders...", flush=True)
    if args.live:
        sh_demand = pull_shopify_api(settings)
    else:
        sh_demand = load_shopify_csv(settings)
    sh_cheese = {k: v for k, v in sh_demand.items() if k.startswith("CH-")}
    print(f"    -> {sum(sh_demand.values())} total units, {sum(sh_cheese.values())} cheese units across {len(sh_cheese)} SKUs")

    # Combine demand
    total_demand = defaultdict(int)
    for sku, qty in rc_demand.items():
        total_demand[sku] += qty
    for sku, qty in sh_demand.items():
        total_demand[sku] += qty

    cheese_demand = {k: v for k, v in total_demand.items() if k.startswith("CH-")}
    print(f"\n    Combined cheese demand: {sum(cheese_demand.values())} units across {len(cheese_demand)} SKUs")

    # ── Step 2: Load sliced inventory ──────────────────────────────────
    print("\n[3] Loading sliced inventory from Template Check CSV...")
    sliced = load_sliced_inventory()
    print(f"    -> {len([s for s in sliced if sliced[s] > 0])} cheese SKUs with positive inventory")

    # ── Step 3: Build cut order ────────────────────────────────────────
    print("\n[4] Calculating net position & cut order...")

    # Collect all cheese SKUs
    all_skus = sorted(set(
        list(cheese_demand.keys()) +
        [s for s in sliced if sliced[s] > 0] +
        list(WHEEL_INVENTORY.keys())
    ))

    rows = []
    shortages = []
    cut_needed = []

    for sku in all_skus:
        name = inventory_names.get(sku, WHEEL_INVENTORY.get(sku, {}).get("name", sku))
        sl = sliced.get(sku, 0)
        wheel = WHEEL_INVENTORY.get(sku, {})
        wp = wheel.get("slices", 0)
        dm = cheese_demand.get(sku, 0)
        net = sl + wp - dm
        notes = []

        wheels_to_cut = 0
        pcs_from_cut = 0
        if dm > sl and wheel:
            gap = dm - sl
            slices_per_wheel = wheel["wt"] * WHEEL_TO_SLICE
            wheels_to_cut = math.ceil(gap / slices_per_wheel)
            wheels_to_cut = min(wheels_to_cut, wheel["wheels"])
            pcs_from_cut = int(wheels_to_cut * slices_per_wheel)
            notes.append(f"Cut {wheels_to_cut} wheel(s) = ~{pcs_from_cut} pcs")
            cut_needed.append((sku, name, wheels_to_cut, pcs_from_cut))

        if net < 0:
            notes.append("SHORTAGE")
            shortages.append((sku, name, abs(net)))
        elif dm == 0 and sl == 0 and wp > 0:
            notes.append("Wheel only, no demand")
        elif dm == 0:
            notes.append("No demand this week")

        # PR-CJAM source
        prcjam_curations = [c for c, ch in PR_CJAM_OVERRIDES.items() if ch == sku]
        if prcjam_curations:
            notes.append(f"PR-CJAM: {','.join(sorted(prcjam_curations))}")

        # CEX-EC source
        cexec_curations = [c for c, ch in CEX_EC_OVERRIDES.items() if ch == sku]
        if cexec_curations:
            notes.append(f"CEX-EC: {','.join(sorted(cexec_curations))}")

        # Demand breakdown
        rc_qty = rc_demand.get(sku, 0)
        sh_qty = sh_demand.get(sku, 0)
        if rc_qty or sh_qty:
            sources = []
            if rc_qty:
                sources.append(f"RC:{rc_qty}")
            if sh_qty:
                sources.append(f"SH:{sh_qty}")
            notes.append(f"({' + '.join(sources)})")

        rows.append({
            "SKU": sku,
            "Name": name,
            "Sliced On Hand": sl,
            "Wheel Potential": wp,
            "Recharge Demand": rc_qty,
            "Shopify Demand": sh_qty,
            "Total Demand": dm,
            "Net Position": net,
            "Wheels to Cut": wheels_to_cut if wheels_to_cut else "",
            "Pcs from Cut": pcs_from_cut if pcs_from_cut else "",
            "Notes": "; ".join(notes),
        })

    # ── Step 4: CEX-EC Suggestions ─────────────────────────────────────
    print("\n[5] CEX-EC Suggestions (prioritize deep inventory)...")
    surplus_rank = []
    for r in rows:
        if r["Total Demand"] > 0 or r["Sliced On Hand"] > 100 or r["Wheel Potential"] > 200:
            net = r["Net Position"]
            if net > 50:
                surplus_rank.append((r["SKU"], r["Name"], net,
                                     r["Sliced On Hand"], r["Wheel Potential"]))
    surplus_rank.sort(key=lambda x: -x[2])
    print("    Top surplus cheeses for CEX-EC:")
    for sku, name, surplus, sl, wp in surplus_rank[:12]:
        cur_assign = [c for c, ch in settings.get("cex_ec", {}).items() if ch == sku]
        assign_str = f" (current: {','.join(cur_assign)})" if cur_assign else ""
        print(f"      {sku:12} {name[:32]:32} net={surplus:>5}  sl={sl:>5} whl={wp:>5}{assign_str}")

    # ── Step 5: Output ─────────────────────────────────────────────────
    # Summary table — only rows with demand
    print("\n" + "=" * 140)
    print(f"{'SKU':14} {'Name':32} {'Sliced':>7} {'WhlPot':>7} {'RC Dem':>7} {'SH Dem':>7} {'Total':>7} {'Net':>7} {'Cut':>5} {'Pcs':>6}  Notes")
    print("-" * 140)
    for r in rows:
        if r["Total Demand"] > 0 or r["Wheels to Cut"]:
            net_val = r["Net Position"]
            net_str = f"({abs(net_val)})" if net_val < 0 else str(net_val)
            pcs_str = str(r["Pcs from Cut"]) if r["Pcs from Cut"] else ""
            print(f"{r['SKU']:14} {r['Name'][:32]:32} {r['Sliced On Hand']:>7} {r['Wheel Potential']:>7} "
                  f"{r['Recharge Demand']:>7} {r['Shopify Demand']:>7} "
                  f"{r['Total Demand']:>7} {net_str:>7} {str(r['Wheels to Cut']):>5} {pcs_str:>6}  "
                  f"{r['Notes'][:45]}")

    # Shortages
    if shortages:
        print(f"\n{'*** SHORTAGES ***':^70}")
        print("-" * 70)
        for sku, name, deficit in sorted(shortages, key=lambda x: -x[2]):
            print(f"  {sku:14} {name[:35]:35} short by {deficit:>5}")

    # Cut order
    if cut_needed:
        print(f"\n{'*** WHEELS TO CUT ***':^80}")
        print("-" * 80)
        print(f"  {'SKU':14} {'Name':32} {'Whls':>5} {'/ Avl':>6} {'Pcs':>6}  {'Per Whl':>8}")
        print(f"  {'-'*14} {'-'*32} {'-'*5} {'-'*6} {'-'*6}  {'-'*8}")
        total_pcs = 0
        for sku, name, wheels, pcs in sorted(cut_needed, key=lambda x: -x[2]):
            wh = WHEEL_INVENTORY[sku]
            total_pcs += pcs
            print(f"  {sku:14} {name[:32]:32} {wheels:>5} / {wh['wheels']:>3}  {pcs:>5}  ~{int(wh['wt']*WHEEL_TO_SLICE)} pcs/whl")
        print(f"  {'':14} {'TOTAL':32} {'':>5} {'':>6}  {total_pcs:>5}")

    # PR-CJAM assignments
    print(f"\n{'*** PR-CJAM ASSIGNMENTS ***':^70}")
    print("-" * 70)
    for cur in ["MONG", "MDT", "OWC", "SPN", "ALPN", "ALPT", "ISUN", "HHIGH", "BYO", "GEN", "NMS", "SS"]:
        ch = PR_CJAM_OVERRIDES.get(cur, "?")
        ch_name = inventory_names.get(ch, WHEEL_INVENTORY.get(ch, {}).get("name", ch))
        print(f"  {cur:8} -> {ch:12} ({ch_name})")

    # CEX-EC assignments
    print(f"\n{'*** CEX-EC ASSIGNMENTS ***':^70}")
    print("-" * 70)
    for cur in ["MONG", "MDT", "OWC", "SPN", "ALPN", "ISUN", "HHIGH", "BYO", "SS", "NMS", "MS"]:
        if cur in CEXEC_SPLITS_OVERRIDES:
            parts = []
            for ssku, pct in CEXEC_SPLITS_OVERRIDES[cur].items():
                sname = inventory_names.get(ssku, WHEEL_INVENTORY.get(ssku, {}).get("name", ssku))
                parts.append(f"{ssku} {int(pct*100)}% ({sname})")
            print(f"  {cur:8} -> {' + '.join(parts)}")
        else:
            ch = CEX_EC_OVERRIDES.get(cur, "?")
            ch_name = inventory_names.get(ch, WHEEL_INVENTORY.get(ch, {}).get("name", ch))
            print(f"  {cur:8} -> {ch:12} ({ch_name})")

    # Write CSV
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    fieldnames = ["SKU", "Name", "Sliced On Hand", "Wheel Potential",
                  "Recharge Demand", "Shopify Demand", "Total Demand",
                  "Net Position", "Wheels to Cut", "Pcs from Cut", "Notes"]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nCSV exported to: {OUTPUT_CSV}")
    print(f"  Total rows: {len(rows)}")
    print(f"  SKUs with demand: {sum(1 for r in rows if r['Total Demand'] > 0)}")
    print(f"  Total cheese demand: {sum(r['Total Demand'] for r in rows)} units")
    print(f"  Shortages: {len(shortages)}")
    print(f"  Wheels to cut: {sum(w for _, _, w, _ in cut_needed)}")
    print(f"  Pcs from cuts: {sum(p for _, _, _, p in cut_needed)}")


if __name__ == "__main__":
    main()
