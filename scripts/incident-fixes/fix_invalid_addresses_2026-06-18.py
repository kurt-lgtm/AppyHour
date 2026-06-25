"""Fix 4 invalid_address orders pre-_SHIP_2026-06-22 (Kurt approved 2026-06-18).

Triage: _outputs/reports/wrong-address-2026-06-18.md
All 4 are split-merge: street number in address1, street name in address2.
Fix: combine into address1, clear address2, remove invalid_address tag.

GraphQL orderUpdate(shippingAddress) + tagsRemove. Dry-run default; --apply to write.
"""
import sys
import json
import time

sys.path.insert(0, r"C:\Users\Work\Claude Projects\AppyHour\AppyHourMCP")
import requests
from utils import get_shopify_auth

FIXES = {
    "gid://shopify/Order/7206890570008": {"name": "#153028", "a1": "1649 Sunrise Drive", "a2": ""},
    "gid://shopify/Order/7207998521624": {"name": "#153128", "a1": "12418 Carriage Hill Dr", "a2": ""},
    "gid://shopify/Order/7209318580504": {"name": "#153479", "a1": "10029 N Lawn Ave", "a2": ""},
    "gid://shopify/Order/7209321693464": {"name": "#153558", "a1": "3358 Murry Dr", "a2": ""},
}

ORDER_UPDATE = """
mutation($input: OrderInput!) {
  orderUpdate(input: $input) {
    order { id name shippingAddress { address1 address2 city } }
    userErrors { field message }
  }
}"""
TAGS_REMOVE = """
mutation($id: ID!, $tags: [String!]!) {
  tagsRemove(id: $id, tags: $tags) {
    userErrors { field message }
  }
}"""


def gql(base, hdr, query, variables):
    r = requests.post(f"{base}/graphql.json", headers=hdr,
                      json={"query": query, "variables": variables}, timeout=60)
    r.raise_for_status()
    j = r.json()
    if j.get("errors"):
        raise RuntimeError(json.dumps(j["errors"])[:300])
    return j["data"]


def main():
    apply = "--apply" in sys.argv
    base, hdr = get_shopify_auth()
    print(f"mode: {'APPLY' if apply else 'DRY-RUN'}  fixes={len(FIXES)}")
    ok = fail = 0
    for gid, fx in FIXES.items():
        addr = {"address1": fx["a1"], "address2": fx["a2"]}
        print(f"[fix] {fx['name']}: {addr}")
        if not apply:
            continue
        try:
            d = gql(base, hdr, ORDER_UPDATE, {"input": {"id": gid, "shippingAddress": addr}})
            errs = d["orderUpdate"]["userErrors"]
            if errs:
                print(f"   ERROR: {errs}"); fail += 1; continue
            got = d["orderUpdate"]["order"]["shippingAddress"]
            print(f"   ok -> {got['address1']} | {got.get('address2') or ''} | {got['city']}")
            d2 = gql(base, hdr, TAGS_REMOVE, {"id": gid, "tags": ["invalid_address"]})
            if d2["tagsRemove"]["userErrors"]:
                print(f"   untag ERROR: {d2['tagsRemove']['userErrors']}"); fail += 1; continue
            ok += 1
            time.sleep(0.4)
        except Exception as e:  # noqa: BLE001
            print(f"   EXCEPTION: {e}"); fail += 1
    print(f"\ndone: ok={ok} fail={fail}" if apply else "\nDRY-RUN complete (no writes)")


if __name__ == "__main__":
    main()
