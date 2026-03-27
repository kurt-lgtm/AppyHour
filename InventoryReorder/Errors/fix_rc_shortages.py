"""Fix shortage SKUs on Recharge queued charges for 3/21.

Actions:
  AC-LFOLIVE: remove if paid, swap to AC-PPCM if curation
  AC-MARC: remove if paid, swap to AC-SMAL if curation
  MT-BRAS: remove if paid
  CH-ALPHA: remove if paid, swap to CH-HCGU if curation
  CH-BRIE: swap to CH-EBRIE (paid -> paid variant, curation -> $0 variant)
  AC-PBLINI: remove if paid
  CH-GAOP: remove if paid

Usage:
    python fix_rc_shortages.py              # dry-run
    python fix_rc_shortages.py --commit     # apply
"""
import requests, json, sys, time, csv
from collections import defaultdict
from datetime import datetime, timedelta

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

RC_TOKEN_READ = settings["recharge_api_token"]
RC_TOKEN_WRITE = "sk_2x2_f998f08c853bd9391790b7760b449a60140c37dc0b3da48c4a54f7e0c7e67d10"
BASE_URL = "https://api.rechargeapps.com"

COMMIT = "--commit" in sys.argv
TARGET_DATE = "2026-03-21"

CSV_PATH = r"C:\Users\Work\Claude Projects\charges_queued-2026.03.20-10_31_27-7da7632d082d4eedaffca232ea525511.csv"

# SKU swap/remove rules
RULES = {
    "AC-LFOLIVE": {"paid": "remove", "curation": "AC-PPCM"},
    "AC-MARC":    {"paid": "remove", "curation": "AC-SMAL"},
    "MT-BRAS":    {"paid": "remove", "curation": None},  # leave curation alone
    "CH-ALPHA":   {"paid": "remove", "curation": "CH-HCGU"},
    "CH-BRIE":    {"paid": "CH-EBRIE", "curation": "CH-EBRIE"},
    "AC-PBLINI":  {"paid": "remove", "curation": None},
    "CH-GAOP":    {"paid": "remove", "curation": None},
}

# Variant IDs for swaps ($0 curation variants)
SWAP_VARIANTS = {
    "AC-PPCM":  "49887127666968",   # $0 Piri Piri Cocktail Mix*
    "AC-SMAL":  "48786762727704",   # $0 Sweet & Smoky Almonds*
    "CH-HCGU":  "51723866571032",   # $0 Honey Clover Gouda*
    "CH-EBRIE": "51232419578136",   # $0 Triple Cream Excellence*
}

# Paid variant for CH-EBRIE
PAID_VARIANTS = {
    "CH-EBRIE": "51835817034008",   # $9 Triple Cream Excellence
}

# Product IDs (needed for bundle_selection items)
PRODUCT_IDS = {
    "AC-PPCM":  "9687311745304",
    "AC-SMAL":  "9369854771480",
    "CH-HCGU":  "10110685610264",
    "CH-EBRIE": "9977241534744",
}


def _headers(write=False):
    return {
        "X-Recharge-Access-Token": RC_TOKEN_WRITE if write else RC_TOKEN_READ,
        "Content-Type": "application/json",
        "X-Recharge-Version": "2021-11",
    }


def rc_get(endpoint, params=None):
    for attempt in range(5):
        resp = requests.get(f"{BASE_URL}{endpoint}", headers=_headers(),
                            params=params, timeout=30)
        if resp.status_code == 429:
            wait = int(resp.headers.get("retry-after", "5"))
            time.sleep(wait)
            continue
        resp.raise_for_status()
        time.sleep(0.5)
        return resp.json()
    raise Exception(f"Max retries on GET {endpoint}")


def rc_put(endpoint, body):
    for attempt in range(5):
        resp = requests.put(f"{BASE_URL}{endpoint}", headers=_headers(write=True),
                            json=body, timeout=30)
        if resp.status_code == 429:
            wait = int(resp.headers.get("retry-after", "5"))
            time.sleep(wait)
            continue
        resp.raise_for_status()
        time.sleep(0.3)
        return resp.json()
    raise Exception(f"Max retries on PUT {endpoint}")


def rc_delete(endpoint):
    for attempt in range(5):
        resp = requests.delete(f"{BASE_URL}{endpoint}", headers=_headers(write=True),
                               timeout=30)
        if resp.status_code == 429:
            wait = int(resp.headers.get("retry-after", "5"))
            time.sleep(wait)
            continue
        if resp.status_code not in (200, 204):
            resp.raise_for_status()
        time.sleep(0.5)
        return


def _extract_variant_id(li):
    evid = li.get("external_variant_id")
    if isinstance(evid, dict):
        return str(evid.get("ecommerce", "") or "")
    return str(evid or li.get("shopify_variant_id") or li.get("variant_id") or "")


def main():
    mode = "COMMIT" if COMMIT else "DRY-RUN"
    print(f"\n{'='*60}")
    print(f"  Fix Recharge shortage SKUs for {TARGET_DATE} [{mode}]")
    print(f"{'='*60}\n")

    # 1. Read CSV to find charges with target SKUs
    print("Reading queued charges CSV...")
    charge_skus = defaultdict(lambda: {"skus": set(), "email": "", "customer_id": ""})
    with open(CSV_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sched = (row.get("scheduled_at") or "").strip()
            if not sched.startswith(TARGET_DATE):
                continue
            sku = (row.get("line_item_sku") or "").strip()
            if sku in RULES:
                cid = row.get("charge_id", "")
                charge_skus[cid]["skus"].add(sku)
                charge_skus[cid]["email"] = row.get("email", "")
                charge_skus[cid]["customer_id"] = row.get("customer_id", "")

    print(f"  Found {len(charge_skus)} charges with target SKUs\n")

    # 2. Process each charge
    results = {"removed": 0, "swapped": 0, "skipped": 0, "failed": 0}

    for charge_id, info in charge_skus.items():
        target_skus = info["skus"]
        print(f"\n  Charge {charge_id} ({info['email']}) — {', '.join(sorted(target_skus))}")

        # Fetch charge details from API
        try:
            charge_data = rc_get(f"/charges/{charge_id}")
            charge = charge_data.get("charge", {})
        except Exception as e:
            print(f"    FAILED to fetch charge: {e}")
            results["failed"] += len(target_skus)
            continue

        line_items = charge.get("line_items", [])
        scheduled_at = charge.get("scheduled_at", "")[:10]

        for li in line_items:
            sku = (li.get("sku") or "").strip()
            if sku not in target_skus:
                continue

            rule = RULES[sku]
            purchase_item_id = li.get("purchase_item_id")
            purchase_item_type = li.get("purchase_item_type", "")
            subscription_id = li.get("subscription_id")
            variant_id = _extract_variant_id(li)

            is_onetime = purchase_item_type == "onetime"
            is_subscription = purchase_item_type == "subscription"

            print(f"    {sku}: type={purchase_item_type}, pid={purchase_item_id}")

            if is_onetime:
                # Paid one-time item
                action = rule["paid"]
                if action == "remove":
                    print(f"      -> REMOVE onetime {purchase_item_id}")
                    if COMMIT:
                        try:
                            rc_delete(f"/onetimes/{purchase_item_id}")
                            print(f"      DELETED")
                            results["removed"] += 1
                        except Exception as e:
                            print(f"      DELETE FAILED: {e}")
                            results["failed"] += 1
                    else:
                        results["removed"] += 1
                elif action:
                    # Swap to different SKU (e.g. CH-BRIE -> CH-EBRIE paid)
                    new_variant = PAID_VARIANTS.get(action, SWAP_VARIANTS.get(action))
                    print(f"      -> SWAP onetime to {action} (variant {new_variant})")
                    if COMMIT and new_variant:
                        try:
                            rc_put(f"/onetimes/{purchase_item_id}", {
                                "external_variant_id": new_variant,
                                "sku": action,
                            })
                            print(f"      UPDATED")
                            results["swapped"] += 1
                        except Exception as e:
                            print(f"      UPDATE FAILED: {e}")
                            results["failed"] += 1
                    else:
                        results["swapped"] += 1
                else:
                    print(f"      -> SKIP (no action for paid curation-only rule)")
                    results["skipped"] += 1

            elif is_subscription:
                # Curation/subscription item — need to modify bundle_selections
                action = rule["curation"]
                if action is None:
                    print(f"      -> SKIP (no curation action)")
                    results["skipped"] += 1
                    continue

                sub_id = subscription_id or purchase_item_id
                if not sub_id:
                    print(f"      -> SKIP (no subscription ID)")
                    results["skipped"] += 1
                    continue

                new_variant = SWAP_VARIANTS.get(action)
                new_product = PRODUCT_IDS.get(action)
                if not new_variant or not new_product:
                    print(f"      -> SKIP (no variant mapping for {action})")
                    results["skipped"] += 1
                    continue

                print(f"      -> SWAP in bundle_selections: {sku} -> {action}")

                if COMMIT:
                    try:
                        # Get bundle selections
                        bs_data = rc_get("/bundle_selections", params={"purchase_item_ids": sub_id})
                        selections = bs_data.get("bundle_selections", [])
                        upcoming = [s for s in selections if s.get("charge_id") is None]

                        if not upcoming:
                            print(f"      No upcoming bundle_selection found")
                            results["failed"] += 1
                            continue

                        bs = upcoming[0]
                        bs_id = bs["id"]
                        bs_items = bs.get("items", [])

                        # Find and replace the target item
                        new_items = []
                        swapped = False
                        for item in bs_items:
                            item_vid = str(item.get("external_variant_id", ""))
                            if item_vid == variant_id and not swapped:
                                # Replace with new variant
                                new_items.append({
                                    "collection_id": item.get("collection_id", ""),
                                    "collection_source": item.get("collection_source", "shopify"),
                                    "external_product_id": new_product,
                                    "external_variant_id": new_variant,
                                    "quantity": item.get("quantity", 1),
                                })
                                swapped = True
                            else:
                                new_items.append({
                                    "collection_id": item.get("collection_id", ""),
                                    "collection_source": item.get("collection_source", "shopify"),
                                    "external_product_id": item.get("external_product_id", ""),
                                    "external_variant_id": item.get("external_variant_id", ""),
                                    "quantity": item.get("quantity", 1),
                                })

                        if not swapped:
                            print(f"      Variant {variant_id} not found in bundle_selection")
                            results["failed"] += 1
                            continue

                        rc_put(f"/bundle_selections/{bs_id}", {"items": new_items})
                        print(f"      Bundle selection updated")

                        # Date-shuffle to regenerate line_items
                        temp_date = (datetime.strptime(scheduled_at, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
                        rc_put(f"/subscriptions/{sub_id}", {"next_charge_scheduled_at": temp_date})
                        time.sleep(0.5)
                        rc_put(f"/subscriptions/{sub_id}", {"next_charge_scheduled_at": scheduled_at})
                        print(f"      Date-shuffled to regenerate")
                        results["swapped"] += 1

                    except Exception as e:
                        print(f"      FAILED: {e}")
                        results["failed"] += 1
                else:
                    results["swapped"] += 1

            else:
                print(f"      -> SKIP (unknown type: {purchase_item_type})")
                results["skipped"] += 1

    print(f"\n{'='*60}")
    print(f"  Results: {results['removed']} removed, {results['swapped']} swapped, {results['skipped']} skipped, {results['failed']} failed")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
