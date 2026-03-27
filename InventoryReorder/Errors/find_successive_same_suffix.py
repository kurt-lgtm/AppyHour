"""Find customers with successive curation charges using the same suffix.

When Recharge rotates curations monthly, the box SKU suffix should change
(e.g., MONG -> MDT -> OWC). If two consecutive charges have the same suffix,
it may indicate the rotation failed (like the Feb 24 bulk batch issue).

Exemptions:
- Customers with 2+ active subscriptions (legitimately have same suffix
  appearing on multiple charges, e.g. rockyandrich1@gmail.com)
- BYO boxes (customer-chosen, no rotation)
- Monthly boxes (AHB-MED/LGE/CMED — not curated per-customer)
- One-time / specialty boxes (AHB-X*)
"""
import requests, json, time, csv, re
from collections import defaultdict

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

RC_TOKEN = settings["recharge_api_token"]
RC_HEADERS = {
    "X-Recharge-Access-Token": RC_TOKEN,
    "Content-Type": "application/json",
    "X-Recharge-Version": "2021-11",
}


def extract_curation_suffix(box_sku):
    """Extract curation suffix from box SKU.

    AHB-MCUST-MONG -> MONG
    AHB-LCUST-CORS-MDT -> MDT  (curation is always the LAST segment)
    AHB-MED -> None (monthly, not curated per-customer)
    """
    if not box_sku:
        return None
    box_sku = box_sku.strip().upper()
    if not box_sku.startswith(("AHB-MCUST", "AHB-LCUST")):
        return None
    parts = box_sku.split("-")
    if len(parts) < 3:
        return None
    return parts[-1]


def fetch_charges(status, min_date=None):
    """Fetch charges with given status using cursor pagination.

    Args:
        status: 'queued' or 'success'
        min_date: Optional minimum scheduled_at date (YYYY-MM-DD) to limit history
    """
    print(f"Fetching {status} charges" + (f" (since {min_date})" if min_date else "") + "...", flush=True)
    charges = []
    cursor = None
    while True:
        if cursor:
            params = {"cursor": cursor, "limit": 250}
        else:
            params = {"status": status, "limit": 250, "sort_by": "id-asc"}
            if min_date:
                params["scheduled_at_min"] = min_date
        for attempt in range(3):
            try:
                resp = requests.get("https://api.rechargeapps.com/charges",
                                    headers=RC_HEADERS, params=params, timeout=60)
                break
            except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout):
                wait = 10 * (attempt + 1)
                print(f"  Connection error, retrying in {wait}s...", flush=True)
                time.sleep(wait)
        else:
            print("  Failed after 3 retries, stopping.", flush=True)
            break
        time.sleep(1)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "5"))
            print(f"  Rate limited, waiting {retry_after}s...", flush=True)
            time.sleep(retry_after)
            continue
        if resp.status_code != 200:
            print(f"  Error {resp.status_code}: {resp.text[:200]}", flush=True)
            break
        data = resp.json()
        batch = data.get("charges", [])
        if not batch:
            break
        charges.extend(batch)
        cursor = data.get("next_cursor")
        print(f"  {len(charges)} charges...", flush=True)
        if not cursor:
            break
    return charges


def get_active_subscription_count(customer_id):
    """Count active subscriptions for a customer (to detect multi-sub customers)."""
    resp = requests.get("https://api.rechargeapps.com/subscriptions",
                        headers=RC_HEADERS,
                        params={"customer_id": customer_id, "status": "active"},
                        timeout=30)
    time.sleep(0.3)
    if resp.status_code != 200:
        return 0
    subs = resp.json().get("subscriptions", [])
    # Count only box subscriptions (AHB-MCUST/LCUST)
    box_subs = [s for s in subs
                if (s.get("sku") or "").startswith(("AHB-MCUST", "AHB-LCUST"))]
    return len(box_subs)


# --- Fetch charges ---
# Get recent successful charges (last 4 months) and upcoming queued charges
# to see the full rotation history without fetching entire history
from datetime import datetime, timedelta
cutoff_date = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")

queued = fetch_charges("queued")
success = fetch_charges("success", min_date=cutoff_date)
all_charges = queued + success
print(f"\nTotal charges: {len(all_charges)} ({len(success)} success + {len(queued)} queued)\n", flush=True)

# --- Group charges by customer ---
# Key: email -> list of (scheduled_date, curation_suffix, charge_id, box_sku)
customer_charges = defaultdict(list)

for c in all_charges:
    # v2021-11: email and customer_id are nested under c["customer"]
    cust_obj = c.get("customer") or {}
    email = (cust_obj.get("email") or "").strip().lower()
    if not email:
        continue

    scheduled = (c.get("scheduled_at") or "")[:10]
    if not scheduled:
        continue

    customer_id = str(cust_obj.get("id", "") or "")
    charge_id = c["id"]
    line_items = c.get("line_items", [])

    # Find box SKU in line items
    box_sku = None
    for li in line_items:
        sku = (li.get("sku") or "").strip()
        if sku.startswith(("AHB-MCUST", "AHB-LCUST")):
            box_sku = sku
            break

    suffix = extract_curation_suffix(box_sku)
    # Skip non-rotating tracks: BYO (customer-chosen), NMS/SS (fixed tracks)
    if suffix and suffix not in ("BYO", "NMS", "SS"):
        # Get customer name from billing address
        ba = c.get("billing_address") or {}
        name = f"{ba.get('first_name', '')} {ba.get('last_name', '')}".strip()

        customer_charges[email].append({
            "date": scheduled,
            "suffix": suffix,
            "charge_id": charge_id,
            "box_sku": box_sku,
            "customer_id": customer_id,
            "name": name,
            "status": c.get("status", ""),
        })

print(f"Customers with curated box charges: {len(customer_charges)}", flush=True)

# --- Find successive same-suffix charges ---
flagged = []

for email, charges_list in customer_charges.items():
    # Sort by date
    charges_list.sort(key=lambda x: x["date"])

    # Deduplicate by date (same scheduled date = same charge cycle)
    seen_dates = set()
    unique = []
    for ch in charges_list:
        if ch["date"] not in seen_dates:
            seen_dates.add(ch["date"])
            unique.append(ch)

    if len(unique) < 2:
        continue

    # Check consecutive pairs
    for i in range(1, len(unique)):
        prev = unique[i - 1]
        curr = unique[i]

        if prev["suffix"] == curr["suffix"]:
            flagged.append({
                "email": email,
                "customer_id": curr["customer_id"],
                "name": curr["name"],
                "suffix": curr["suffix"],
                "prev_date": prev["date"],
                "prev_charge_id": str(prev["charge_id"]),
                "prev_status": prev["status"],
                "curr_date": curr["date"],
                "curr_charge_id": str(curr["charge_id"]),
                "curr_status": curr["status"],
                "box_sku": curr["box_sku"],
            })

print(f"\nRaw flags (successive same suffix): {len(flagged)}")
print(f"Unique customers flagged: {len(set(f['email'] for f in flagged))}")

# --- Filter out multi-subscription customers ---
print("\nChecking for multi-subscription exemptions...")
multi_sub_cache = {}
exempt = []
kept = []

for f in flagged:
    cid = f["customer_id"]
    if cid in multi_sub_cache:
        count = multi_sub_cache[cid]
    else:
        count = get_active_subscription_count(cid)
        multi_sub_cache[cid] = count

    if count >= 2:
        exempt.append(f)
    else:
        kept.append(f)

print(f"Exempt (2+ box subscriptions): {len(exempt)} flags across {len(set(e['email'] for e in exempt))} customers")
if exempt:
    print("  Exempt customers:")
    for email in sorted(set(e["email"] for e in exempt)):
        count = multi_sub_cache.get(next(e["customer_id"] for e in exempt if e["email"] == email), "?")
        print(f"    {email} ({count} box subs)")

print(f"\nFinal flags: {len(kept)}")

# --- Write results ---
outfile = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\Errors\successive-same-suffix.csv"
fieldnames = ["email", "name", "customer_id", "suffix", "prev_date", "prev_charge_id",
              "prev_status", "curr_date", "curr_charge_id", "curr_status", "box_sku"]
with open(outfile, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(kept)

print(f"Wrote {len(kept)} rows to {outfile}")

# Also copy to Downloads
import shutil
dl_path = r"C:\Users\Work\Downloads\successive-same-suffix.csv"
shutil.copy2(outfile, dl_path)
print(f"Copied to {dl_path}")

# --- Summary ---
print("\n=== FLAGGED (same suffix on consecutive charges) ===")
for r in kept:
    print(f"  {r['name']} ({r['email']})")
    print(f"    Suffix: {r['suffix']} | {r['prev_date']} ({r['prev_status']}) -> {r['curr_date']} ({r['curr_status']})")
    print(f"    Charges: {r['prev_charge_id']} -> {r['curr_charge_id']} | Box: {r['box_sku']}")
