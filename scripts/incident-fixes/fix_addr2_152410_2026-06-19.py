"""Fix order #152410 address2 leftover junk (Kurt approved 2026-06-19).

shipping_name_cleaner PO-box rule stripped "PO Box" from address2 but left the
remainder "7024 if by mail". Street is in address1 (110 Wall Street), so
address2 should be empty. Clear it.

GraphQL orderUpdate(shippingAddress). Dry-run default; --apply to write.
"""
import sys
import json

sys.path.insert(0, r"C:\Users\Work\Claude Projects\AppyHour\AppyHourMCP")
import requests
from utils import get_shopify_auth

FIXES = {
    "gid://shopify/Order/7190096347416": {"name": "#152410", "a1": "110 Wall Street", "a2": ""},
}

ORDER_UPDATE = """
mutation($input: OrderInput!) {
  orderUpdate(input: $input) {
    order { id name shippingAddress { address1 address2 city } }
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
    for gid, fx in FIXES.items():
        addr = {"address1": fx["a1"], "address2": fx["a2"]}
        print(f"[fix] {fx['name']}: {addr}")
        if not apply:
            continue
        d = gql(base, hdr, ORDER_UPDATE, {"input": {"id": gid, "shippingAddress": addr}})
        errs = d["orderUpdate"]["userErrors"]
        if errs:
            print(f"   ERROR: {errs}"); continue
        got = d["orderUpdate"]["order"]["shippingAddress"]
        print(f"   ok -> {got['address1']} | a2={got.get('address2') or '(empty)'} | {got['city']}")
    print("\nDRY-RUN complete (no writes)" if not apply else "\ndone")


if __name__ == "__main__":
    main()
