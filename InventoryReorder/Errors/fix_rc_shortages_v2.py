"""Fix shortage SKUs on Recharge queued charges for 3/21 (v2 - parent box lookup).

Finds the AHB- parent subscription on each charge, then modifies its
bundle_selections to swap only the target item.

Usage:
    python fix_rc_shortages_v2.py              # dry-run
    python fix_rc_shortages_v2.py --commit     # apply
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

# SKU swap rules (curation only - paid onetimes already handled in v1)
SWAP_RULES = {
    "AC-LFOLIVE": "AC-PPCM",
    "AC-MARC":    "AC-SMAL",
    "CH-ALPHA":   "CH-HCGU",
    "CH-BRIE":    "CH-EBRIE",
    "MT-BRAS":    "MT-SOP",
    "AC-PBLINI":  "AC-FLH",
    "CH-GAOP":    "CH-FONTAL",
}

# $0 variant IDs for replacements
SWAP_VARIANTS = {
    "AC-PPCM":   "49887127666968",
    "AC-SMAL":   "48786762727704",
    "CH-HCGU":   "51723866571032",
    "CH-EBRIE":  "51232419578136",
    "MT-SOP":    "49467543257368",
    "AC-FLH":    "50637838352664",
    "CH-FONTAL": "51196926394648",
}

SWAP_PRODUCTS = {
    "AC-PPCM":   "9687311745304",
    "AC-SMAL":   "9369854771480",
    "CH-HCGU":   "10110685610264",
    "CH-EBRIE":  "9977241534744",
    "MT-SOP":    "9593852068120",
    "AC-FLH":    "9868763431192",
    "CH-FONTAL": "9974091219224",
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


def _extract_variant_id(li):
    evid = li.get("external_variant_id")
    if isinstance(evid, dict):
        return str(evid.get("ecommerce", "") or "")
    return str(evid or li.get("shopify_variant_id") or li.get("variant_id") or "")


def find_parent_box_sub(charge):
    """Find the AHB- parent subscription ID on a charge."""
    for li in charge.get("line_items", []):
        sku = (li.get("sku") or "").strip().upper()
        if sku.startswith("AHB-") and li.get("subscription_id"):
            return str(li["subscription_id"])
    # Fallback: check purchase_item_id
    for li in charge.get("line_items", []):
        sku = (li.get("sku") or "").strip().upper()
        if sku.startswith("AHB-") and li.get("purchase_item_id"):
            return str(li["purchase_item_id"])
    return None


def find_target_variant_in_bs(bs_items, target_sku, charge):
    """Find the variant_id of the target SKU in bundle_selection items by cross-referencing charge line items."""
    # First find the variant_id of the target SKU from the charge
    for li in charge.get("line_items", []):
        sku = (li.get("sku") or "").strip()
        if sku == target_sku:
            vid = _extract_variant_id(li)
            if vid:
                return vid
    return None


def main():
    mode = "COMMIT" if COMMIT else "DRY-RUN"
    print(f"\n{'='*60}")
    print(f"  Fix RC shortage SKUs v2 - {TARGET_DATE} [{mode}]")
    print(f"{'='*60}\n")

    # 1. Read CSV to find charges with target SKUs
    print("Reading queued charges CSV...")
    charge_targets = defaultdict(lambda: {"skus": [], "email": ""})
    with open(CSV_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sched = (row.get("scheduled_at") or "").strip()
            if not sched.startswith(TARGET_DATE):
                continue
            sku = (row.get("line_item_sku") or "").strip()
            if sku in SWAP_RULES:
                cid = row.get("charge_id", "")
                if sku not in charge_targets[cid]["skus"]:
                    charge_targets[cid]["skus"].append(sku)
                charge_targets[cid]["email"] = row.get("email", "")

    # Filter out charges already handled in v1 (only keep ones that failed)
    # For simplicity, just process all — already-swapped items won't be found in bundle_selection
    print(f"  Found {len(charge_targets)} charges with target SKUs\n")

    results = {"swapped": 0, "skipped": 0, "failed": 0, "already_done": 0}

    for charge_id, info in charge_targets.items():
        target_skus = info["skus"]
        print(f"\n  Charge {charge_id} ({info['email']}) - {', '.join(target_skus)}")

        # Fetch charge
        try:
            charge = rc_get(f"/charges/{charge_id}").get("charge", {})
        except Exception as e:
            print(f"    FAILED fetch charge: {e}")
            results["failed"] += len(target_skus)
            continue

        scheduled_at = charge.get("scheduled_at", "")[:10]

        # Find parent AHB- subscription
        parent_sub_id = find_parent_box_sub(charge)
        if not parent_sub_id:
            print(f"    No AHB- parent subscription found, skipping")
            results["skipped"] += len(target_skus)
            continue

        print(f"    Parent box sub: {parent_sub_id}")

        # Get bundle_selections for parent subscription
        try:
            bs_data = rc_get("/bundle_selections", params={"purchase_item_ids": parent_sub_id})
            selections = bs_data.get("bundle_selections", [])
        except Exception as e:
            print(f"    FAILED fetch bundle_selections: {e}")
            results["failed"] += len(target_skus)
            continue

        upcoming = [s for s in selections if s.get("charge_id") is None]
        if not upcoming:
            print(f"    No upcoming bundle_selection ({len(selections)} total)")
            results["skipped"] += len(target_skus)
            continue

        bs = upcoming[0]
        bs_id = bs["id"]
        bs_items = bs.get("items", [])
        print(f"    Bundle selection {bs_id}: {len(bs_items)} items")

        # Process each target SKU
        items_modified = False
        new_items = list(bs_items)  # start with current items

        for target_sku in target_skus:
            swap_to = SWAP_RULES[target_sku]
            target_vid = find_target_variant_in_bs(bs_items, target_sku, charge)

            if not target_vid:
                print(f"    {target_sku}: variant not found in charge")
                results["failed"] += 1
                continue

            # Find and replace in items list
            found = False
            for i, item in enumerate(new_items):
                item_vid = str(item.get("external_variant_id", ""))
                if item_vid == target_vid:
                    # Replace with swap variant, keep same collection_id
                    new_items[i] = {
                        "collection_id": item.get("collection_id", ""),
                        "collection_source": item.get("collection_source", "shopify"),
                        "external_product_id": SWAP_PRODUCTS.get(swap_to, ""),
                        "external_variant_id": SWAP_VARIANTS.get(swap_to, ""),
                        "quantity": item.get("quantity", 1),
                    }
                    print(f"    {target_sku} -> {swap_to} (variant {target_vid} -> {SWAP_VARIANTS[swap_to]})")
                    found = True
                    items_modified = True
                    break

            if not found:
                print(f"    {target_sku}: variant {target_vid} not in bundle_selection (maybe already swapped)")
                results["already_done"] += 1

        if not items_modified:
            continue

        if not COMMIT:
            results["swapped"] += sum(1 for s in target_skus if find_target_variant_in_bs(bs_items, s, charge))
            continue

        # PUT updated bundle_selection
        try:
            # Format items for PUT
            put_items = []
            for item in new_items:
                put_items.append({
                    "collection_id": item.get("collection_id", ""),
                    "collection_source": item.get("collection_source", "shopify"),
                    "external_product_id": str(item.get("external_product_id", "")),
                    "external_variant_id": str(item.get("external_variant_id", "")),
                    "quantity": item.get("quantity", 1),
                })
            rc_put(f"/bundle_selections/{bs_id}", {"items": put_items})
            print(f"    Bundle selection updated")
        except Exception as e:
            print(f"    PUT FAILED: {e}")
            results["failed"] += len(target_skus)
            continue

        # Date-shuffle
        try:
            temp_date = (datetime.strptime(scheduled_at, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            rc_put(f"/subscriptions/{parent_sub_id}", {"next_charge_scheduled_at": temp_date})
            time.sleep(0.5)
            rc_put(f"/subscriptions/{parent_sub_id}", {"next_charge_scheduled_at": scheduled_at})
            print(f"    Date-shuffled")
            results["swapped"] += len(target_skus)
        except Exception as e:
            print(f"    DATE SHUFFLE FAILED: {e}")
            results["failed"] += len(target_skus)

    print(f"\n{'='*60}")
    print(f"  Results: {results['swapped']} swapped, {results['skipped']} skipped, {results['already_done']} already done, {results['failed']} failed")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
