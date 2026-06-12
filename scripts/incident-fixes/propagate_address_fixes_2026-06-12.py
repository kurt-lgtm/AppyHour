"""Phase 2 of invalid-address fix (Kurt approved 2026-06-12): propagate the 12 order-level
address fixes to (a) Shopify CUSTOMER default address and (b) RECHARGE subscription address —
otherwise next month's renewal recreates the broken address.

Match safety: only update a customer/Recharge address whose CURRENT address1 matches the OLD
broken value (we never clobber an address the customer already fixed themselves).
Recharge: v2021-11, timeout=30 (recharge-api skill rules). Dry-run default; --apply writes.
"""
import sys
import time

sys.path.insert(0, r"C:\Users\Work\Claude Projects\AppyHour\AppyHourMCP")
sys.path.insert(0, r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder")
import requests
from utils import get_shopify_auth
from inventory_reorder import load_settings as load_inv_settings

# email -> {old_a1 (match guard), new_a1, new_a2 (None=keep), city (None=keep)}
FIXES = [
    {"who": "Rousselle", "email": "cindy@adcreativeservices.com", "old_a1": "3693", "a1": "3693 Richie Dr", "a2": ""},
    {"who": "Mou/Kayafas", "email": "guskayafas@yahoo.com", "old_a1": "2307", "a1": "2307 Chapline St", "a2": ""},
    {"who": "Burns", "email": "justsusieb@yahoo.com", "old_a1": "1324", "a1": "1324 Maple Meadows Dr NE", "a2": ""},
    {"who": "Braum", "email": "karolynbraum@gmail.com", "old_a1": "4350", "a1": "4350 Squirrel Bend", "a2": ""},
    {"who": "Gonzales", "email": "jaime.gonzales01@gmail.com", "old_a1": "15906", "a1": "15906 Hachita Blanco", "a2": ""},
    {"who": "Lee", "email": "hlee620@yahoo.com", "old_a1": "201 Newton Raod", "a1": "201 Newton Road", "a2": None},
    {"who": "hargus", "email": "fsptphil.hargus@gmail.com", "old_a1": "122 brentwood hieghts", "a1": "122 Brentwood Heights", "a2": None},
    {"who": "Tennant", "email": "getset29@yahoo.com", "old_a1": "121Mill Lane", "a1": "121 Mill Lane", "a2": None},
    {"who": "Handschuh", "email": "joann.handschuh@gmail.com", "old_a1": "178 Champions X-ing", "a1": "178 Champions Crossing", "a2": None},
    {"who": "Koster", "email": "stumpfd418@yahoo.com", "old_a1": "71 -61 159th Street", "a1": "71-61 159th St", "a2": None},
    {"who": "Bartosek", "email": "cindybartosek@gmail.com", "old_a1": "3200 S Ocean Blvd, Palm Beach, FL, USA", "a1": "3200 S Ocean Blvd", "a2": "C401"},
    {"who": "Judge", "email": "ios.hex@gmail.com", "old_a1": "204 Lakehurst  Camp 195 ", "a1": "204 Lakehurst Camp 195", "a2": None},
]


def fix_shopify_customer(base, hdr, fx, apply):
    r = requests.get(f"{base}/customers/search.json", headers=hdr,
                     params={"query": f"email:{fx['email']}"}, timeout=30)
    r.raise_for_status()
    custs = r.json().get("customers", [])
    if not custs:
        return "no-customer"
    cid = custs[0]["id"]
    r = requests.get(f"{base}/customers/{cid}/addresses.json", headers=hdr, timeout=30)
    r.raise_for_status()
    done = []
    for ad in r.json().get("addresses", []):
        cur = (ad.get("address1") or "").strip()
        if cur == fx["old_a1"].strip() or (cur.split()[0] if cur else "") == fx["old_a1"]:
            body = {"address": {"address1": fx["a1"]}}
            if fx["a2"] is not None:
                body["address"]["address2"] = fx["a2"]
            if apply:
                pr = requests.put(f"{base}/customers/{cid}/addresses/{ad['id']}.json",
                                  headers=hdr, json=body, timeout=30)
                pr.raise_for_status()
            done.append(ad["id"])
    return f"updated {len(done)} addr" if done else "no-match (already fixed?)"


def fix_recharge(token, fx, apply):
    h = {"X-Recharge-Access-Token": token, "X-Recharge-Version": "2021-11"}
    r = requests.get("https://api.rechargeapps.com/customers",
                     headers=h, params={"email": fx["email"]}, timeout=30)
    r.raise_for_status()
    custs = r.json().get("customers", [])
    if not custs:
        return "no-rc-customer"
    rcid = custs[0]["id"]
    r = requests.get("https://api.rechargeapps.com/addresses",
                     headers=h, params={"customer_id": rcid}, timeout=30)
    r.raise_for_status()
    done = []
    for ad in r.json().get("addresses", []):
        cur = (ad.get("address1") or "").strip()
        if cur == fx["old_a1"].strip() or (cur.split()[0] if cur else "") == fx["old_a1"]:
            body = {"address1": fx["a1"]}
            if fx["a2"] is not None:
                body["address2"] = fx["a2"]
            if apply:
                pr = requests.put(f"https://api.rechargeapps.com/addresses/{ad['id']}",
                                  headers=h, json=body, timeout=30)
                pr.raise_for_status()
            done.append(ad["id"])
        time.sleep(0.2)
    return f"updated {len(done)} addr" if done else "no-match (already fixed?)"


def main():
    apply = "--apply" in sys.argv
    base, hdr = get_shopify_auth()
    token = load_inv_settings().get("recharge_api_token", "")
    if not token:
        print("ERROR: no recharge_api_token"); return 1
    print(f"mode: {'APPLY' if apply else 'DRY-RUN'}  customers={len(FIXES)}")
    for fx in FIXES:
        try:
            s = fix_shopify_customer(base, hdr, fx, apply)
        except Exception as e:  # noqa: BLE001
            s = f"SHOPIFY-ERR {e}"
        try:
            rc = fix_recharge(token, fx, apply)
        except Exception as e:  # noqa: BLE001
            rc = f"RC-ERR {e}"
        print(f"  {fx['who']:<14} shopify: {s:<28} recharge: {rc}")
        time.sleep(0.5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
