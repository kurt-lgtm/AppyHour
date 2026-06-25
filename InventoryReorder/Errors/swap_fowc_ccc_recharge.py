# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///
"""Swap CH-FOWC -> CH-CCC in ALL Recharge queued charges (bundle_selections).

Usage:
  python swap_fowc_ccc_recharge.py            # dry-run
  python swap_fowc_ccc_recharge.py --commit   # apply
"""
import requests, json, sys, time
from datetime import datetime, timedelta

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

RC_TOKEN_READ = settings["recharge_api_token"]
RC_TOKEN_WRITE = settings["recharge_api_token_write"]  # scrubbed 2026-06-14: load from local settings.json (gitignored); rotate in Recharge admin
BASE_URL = "https://api.rechargeapps.com"

def H(write=False):
    return {
        "X-Recharge-Access-Token": RC_TOKEN_WRITE if write else RC_TOKEN_READ,
        "Content-Type": "application/json",
        "X-Recharge-Version": "2021-11",
    }

COMMIT = "--commit" in sys.argv
OLD_SKU = "CH-FOWC"
NEW_SKU = "CH-CCC"

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

def lookup_sku_ids(sku):
    """Return (lowest_price_product_id, lowest_price_variant_id, set(all_variant_ids))."""
    q = f'{{ productVariants(first:20, query:"sku:{sku}") {{ edges {{ node {{ id sku price product {{ id }} }} }} }} }}'
    r = requests.post(GQL, headers=SH_H, json={"query": q}, timeout=30)
    r.raise_for_status()
    cands = [e["node"] for e in r.json()["data"]["productVariants"]["edges"] if e["node"]["sku"]==sku]
    cands.sort(key=lambda n: float(n["price"]))
    if not cands: raise Exception(f"{sku} not found")
    n = cands[0]
    all_vids = {n2["id"].rsplit("/",1)[-1] for n2 in cands}
    return n["product"]["id"].rsplit("/",1)[-1], n["id"].rsplit("/",1)[-1], all_vids

def all_queued_charges():
    out = []
    cursor = None
    pages = 0
    while True:
        if cursor:
            params = {"cursor": cursor, "limit": 250}
        else:
            params = {"status": "queued", "limit": 250, "sort_by": "id-asc"}
        d = rc_get("/charges", params=params)
        batch = d.get("charges", [])
        out.extend(batch)
        pages += 1
        print(f"  page {pages}: {len(batch)} charges (total {len(out)})")
        cursor = d.get("next_cursor")
        if not cursor: break
    return out

def _vid(li):
    evid = li.get("external_variant_id")
    if isinstance(evid, dict): return str(evid.get("ecommerce","") or "")
    return str(evid or "")

def find_old_sku_charges(charges):
    hits = []
    for c in charges:
        old_li = None
        for li in c.get("line_items", []):
            if (li.get("sku") or "").strip() == OLD_SKU:
                old_li = li; break
        if not old_li: continue
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
            for li2 in c["line_items"]:
                if li2.get("purchase_item_type") == "subscription" and li2.get("purchase_item_id"):
                    sub_id = str(li2["purchase_item_id"]); break
        cust = c.get("customer") or {}
        is_onetime = old_li.get("purchase_item_type") == "onetime"
        hits.append({
            "charge_id": str(c["id"]),
            "scheduled_at": (c.get("scheduled_at") or "")[:10],
            "subscription_id": sub_id,
            "onetime_ids": onetime_ids,
            "old_variant_id": _vid(old_li),
            "email": cust.get("email",""),
            "is_onetime": is_onetime,
            "old_pi_id": str(old_li.get("purchase_item_id") or "") if is_onetime else "",
        })
    return hits

def swap_one(hit, new_pid, new_vid, old_vids, dry_run):
    # Onetime path: PUT /onetimes/{pi_id}
    if hit["is_onetime"]:
        ot_id = hit["old_pi_id"]
        print(f"  charge {hit['charge_id']} ({hit['email']}) sched {hit['scheduled_at']}: ONETIME {ot_id}, {OLD_SKU} -> {NEW_SKU}")
        if dry_run: return True
        rc_put(f"/onetimes/{ot_id}", {
            "external_product_id": {"ecommerce": new_pid},
            "external_variant_id": {"ecommerce": new_vid},
        })
        print("    onetime PUT ok")
        # date-shuffle subscription + onetimes to force regen
        sub_id = hit["subscription_id"]
        if sub_id:
            original = hit["scheduled_at"]
            temp = (datetime.strptime(original,"%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            try:
                rc_put(f"/subscriptions/{sub_id}", {"next_charge_scheduled_at": temp})
                for ot in hit["onetime_ids"]:
                    rc_put(f"/onetimes/{ot}", {"next_charge_scheduled_at": temp})
                rc_put(f"/subscriptions/{sub_id}", {"next_charge_scheduled_at": original})
                for ot in hit["onetime_ids"]:
                    try: rc_put(f"/onetimes/{ot}", {"next_charge_scheduled_at": original})
                    except Exception as e:
                        if "409" in str(e): pass
                        else: raise
                print("    date-shuffle ok")
            except Exception as e:
                print(f"    date-shuffle warn: {e}")
        return True

    sub_id = hit["subscription_id"]
    if not sub_id:
        print(f"  charge {hit['charge_id']} ({hit['email']}): no subscription_id, skip"); return False
    bs_data = rc_get("/bundle_selections", params={"purchase_item_ids": sub_id})
    upcoming = [s for s in bs_data.get("bundle_selections",[]) if s.get("charge_id") is None]
    if not upcoming:
        print(f"  charge {hit['charge_id']} ({hit['email']}): no upcoming bundle_selection"); return False
    bs = upcoming[0]
    bs_id = bs["id"]
    items = bs.get("items", [])
    new_items = []
    swapped = 0
    for it in items:
        vid = str(it.get("external_variant_id",""))
        cid = str(it.get("collection_id","") or "")
        if vid in old_vids:
            new_items.append({
                "collection_id": cid,
                "collection_source": it.get("collection_source","shopify"),
                "external_product_id": new_pid,
                "external_variant_id": new_vid,
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
    print(f"  charge {hit['charge_id']} ({hit['email']}) sched {hit['scheduled_at']}: bs {bs_id}, {swapped}x {OLD_SKU} -> {NEW_SKU}")
    if dry_run: return True
    rc_put(f"/bundle_selections/{bs_id}", {"items": new_items})
    print("    bundle_selection PUT ok")
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
    print(f"== Swap {OLD_SKU} -> {NEW_SKU} in ALL queued Recharge charges [{mode}] ==\n")
    new_pid, new_vid, _ = lookup_sku_ids(NEW_SKU)
    _, _, old_vids = lookup_sku_ids(OLD_SKU)
    print(f"{NEW_SKU}: product_id={new_pid} variant_id={new_vid}")
    print(f"{OLD_SKU} all variant_ids: {sorted(old_vids)}\n")
    print("Fetching all queued charges...")
    charges = all_queued_charges()
    print(f"\nTotal queued charges: {len(charges)}")
    hits = find_old_sku_charges(charges)
    print(f"Charges containing {OLD_SKU}: {len(hits)}\n")
    if not hits: return
    ok = []; failed = []
    for h in hits:
        try:
            swap_one(h, new_pid, new_vid, old_vids, dry_run=not COMMIT)
            ok.append(h)
        except Exception as e:
            msg = str(e)[:200]
            print(f"    FAILED: {msg}")
            failed.append({"charge": h["charge_id"], "email": h["email"], "kind": "onetime" if h["is_onetime"] else "bundle", "error": msg})
    print(f"\n=== {mode} ===")
    print(f"OK: {len(ok)}  Failed: {len(failed)}")
    for f in failed:
        print(f"  {f['charge']} ({f['email']}) [{f['kind']}]: {f['error']}")

if __name__ == "__main__":
    main()
