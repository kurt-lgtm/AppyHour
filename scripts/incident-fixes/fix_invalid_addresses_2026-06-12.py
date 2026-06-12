"""Fix invalid_address orders pre-_SHIP_2026-06-15 (Kurt approved 2026-06-12).

Triage: _outputs/reports/2026-06-12-invalid-address-triage.md
- FIXES: 12 orders (split-merge / typo / format). Bare numbers in address2 KEPT (Kurt:
  "sometimes they're not apts").
- UNTAG-ONLY: rural false positives + bare-number cases -> remove invalid_address so they
  route normally. Martinez #150862 EXCLUDED (military-area check first, stays _HOLD+tagged).

GraphQL orderUpdate(shippingAddress) + tagsRemove. Dry-run default; --apply to write.
"""
import sys
import json
import time

sys.path.insert(0, r"C:\Users\Work\Claude Projects\AppyHour\AppyHourMCP")
import requests
from utils import get_shopify_auth

# order_gid -> {a1, a2, city} (None = leave field unchanged)
FIXES = {
    # A: split number/street merges
    "gid://shopify/Order/7154830016792": {"name": "#149392 Rousselle", "a1": "3693 Richie Dr", "a2": ""},
    "gid://shopify/Order/7156549779736": {"name": "#149640 Mou", "a1": "2307 Chapline St", "a2": ""},
    "gid://shopify/Order/7156552433944": {"name": "#149723 Burns", "a1": "1324 Maple Meadows Dr NE", "a2": ""},
    "gid://shopify/Order/7160330518808": {"name": "#150350 Braum", "a1": "4350 Squirrel Bend", "a2": ""},
    "gid://shopify/Order/7164528165144": {"name": "#151354 Gonzales", "a1": "15906 Hachita Blanco", "a2": ""},
    # B: typos / spacing
    "gid://shopify/Order/7156555448600": {"name": "#149806 Lee", "a1": "201 Newton Road"},
    "gid://shopify/Order/7162402996504": {"name": "#150643 hargus", "a1": "122 Brentwood Heights"},
    "gid://shopify/Order/7152925540632": {"name": "#149220 Tennant", "a1": "121 Mill Lane"},
    "gid://shopify/Order/7160331534616": {"name": "#150377 Handschuh", "a1": "178 Champions Crossing"},
    "gid://shopify/Order/7140678992152": {"name": "#148174 Koster (_HOLD stays)", "a1": "71-61 159th St"},
    # C: format cleanup (bare unit numbers KEPT bare)
    "gid://shopify/Order/7164518727960": {"name": "#151117 Bartosek", "a1": "3200 S Ocean Blvd", "a2": "C401"},
    "gid://shopify/Order/7162413777176": {"name": "#150933 Judge", "a1": "204 Lakehurst Camp 195", "city": "Isle La Motte"},
}

# untag-only: rural false positives + bare-number-fine cases (D + Sollitto + Shaffer)
UNTAG_ONLY = [
    "gid://shopify/Order/7156595687704",  # 150004 Miller US-6
    "gid://shopify/Order/7162404700440",  # 150693 Liles US-380
    "gid://shopify/Order/7158327935256",  # 150160 Scheid MN-65 Trlr 87
    "gid://shopify/Order/7156553023768",  # 149730 Frederickson Curly Horse Ranch
    "gid://shopify/Order/7156559610136",  # 149906 Fisanick Lane
    "gid://shopify/Order/7159763861784",  # 150273 Blankenship S Haystack
    "gid://shopify/Order/7156551352600",  # 149685 Otis Route 195 Lot B10
    "gid://shopify/Order/7156563149080",  # 150002 Collison Batchelor Ridge
    "gid://shopify/Order/7156557381912",  # 149850 Maidman E Reed Ave
    "gid://shopify/Order/7159958929688",  # 150293 Bond Castlepines
    "gid://shopify/Order/7163776925976",  # 151000 Calloway Pinnacle Loop
    "gid://shopify/Order/7160646664472",  # 150512 Phipps Klein Rd
    "gid://shopify/Order/7160334221592",  # 150449 Bender Maple Ave
    "gid://shopify/Order/7162413023512",  # 150915 Cooper Madison St
    "gid://shopify/Order/7162403225880",  # 150647 Cetti Kisiwa Village
    "gid://shopify/Order/7159996350744",  # 150298 Ogle W Angel St
    "gid://shopify/Order/7152332210456",  # 149174 Allen Highland Ave
    "gid://shopify/Order/7158327673112",  # 150152 Paranich Riverside Dr
    "gid://shopify/Order/7150845231384",  # 148737 Giem Woodland Trail
    "gid://shopify/Order/7156023918872",  # 149552 Davis S Market St
    "gid://shopify/Order/7156548567320",  # 149611 Flores Blair Dr Unit B
    "gid://shopify/Order/7164527804696",  # 151344 Barta Camaro St
    "gid://shopify/Order/7164561359128",  # 151475 Holeyfield Basel Dr #5232
    "gid://shopify/Order/7153485087000",  # 149242 Shaffer Caliente Lane 442 (bare number kept)
    "gid://shopify/Order/7156549288216",  # 149644 Sollitto Lane Ave + bare 402 (kept)
]

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
    print(f"mode: {'APPLY' if apply else 'DRY-RUN'}  fixes={len(FIXES)} untag_only={len(UNTAG_ONLY)}")
    ok = fail = 0
    for gid, fx in FIXES.items():
        addr = {}
        if fx.get("a1") is not None: addr["address1"] = fx["a1"]
        if "a2" in fx: addr["address2"] = fx["a2"]
        if fx.get("city"): addr["city"] = fx["city"]
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
    for gid in UNTAG_ONLY:
        print(f"[untag] {gid.rsplit('/',1)[1]}")
        if not apply:
            continue
        try:
            d = gql(base, hdr, TAGS_REMOVE, {"id": gid, "tags": ["invalid_address"]})
            if d["tagsRemove"]["userErrors"]:
                print(f"   ERROR: {d['tagsRemove']['userErrors']}"); fail += 1
            else:
                ok += 1
            time.sleep(0.3)
        except Exception as e:  # noqa: BLE001
            print(f"   EXCEPTION: {e}"); fail += 1
    print(f"\ndone: ok={ok} fail={fail}" if apply else "\nDRY-RUN complete (no writes)")


if __name__ == "__main__":
    main()
