# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///
"""Swap AC-FRESC -> AC-LFOLIVE in Recharge bundle_selections for 2 specific customers.

Customers:
  sezanne1945@yahoo.com
  tylerbusedesign@gmail.com

Usage:
  python swap_fresc_lfolive_2emails.py            # investigate / dry-run
  python swap_fresc_lfolive_2emails.py --commit   # apply
"""
import requests, json, sys, time
from datetime import datetime, timedelta

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

RC_TOKEN_READ = settings["recharge_api_token"]
RC_TOKEN_WRITE = "sk_2x2_f998f08c853bd9391790b7760b449a60140c37dc0b3da48c4a54f7e0c7e67d10"
BASE_URL = "https://api.rechargeapps.com"

def H(write=False):
    return {
        "X-Recharge-Access-Token": RC_TOKEN_WRITE if write else RC_TOKEN_READ,
        "Content-Type": "application/json",
        "X-Recharge-Version": "2021-11",
    }

COMMIT = "--commit" in sys.argv
EMAILS = ["sezanne1945@yahoo.com"]  # tylerbusedesign: fallback bs, swap post-charge on 5-20
OLD_SKU = "AC-FRESC"
NEW_SKU = "AC-LFOLIVE"

# AC-LFOLIVE variant IDs from Shopify (will look up at runtime if needed)
# Already confirmed via earlier swap MCP: gid://shopify/ProductVariant/51706926727448
LFOLIVE_VARIANT_ID = "51706926727448"
LFOLIVE_PRODUCT_ID = None  # fill in via Shopify lookup

# Shopify creds (for product_id lookup)
STORE = settings["shopify_store_url"]
SHOP_TOKEN = settings["shopify_access_token"]
GQL = f"https://{STORE}.myshopify.com/admin/api/2024-01/graphql.json"
SH_H = {"X-Shopify-Access-Token": SHOP_TOKEN, "Content-Type": "application/json"}

def rc_get(path, params=None, retries=5):
    last = None
    for a in range(retries):
        try:
            r = requests.get(BASE_URL + path, headers=H(False), params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(int(r.headers.get("retry-after","5"))); continue
            r.raise_for_status()
            time.sleep(0.4)
            return r.json()
        except Exception as e:
            last = e; time.sleep(2*(a+1))
    raise last

def rc_put(path, body):
    for a in range(5):
        r = requests.put(BASE_URL + path, headers=H(True), json=body, timeout=30)
        if r.status_code == 429:
            time.sleep(int(r.headers.get("retry-after","5"))); continue
        if r.status_code >= 500:
            time.sleep(1<<a); continue
        if r.status_code == 409:
            raise Exception(f"409 CONFLICT {path}: {r.text}")
        if r.status_code >= 400:
            raise Exception(f"{r.status_code} {path}: {r.text}")
        r.raise_for_status()
        time.sleep(0.3)
        return r.json() if r.text else {}
    raise Exception(f"max retries PUT {path}")

def lookup_lfolive_product_id():
    q = '{ productVariants(first:20, query:"sku:AC-LFOLIVE") { edges { node { id sku price product { id } } } } }'
    r = requests.post(GQL, headers=SH_H, json={"query": q}, timeout=30)
    r.raise_for_status()
    cands = [e["node"] for e in r.json()["data"]["productVariants"]["edges"] if e["node"]["sku"]=="AC-LFOLIVE"]
    cands.sort(key=lambda n: float(n["price"]))  # $0 (bundle variant) first
    if not cands: raise Exception("AC-LFOLIVE not found")
    n = cands[0]
    return n["product"]["id"].rsplit("/",1)[-1], n["id"].rsplit("/",1)[-1]

def find_customer(email):
    d = rc_get("/customers", params={"email": email})
    cs = d.get("customers", [])
    return cs[0] if cs else None

def queued_charges(customer_id):
    out = []
    cursor = None
    while True:
        if cursor:
            params = {"cursor": cursor, "limit": 250}
        else:
            params = {"customer_id": customer_id, "status": "queued", "limit": 250, "sort_by": "id-asc"}
        d = rc_get("/charges", params=params)
        out.extend(d.get("charges", []))
        cursor = d.get("next_cursor")
        if not cursor: break
    return out

def _vid(li):
    evid = li.get("external_variant_id")
    if isinstance(evid, dict): return str(evid.get("ecommerce","") or "")
    return str(evid or "")

def find_fresc_charges(charges):
    hits = []
    for c in charges:
        for li in c.get("line_items", []):
            sku = (li.get("sku") or "").strip()
            if sku == OLD_SKU:
                # collect sub id (any purchase_item_id of an AHB- box item)
                sub_id = None
                onetime_ids = []
                for li2 in c["line_items"]:
                    pid = li2.get("purchase_item_id")
                    ptype = li2.get("purchase_item_type","")
                    sku2 = (li2.get("sku") or "").strip()
                    if pid and sku2.startswith("AHB-"):
                        sub_id = str(pid)
                    if ptype == "onetime" and pid:
                        onetime_ids.append(str(pid))
                if not sub_id:
                    # fallback: first sub purchase_item_id
                    for li2 in c["line_items"]:
                        if li2.get("purchase_item_type") == "subscription" and li2.get("purchase_item_id"):
                            sub_id = str(li2["purchase_item_id"]); break
                hits.append({
                    "charge_id": str(c["id"]),
                    "scheduled_at": (c.get("scheduled_at") or "")[:10],
                    "subscription_id": sub_id,
                    "onetime_ids": onetime_ids,
                    "fresc_variant_id": _vid(li),
                })
                break
    return hits

def swap_one(hit, lfolive_pid, lfolive_vid, dry_run):
    sub_id = hit["subscription_id"]
    if not sub_id:
        print(f"  charge {hit['charge_id']}: no subscription_id, skip"); return False
    bs_data = rc_get("/bundle_selections", params={"purchase_item_ids": sub_id})
    upcoming = [s for s in bs_data.get("bundle_selections",[]) if s.get("charge_id") is None]
    if not upcoming:
        print(f"  charge {hit['charge_id']}: no upcoming bundle_selection"); return False
    bs = upcoming[0]
    bs_id = bs["id"]
    items = bs.get("items", [])
    new_items = []
    swapped = 0
    for it in items:
        vid = str(it.get("external_variant_id",""))
        cid = str(it.get("collection_id","") or "")
        if vid == hit["fresc_variant_id"]:
            new_items.append({
                "collection_id": cid,
                "collection_source": it.get("collection_source","shopify"),
                "external_product_id": lfolive_pid,
                "external_variant_id": lfolive_vid,
                "quantity": it.get("quantity",1),
            })
            swapped += it.get("quantity",1)
        else:
            new_items.append({
                "collection_id": cid,
                "collection_source": it.get("collection_source","shopify"),
                "external_product_id": it.get("external_product_id",""),
                "external_variant_id": it.get("external_variant_id",""),
                "quantity": it.get("quantity",1),
            })
    print(f"  charge {hit['charge_id']} sched {hit['scheduled_at']}: bs {bs_id}, {swapped}x AC-FRESC -> AC-LFOLIVE")
    if dry_run:
        return True
    rc_put(f"/bundle_selections/{bs_id}", {"items": new_items})
    print("    bundle_selection PUT ok")
    # date-shuffle
    original = hit["scheduled_at"]
    temp = (datetime.strptime(original,"%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    rc_put(f"/subscriptions/{sub_id}", {"next_charge_scheduled_at": temp})
    for ot in hit["onetime_ids"]:
        rc_put(f"/onetimes/{ot}", {"next_charge_scheduled_at": temp})
    try:
        rc_put(f"/subscriptions/{sub_id}", {"next_charge_scheduled_at": original})
    except Exception as e:
        if "409" in str(e):
            print(f"    409 moving back, leaving at {temp}")
            original = temp
        else: raise
    for ot in hit["onetime_ids"]:
        try: rc_put(f"/onetimes/{ot}", {"next_charge_scheduled_at": original})
        except Exception as e:
            if "409" in str(e): print(f"    409 onetime {ot}, leaving at {temp}")
            else: raise
    print("    date-shuffle ok")
    return True

def main():
    mode = "COMMIT" if COMMIT else "DRY-RUN"
    print(f"== Swap {OLD_SKU} -> {NEW_SKU} in Recharge [{mode}] ==\n")
    pid, vid = lookup_lfolive_product_id()
    print(f"AC-LFOLIVE: product_id={pid} variant_id={vid}\n")
    for email in EMAILS:
        print(f"--- {email} ---")
        cust = find_customer(email)
        if not cust:
            print("  NOT FOUND in Recharge"); continue
        cid = cust["id"]
        print(f"  customer_id={cid}")
        charges = queued_charges(cid)
        print(f"  {len(charges)} queued charges")
        hits = find_fresc_charges(charges)
        if not hits:
            print(f"  no AC-FRESC found"); continue
        for h in hits:
            swap_one(h, pid, vid, dry_run=not COMMIT)
        print()

if __name__ == "__main__":
    main()
