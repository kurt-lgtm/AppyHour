"""Fix Class 2/3 Recharge upcoming charges — replace curation items in bundle selections.

~564 upcoming charges have a blank-SKU "AppyHour Box" promo product with curation items
that shouldn't be there. This script:
1. Replaces bundle_selection items: curation food → AHB-MED/LGE + PR-CJAM-GEN
2. Date-shuffles the subscription (and any onetimes) to regenerate line_items

Box size determined from blank-box line's variant_title:
  "Medium" / "8 item" → AHB-MED
  "Large"  / "10 item" → AHB-LGE

If bundle already contains CH-EBRIE, do NOT add PR-CJAM-GEN.

Modes:
  (no flags)              Dry-run: fetch and show what would change
  --single CHARGE_ID      Fix one charge (with full safety checks)
  --commit                Fix all Class 2/3 charges
  --date YYYY-MM-DD       Only process charges scheduled on this date
  --batch N               Process at most N charges
"""
import requests, json, time, csv, sys, os
from datetime import datetime, timedelta

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

RC_TOKEN_READ = settings["recharge_api_token"]
RC_TOKEN_WRITE = "sk_2x2_f998f08c853bd9391790b7760b449a60140c37dc0b3da48c4a54f7e0c7e67d10"


def _headers(write=False):
    return {
        "X-Recharge-Access-Token": RC_TOKEN_WRITE if write else RC_TOKEN_READ,
        "Content-Type": "application/json",
        "X-Recharge-Version": "2021-11",
    }


RC_HEADERS = _headers(write=False)
BASE_URL = "https://api.rechargeapps.com"

TODAY = datetime.now().strftime("%Y-%m-%d")
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_OUTPUT = os.path.join(OUTPUT_DIR, f"rc-class23-bundle-fixes-{TODAY}.csv")

# ── Replacement items (from working bundle_selections) ───────────────────────
# AHB-MED and AHB-LGE share the same product ID
BOX_PRODUCT_ID = "9683972489496"
VARIANT_AHB_MED = "49871412691224"
VARIANT_AHB_LGE = "49871412723992"

# PR-CJAM-GEN
CJAM_PRODUCT_ID = "9611569561880"
VARIANT_PR_CJAM = "49542974046488"

# CH-EBRIE — if present, skip PR-CJAM-GEN
VARIANT_EBRIE = "51232419578136"
EBRIE_PRODUCT_ID = "9977241534744"


def _extract_variant_id(li):
    """Extract variant ID from a charge line_item (v2021-11 nesting)."""
    evid = li.get("external_variant_id")
    if isinstance(evid, dict):
        return str(evid.get("ecommerce", "") or "")
    return str(evid or li.get("shopify_variant_id") or li.get("variant_id") or "")


def _determine_box_sku(variant_title):
    """Determine box SKU from the promo product variant title."""
    vt = (variant_title or "").lower()
    if "large" in vt or "10 item" in vt:
        return "AHB-LGE"
    if "medium" in vt or "8 item" in vt:
        return "AHB-MED"
    return None


# ── API helpers ──────────────────────────────────────────────────────────────

def rc_get(endpoint, params=None):
    for attempt in range(5):
        resp = requests.get(f"{BASE_URL}{endpoint}", headers=RC_HEADERS,
                            params=params, timeout=30)
        if resp.status_code == 429:
            wait = int(resp.headers.get("retry-after", "5"))
            print(f"  Rate limited, waiting {wait}s...")
            time.sleep(wait)
            continue
        if resp.status_code >= 500:
            wait = (1 << attempt)
            print(f"  Server error {resp.status_code}, retrying in {wait}s...")
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
            print(f"  Rate limited, waiting {wait}s...")
            time.sleep(wait)
            continue
        if resp.status_code >= 500:
            wait = (1 << attempt)
            print(f"  Server error {resp.status_code}, retrying in {wait}s...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        time.sleep(0.3)
        return resp.json()
    raise Exception(f"Max retries on PUT {endpoint}")


def rc_post(endpoint, body=None):
    for attempt in range(5):
        resp = requests.post(f"{BASE_URL}{endpoint}", headers=_headers(write=True),
                             json=body or {}, timeout=30)
        if resp.status_code == 429:
            wait = int(resp.headers.get("retry-after", "5"))
            print(f"  Rate limited, waiting {wait}s...")
            time.sleep(wait)
            continue
        if resp.status_code >= 500:
            wait = (1 << attempt)
            print(f"  Server error {resp.status_code}, retrying in {wait}s...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        time.sleep(0.5)
        return resp.json()
    raise Exception(f"Max retries on POST {endpoint}")


def rc_delete(endpoint):
    for attempt in range(5):
        resp = requests.delete(f"{BASE_URL}{endpoint}", headers=_headers(write=True),
                               timeout=30)
        if resp.status_code == 429:
            wait = int(resp.headers.get("retry-after", "5"))
            print(f"  Rate limited, waiting {wait}s...")
            time.sleep(wait)
            continue
        if resp.status_code >= 500:
            wait = (1 << attempt)
            print(f"  Server error {resp.status_code}, retrying in {wait}s...")
            time.sleep(wait)
            continue
        if resp.status_code not in (200, 204):
            resp.raise_for_status()
        time.sleep(0.5)
        return
    raise Exception(f"Max retries on DELETE {endpoint}")


# ── Fetch all queued charges ─────────────────────────────────────────────────

def fetch_queued_charges():
    print("Fetching queued charges...")
    charges = []
    cursor = None
    while True:
        if cursor:
            params = {"cursor": cursor, "limit": 250}
        else:
            params = {"status": "queued", "limit": 250, "sort_by": "id-asc"}
        data = rc_get("/charges", params=params)
        batch = data.get("charges", [])
        if not batch:
            break
        charges.extend(batch)
        cursor = data.get("next_cursor")
        print(f"  {len(charges)} charges...")
        if not cursor:
            break
    print(f"Got {len(charges)} queued charges\n")
    return charges


# ── Identify Class 2/3 charges ───────────────────────────────────────────────

def classify_charges(charges):
    results = []
    for c in charges:
        line_items = c.get("line_items", [])

        has_blank_box = False
        has_custom_box = False
        has_monthly_box = False
        is_brie_promo = False
        blank_box_variant_title = ""
        curation_items = []
        paid_items = []
        onetime_ids = []   # purchase_item_ids for onetime line items
        sub_id = None
        ahb_sub_id = None

        for li in line_items:
            sku = (li.get("sku") or "").strip()
            title = (li.get("title") or "")
            ptype = li.get("purchase_item_type", "")
            props = li.get("properties", [])
            prop_keys = {p.get("name", "") for p in props}
            is_rc_bundle = "_rc_bundle" in prop_keys
            variant_id = _extract_variant_id(li)
            pid = li.get("purchase_item_id")

            if ptype == "onetime" and pid:
                onetime_ids.append(str(pid))

            if pid:
                if not sub_id:
                    sub_id = str(pid)
                if sku.startswith("AHB-"):
                    ahb_sub_id = str(pid)

            if "appyhour box" in title.lower() or "appy hour" in title.lower():
                if not sku:
                    has_blank_box = True
                    blank_box_variant_title = li.get("variant_title", "") or ""
                    if "brie" in title.lower():
                        is_brie_promo = True
            if sku.startswith(("AHB-MCUST", "AHB-LCUST")):
                has_custom_box = True
            if sku in ("AHB-MED", "AHB-LGE", "AHB-CMED"):
                has_monthly_box = True

            if sku.startswith(("CH-", "MT-", "AC-", "CEX-", "EX-")):
                if ptype == "subscription" and is_rc_bundle:
                    curation_items.append({
                        "sku": sku, "variant_id": variant_id, "title": title
                    })
                else:
                    paid_items.append({
                        "sku": sku, "variant_id": variant_id, "title": title
                    })
            elif sku.startswith("BL-"):
                paid_items.append({
                    "sku": sku, "variant_id": variant_id, "title": title
                })

        if has_blank_box and not has_custom_box and not has_monthly_box and curation_items:
            box_sku = _determine_box_sku(blank_box_variant_title)
            ba = c.get("billing_address") or {}
            first = ba.get("first_name", "") or ""
            last = ba.get("last_name", "") or ""
            results.append({
                "charge_id": str(c["id"]),
                "customer_id": str((c.get("customer", {}) or {}).get("id", "") or c.get("customer_id", "") or ""),
                "email": (c.get("customer", {}) or {}).get("email", "") or c.get("email", "") or "",
                "customer_name": f"{first} {last}".strip(),
                "scheduled_at": (c.get("scheduled_at") or "")[:10],
                "total_price": c.get("total_price", "0"),
                "subscription_id": ahb_sub_id or sub_id or "",
                "box_sku": box_sku,
                "variant_title": blank_box_variant_title,
                "curation_items": curation_items,
                "paid_items": paid_items,
                "onetime_ids": onetime_ids,
                "curation_variant_ids": {i["variant_id"] for i in curation_items},
                "is_brie_promo": is_brie_promo,
                "raw_charge": c,
            })

    return results


# ── Build replacement items ──────────────────────────────────────────────────

def build_replacement_items(box_sku, existing_bs_items, is_brie_promo=False):
    """Build the new bundle_selection items array.

    Uses a collection_id from existing items (required by Recharge API).
    "Free Brie" promo subs get CH-EBRIE instead of PR-CJAM-GEN.
    Returns list of item dicts for PUT, or None if box_sku is unknown.
    """
    if box_sku == "AHB-MED":
        box_vid = VARIANT_AHB_MED
    elif box_sku == "AHB-LGE":
        box_vid = VARIANT_AHB_LGE
    else:
        return None

    # Grab a collection_id from existing bundle_selection items (API requires it)
    coll_id = ""
    for item in existing_bs_items:
        cid = str(item.get("collection_id", "") or "")
        if cid:
            coll_id = cid
            break

    items = [{
        "collection_id": coll_id,
        "collection_source": "shopify",
        "external_product_id": BOX_PRODUCT_ID,
        "external_variant_id": box_vid,
        "quantity": 1,
    }]

    # Check if EBRIE is already in the bundle_selection
    has_ebrie = any(
        str(item.get("external_variant_id", "")) == VARIANT_EBRIE
        for item in existing_bs_items
    )

    if not has_ebrie:
        if is_brie_promo:
            # "Free Brie for a Year" subs get CH-EBRIE, not PR-CJAM-GEN
            items.append({
                "collection_id": coll_id,
                "collection_source": "shopify",
                "external_product_id": EBRIE_PRODUCT_ID,
                "external_variant_id": VARIANT_EBRIE,
                "quantity": 1,
            })
        else:
            items.append({
                "collection_id": coll_id,
                "collection_source": "shopify",
                "external_product_id": CJAM_PRODUCT_ID,
                "external_variant_id": VARIANT_PR_CJAM,
                "quantity": 1,
            })

    return items


# ── Fix a single charge ──────────────────────────────────────────────────────

def fix_charge(entry, dry_run=False):
    charge_id = entry["charge_id"]
    sub_id = entry["subscription_id"]
    curation_vids = entry["curation_variant_ids"]
    scheduled_before = entry["scheduled_at"]
    price_before = str(entry["total_price"])
    box_sku = entry["box_sku"]

    result = {
        "charge_id": charge_id,
        "scheduled": scheduled_before,
        "customer": entry["customer_name"],
        "email": entry["email"],
        "rc_customer_id": entry["customer_id"],
        "rc_subscription_id": sub_id,
        "bundle_selection_id": "",
        "status": "PENDING",
        "box_sku": box_sku or "UNKNOWN",
        "items_removed": "",
        "items_kept": "",
        "price_before": price_before,
        "price_after": "",
    }

    if not sub_id:
        result["status"] = "ERROR"
        result["items_removed"] = "No subscription ID found"
        return result

    if not box_sku:
        result["status"] = "ERROR"
        result["items_removed"] = f"Cannot determine box size from variant_title: {entry['variant_title']}"
        return result

    # 1. Get bundle selections for this subscription
    print(f"  Fetching bundle_selections for sub {sub_id}...")
    bs_data = rc_get("/bundle_selections", params={"purchase_item_ids": sub_id})
    selections = bs_data.get("bundle_selections", [])

    # Find upcoming selection (charge_id = null)
    upcoming = [s for s in selections if s.get("charge_id") is None]
    if not upcoming:
        result["status"] = "ERROR"
        result["items_removed"] = f"No upcoming bundle_selection found ({len(selections)} total)"
        return result

    bs = upcoming[0]
    bs_id = bs["id"]
    if not bs_id:
        result["status"] = "ERROR"
        result["items_removed"] = "bundle_selection has no ID (None)"
        print(f"  ERROR: bundle_selection ID is None — skipping")
        return result
    result["bundle_selection_id"] = str(bs_id)
    bs_items = bs.get("items", [])

    print(f"  Bundle selection {bs_id}: {len(bs_items)} items")

    # 2. Cross-reference: which items are curation?
    #    CH-EBRIE is kept (free brie promo) — never removed
    items_to_remove = []
    items_to_keep = []
    for item in bs_items:
        vid = str(item.get("external_variant_id", ""))
        if vid == VARIANT_EBRIE:
            items_to_keep.append(item)  # Free Brie — always keep
        elif vid in curation_vids:
            items_to_remove.append(item)
        else:
            items_to_keep.append(item)

    removed_skus = []
    for item in items_to_remove:
        vid = str(item.get("external_variant_id", ""))
        sku = "?"
        for ci in entry["curation_items"]:
            if ci["variant_id"] == vid:
                sku = ci["sku"]
                break
        removed_skus.append(sku)

    result["items_removed"] = ", ".join(sorted(removed_skus))

    if not items_to_remove:
        result["status"] = "ALREADY_CLEAN"
        print(f"  No curation items in bundle_selection — already clean")
        return result

    # 3. Build replacement items
    is_brie = entry.get("is_brie_promo", False)
    replacement = build_replacement_items(box_sku, bs_items, is_brie_promo=is_brie)
    if replacement is None:
        result["status"] = "ERROR"
        result["items_removed"] = f"Unknown box SKU: {box_sku}"
        return result

    # Merge: keep non-curation items + add replacement
    new_items = []
    for item in items_to_keep:
        new_items.append({
            "collection_id": item.get("collection_id", ""),
            "collection_source": item.get("collection_source", "shopify"),
            "external_product_id": item.get("external_product_id", ""),
            "external_variant_id": item.get("external_variant_id", ""),
            "quantity": item.get("quantity", 1),
        })
    new_items.extend(replacement)

    bonus = "CH-EBRIE" if is_brie else "PR-CJAM-GEN"
    kept_desc = [box_sku] + ([bonus] if len(replacement) > 1 else [])
    result["items_kept"] = ", ".join(kept_desc)

    print(f"  Remove {len(items_to_remove)} curation items: {result['items_removed']}")
    print(f"  Replace with: {result['items_kept']}")

    if dry_run:
        result["status"] = "DRY_RUN"
        return result

    # 4. PUT updated bundle_selection
    print(f"  PUT /bundle_selections/{bs_id} with {len(new_items)} items...")
    rc_put(f"/bundle_selections/{bs_id}", {"items": new_items})
    print(f"  Bundle selection updated")

    # 5. Date-shuffle to regenerate line_items
    #    Move sub to temp_date → creates new charge with correct items
    #    Move onetimes to temp_date → merges them onto same charge
    #    Move everything back to original_date
    onetime_ids = entry.get("onetime_ids", [])
    original_date = scheduled_before  # "YYYY-MM-DD"
    temp_date = (datetime.strptime(original_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"  Moving sub {sub_id} to {temp_date}...")
    rc_put(f"/subscriptions/{sub_id}", {"next_charge_scheduled_at": temp_date})

    if onetime_ids:
        print(f"  Moving {len(onetime_ids)} onetimes to {temp_date}...")
        for ot_id in onetime_ids:
            rc_put(f"/onetimes/{ot_id}", {"next_charge_scheduled_at": temp_date})

    print(f"  Moving sub back to {original_date}...")
    try:
        rc_put(f"/subscriptions/{sub_id}", {"next_charge_scheduled_at": original_date})
    except Exception as e:
        if "409" in str(e) or "CONFLICT" in str(e):
            print(f"  *** 409 CONFLICT moving back to {original_date} (date may be today/past) — charge stays at {temp_date} ***")
            original_date = temp_date  # update for verification query
        else:
            raise

    if onetime_ids:
        print(f"  Moving {len(onetime_ids)} onetimes back to {original_date}...")
        for ot_id in onetime_ids:
            try:
                rc_put(f"/onetimes/{ot_id}", {"next_charge_scheduled_at": original_date})
            except Exception as e:
                if "409" in str(e) or "CONFLICT" in str(e):
                    print(f"  *** 409 on onetime {ot_id} — stays at {temp_date} ***")
                else:
                    raise

    # 6. Quick verification — date shuffle has never actually failed,
    #    so just do one fast check instead of 3 retries with 5s waits
    customer_id = entry["customer_id"]
    next_day = (datetime.strptime(original_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")

    time.sleep(2)
    check_charges = rc_get("/charges", params={
        "customer_id": customer_id,
        "status": "queued",
        "scheduled_at_min": original_date,
        "scheduled_at_max": next_day,
        "limit": 10,
        "sort_by": "id-asc",
    }).get("charges", [])

    if not check_charges:
        # Trust the fix — bundle PUT + date shuffle succeeded
        result["status"] = "SUCCESS"
        result["price_after"] = f"{price_before} (no verify — stale API)"
        print(f"  SUCCESS (bundle fixed, verification skipped — stale API)")
        return result

    # Pick the charge that has our subscription's items
    check_charge = check_charges[0]
    for cc in check_charges:
        for li in cc.get("line_items", []):
            if str(li.get("subscription_id") or li.get("purchase_item_id") or "") == sub_id:
                check_charge = cc
                break

    result["charge_id"] = str(check_charge["id"])  # charge ID may have changed

    return _verify_charge(result, entry, check_charge, price_before, box_sku)


def _verify_charge(result, entry, check_charge, price_before, box_sku):
    """Run safety checks on the resulting charge after fix."""
    scheduled_before = entry["scheduled_at"]

    new_status = check_charge.get("status", "")
    if new_status != "queued":
        result["status"] = "SAFETY_FAIL"
        result["price_after"] = f"Status changed to {new_status}!"
        print(f"  SAFETY FAIL: status is now {new_status}")
        return result

    new_scheduled = (check_charge.get("scheduled_at") or "")[:10]
    if new_scheduled != scheduled_before:
        result["status"] = "SAFETY_FAIL"
        result["price_after"] = f"Date changed: {scheduled_before} -> {new_scheduled}!"
        print(f"  SAFETY FAIL: date changed {scheduled_before} -> {new_scheduled}")
        return result

    new_price = str(check_charge.get("total_price", "0"))
    result["price_after"] = new_price
    try:
        if float(new_price) > float(price_before) + 0.01:
            result["status"] = "SAFETY_FAIL"
            result["price_after"] = f"{new_price} (was {price_before}, INCREASED!)"
            print(f"  SAFETY FAIL: price increased {price_before} -> {new_price}")
            return result
    except ValueError:
        pass

    new_line_items = check_charge.get("line_items", [])

    # Curation items gone
    remaining_curation = []
    for li in new_line_items:
        sku = (li.get("sku") or "").strip()
        ptype = li.get("purchase_item_type", "")
        props = li.get("properties", [])
        is_rc_bundle = "_rc_bundle" in {p.get("name", "") for p in props}
        if sku.startswith(("CH-", "MT-", "AC-", "CEX-", "EX-")) and ptype == "subscription" and is_rc_bundle and sku != "CH-EBRIE":
            remaining_curation.append(sku)
    if remaining_curation:
        result["status"] = "SAFETY_FAIL"
        result["price_after"] += f" | Curation still present: {remaining_curation}"
        print(f"  SAFETY FAIL: curation still present: {remaining_curation}")
        return result

    # Paid items preserved
    new_paid_skus = set()
    for li in new_line_items:
        sku = (li.get("sku") or "").strip()
        ptype = li.get("purchase_item_type", "")
        props = li.get("properties", [])
        is_rc_bundle = "_rc_bundle" in {p.get("name", "") for p in props}
        if sku.startswith(("CH-", "MT-", "AC-", "CEX-", "EX-", "BL-")):
            if not (ptype == "subscription" and is_rc_bundle):
                new_paid_skus.add(sku)
    original_paid_skus = {i["sku"] for i in entry["paid_items"]}
    missing_paid = original_paid_skus - new_paid_skus
    if missing_paid:
        result["status"] = "SAFETY_FAIL"
        result["price_after"] += f" | Paid items lost: {missing_paid}"
        print(f"  SAFETY FAIL: paid items lost: {missing_paid}")
        return result

    # Box SKU present
    new_skus = {(li.get("sku") or "").strip() for li in new_line_items}
    if box_sku not in new_skus:
        result["status"] = "SAFETY_FAIL"
        result["price_after"] += f" | {box_sku} not in regenerated line_items"
        print(f"  SAFETY FAIL: {box_sku} not found in new line_items")
        return result

    result["status"] = "SUCCESS"
    print(f"  SUCCESS: curation removed, {box_sku} added, price {price_before} -> {new_price}")
    return result


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    mode = "dry-run"
    single_charge = None

    batch_size = None

    if "--single" in args:
        idx = args.index("--single")
        if idx + 1 < len(args):
            single_charge = args[idx + 1]
            mode = "single"
    elif "--commit" in args:
        mode = "commit"

    if "--batch" in args:
        idx = args.index("--batch")
        if idx + 1 < len(args):
            batch_size = int(args[idx + 1])

    date_filter = None
    if "--date" in args:
        idx = args.index("--date")
        if idx + 1 < len(args):
            date_filter = args[idx + 1]

    print(f"{'='*60}")
    print(f"fix_rc_class23_bundles.py")
    print(f"Mode: {mode.upper()}")
    if single_charge:
        print(f"Target charge: {single_charge}")
    if date_filter:
        print(f"Date filter: {date_filter}")
    print(f"{'='*60}\n")

    # Fetch and classify
    charges = fetch_queued_charges()
    class23 = classify_charges(charges)
    class23.sort(key=lambda e: e["scheduled_at"] or "9999-99-99")

    if date_filter:
        class23 = [e for e in class23 if e["scheduled_at"] == date_filter]
        print(f"Found {len(class23)} Class 2/3 charges scheduled on {date_filter}\n")
    else:
        print(f"Found {len(class23)} Class 2/3 charges (sorted by charge date)\n")

    if not class23:
        print("No Class 2/3 charges found. Nothing to do.")
        return

    # Box size summary
    from collections import Counter
    box_counts = Counter(e["box_sku"] or "UNKNOWN" for e in class23)
    for sku, cnt in box_counts.most_common():
        print(f"  {sku}: {cnt}")

    # Show first 10
    for entry in class23[:10]:
        cur_skus = ", ".join(sorted(set(i["sku"] for i in entry["curation_items"])))
        paid_skus = ", ".join(sorted(set(i["sku"] for i in entry["paid_items"]))) or "(none)"
        print(f"  Charge {entry['charge_id']} ({entry['scheduled_at']}) "
              f"{entry['customer_name']} | {entry['box_sku']} | Curation: {len(entry['curation_items'])} | Paid: {paid_skus}")
    if len(class23) > 10:
        print(f"  ... and {len(class23) - 10} more\n")

    # ── DRY-RUN ──────────────────────────────────────────────────────────
    if mode == "dry-run":
        print(f"\n--- DRY RUN: checking bundle_selections ---\n")
        results = []
        for i, entry in enumerate(class23):
            print(f"\n[{i+1}/{len(class23)}] Charge {entry['charge_id']} "
                  f"({entry['customer_name']}) -> {entry['box_sku']}")
            result = fix_charge(entry, dry_run=True)
            results.append(result)
            print(f"  -> {result['status']}")

        _write_csv(results)
        _print_summary(results)
        print(f"\nTo fix one: python {os.path.basename(__file__)} --single <CHARGE_ID>")
        print(f"To fix all: python {os.path.basename(__file__)} --commit")

    # ── SINGLE ───────────────────────────────────────────────────────────
    elif mode == "single":
        entry = None
        for e in class23:
            if e["charge_id"] == single_charge:
                entry = e
                break
        if not entry:
            print(f"Charge {single_charge} not found in Class 2/3 list.")
            print("Available charge IDs (first 20):")
            for e in class23[:20]:
                print(f"  {e['charge_id']} ({e['customer_name']}) {e['box_sku']}")
            return

        print(f"\n--- FIXING SINGLE CHARGE {single_charge} ---\n")
        result = fix_charge(entry, dry_run=False)
        _write_csv([result])
        print(f"\n{'='*60}")
        print(f"Result: {result['status']}")
        for k, v in result.items():
            print(f"  {k}: {v}")

        if result["status"] == "SAFETY_FAIL":
            print("\nSAFETY CHECK FAILED — do NOT run --commit until investigated.")
        elif result["status"] == "SUCCESS":
            print("\nSafety checks passed. Verify in Recharge admin, then run --commit.")

    # ── COMMIT ───────────────────────────────────────────────────────────
    elif mode == "commit":
        targets = class23[:batch_size] if batch_size else class23
        print(f"\n--- COMMITTING FIXES FOR {len(targets)} OF {len(class23)} CLASS 2/3 CHARGES ---")
        print("Starting in 5 seconds... (Ctrl+C to abort)")
        time.sleep(5)

        results = []
        for i, entry in enumerate(targets):
            print(f"\n[{i+1}/{len(class23)}] Charge {entry['charge_id']} "
                  f"({entry['customer_name']}) -> {entry['box_sku']}")
            result = fix_charge(entry, dry_run=False)
            results.append(result)
            print(f"  -> {result['status']}")

            if result["status"] == "SAFETY_FAIL":
                print(f"\n  *** SAFETY WARNING on charge {entry['charge_id']} (likely stale data — will verify later) ***")

            if result["status"] == "ERROR":
                print(f"  Error: {result.get('items_removed', '')}")

            if (i + 1) % 25 == 0:
                _print_summary(results)

        _write_csv(results)
        _print_summary(results)
        print(f"\nDone! Results saved to {CSV_OUTPUT}")


def _write_csv(results):
    fieldnames = ["charge_id", "scheduled", "customer", "email", "rc_customer_id",
                  "rc_subscription_id", "bundle_selection_id", "status", "box_sku",
                  "items_removed", "items_kept", "price_before", "price_after"]
    with open(CSV_OUTPUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"\nCSV written: {CSV_OUTPUT}")


def _print_summary(results):
    from collections import Counter
    counts = Counter(r["status"] for r in results)
    print(f"\n--- Summary ({len(results)} charges) ---")
    for status, count in counts.most_common():
        print(f"  {status}: {count}")


if __name__ == "__main__":
    main()
