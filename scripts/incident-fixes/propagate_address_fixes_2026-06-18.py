"""Phase 2: propagate 4 order-level address fixes to Shopify customer + Recharge subscription.
Kurt approved 2026-06-18. Order-level already fixed via fix_invalid_addresses_2026-06-18.py.

All 4 are split-merge (address1=bare number, address2=street name). Match guard uses old bare
number so we never clobber an address the customer already self-corrected.
Recharge: v2021-11, timeout=30. Dry-run default; --apply writes.
"""
import sys
import time

sys.path.insert(0, r"C:\Users\Work\Claude Projects\AppyHour\AppyHourMCP")
sys.path.insert(0, r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder")
import requests
from utils import get_shopify_auth
from inventory_reorder import load_settings as load_inv_settings

FIXES = [
    {"who": "Jones",     "email": "jannajones@ymail.com",            "old_a1": "1649",  "a1": "1649 Sunrise Drive",    "a2": ""},
    {"who": "Salinas",   "email": "salinas.alison@gmail.com",        "old_a1": "12418", "a1": "12418 Carriage Hill Dr", "a2": ""},
    {"who": "Formanek",  "email": "akformanek@aol.com",              "old_a1": "10029", "a1": "10029 N Lawn Ave",       "a2": ""},
    {"who": "Eckenstein","email": "ashley.eckenstein44@gmail.com",   "old_a1": "3358",  "a1": "3358 Murry Dr",          "a2": ""},
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
            body = {"address": {"address1": fx["a1"], "address2": fx["a2"]}}
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
            body = {"address1": fx["a1"], "address2": fx["a2"]}
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
        print(f"  {fx['who']:<12} shopify: {s:<28} recharge: {rc}")
        time.sleep(0.5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
