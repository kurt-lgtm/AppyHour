"""Canonical order loader — GraphQL, Dan's shape (RUN_2026-08-25 fetch_week.py).

🔴 Use THIS, not a REST pull, for anything that ports his logic. His constants are
computed against this shape and only this shape:
  * `title` is the bare product title; REST `name` appends the variant, so a
    WRAPPER_ADD lookup keyed on the product title silently never fires.
  * `discountedUnitPriceSet` nets LINE-level discounts only — an order-level code
    ("AppyHour Credit") still reads as paid, which is what we want.
  * `currentQuantity` + `quantity` both present, so removed lines stay visible.
Porting his checks onto a REST shape produced 19 flags with zero overlap against his 3.
"""
from __future__ import annotations

import json
import os
import sys
import time

import requests

API = "2025-01"
Q = """query($q:String!){orders(first:60, query:$q){edges{node{
  id name createdAt cancelledAt tags note displayFulfillmentStatus
  customer{id email tags}
  shippingAddress{address1 city province provinceCode zip country}
  currentTotalPriceSet{shopMoney{amount}}
  discountCodes
  lineItems(first:200){edges{node{sku title quantity currentQuantity
    variant{id}
    originalUnitPriceSet{shopMoney{amount}}
    discountedUnitPriceSet{shopMoney{amount}}}}}}}}}"""
# 🔴 variant{id} is not optional. A Simple Bundles parent can carry NO SKU at all
# (#178568 'Ultimate Add-on Package: Summer Cookout', $28, sku=None). Every check keys on
# SKU, so a null-SKU line counts 0 children and matches no rule -- it is invisible, and
# its components never reach the pick sheet. The variant id is the only handle left for
# reading that line's bundled_variants metafield.


STORE = "504ac4"


def admin_url(node) -> str:
    """-> the admin URL for an order NODE (needs the node's `id`, not its name).

    🔴 The order NUMBER and the admin id are unrelated numbers: #181803 lives at
    /orders/7362504229144. Never build this from the number -- an invented id 404s on a
    real order, which is worse than no link (Kurt 2026-09-08, [[never-fabricate]]).
    Every node from Q already carries `id`; read it, do not guess it.
    """
    gid = node.get("id") if isinstance(node, dict) else node
    if not gid:
        raise ValueError("admin_url needs the order's `id`; the order number is not an id")
    return (f"https://admin.shopify.com/store/{STORE}/orders/"
            f"{str(gid).rsplit('/', 1)[-1]}?link_source=search")


def admin_link(node) -> str:
    """-> `[#181803](https://admin.shopify.com/...)` -- the number IS the link text."""
    return f"[{node.get('name')}]({admin_url(node)})"


def _auth():
    for p in (r"C:\Users\Work\Claude Projects\AppyHour\AppyHourMCP",
              r"C:\Users\Work\Claude Projects\AppyHour\GelPackCalculator"):
        if p not in sys.path:
            sys.path.insert(0, p)
    from utils import get_shopify_auth
    base, hdr = get_shopify_auth()
    dom = base.split("//", 1)[1].split("/", 1)[0]
    return f"https://{dom}/admin/api/{API}/graphql.json", hdr


def _post(url, hdr, q):
    j = {}
    for a in range(8):
        try:
            r = requests.post(url, headers={**hdr, "Content-Type": "application/json"},
                              json={"query": Q, "variables": {"q": q}}, timeout=60)
            j = r.json()
        except Exception as e:                                  # noqa: BLE001
            j = {"err": str(e)}
        if j.get("data") and j["data"].get("orders"):
            return j
        time.sleep(2 + 2 * a)
    raise SystemExit(f"shopify query failed: {str(j)[:400]}")


def fetch_by_name(order_ids, cache=None, verbose=True):
    """-> {order_id_without_hash: node}. `order_ids` are bare numbers from the sheet."""
    if cache and os.path.exists(cache):
        d = json.load(open(cache, encoding="utf8"))
        if verbose:
            print(f"  cache hit: {len(d)} orders")
        return d
    url, hdr = _auth()
    ids = [str(x).strip().lstrip("#") for x in order_ids]
    out, B = {}, 20
    for i in range(0, len(ids), B):
        chunk = ids[i:i + B]
        j = _post(url, hdr, " OR ".join(f"name:{n}" for n in chunk))
        for e in j["data"]["orders"]["edges"]:
            out[e["node"]["name"].lstrip("#")] = e["node"]
        if verbose and (i // B) % 20 == 0:
            print(f"  {i + len(chunk)}/{len(ids)} requested, {len(out)} found", flush=True)
        time.sleep(0.35)
    missing = [n for n in ids if n not in out]
    if verbose:
        print(f"  DONE {len(out)}/{len(ids)} orders, {len(missing)} not found")
    if cache:
        json.dump(out, open(cache, "w", encoding="utf8"))
    return out


def line_items(node):
    return [e["node"] for e in node["lineItems"]["edges"]]


def net(li):
    """Discounted UNIT price x currentQuantity. Line-level discounts only."""
    p = float((li.get("discountedUnitPriceSet") or {}).get("shopMoney", {}).get("amount") or 0)
    return round(p * (li.get("currentQuantity") or 0), 2)


PAGED = Q.replace("orders(first:60, query:$q)",
                  "orders(first:60, query:$q, after:$after)").replace(
                  "query($q:String!)", "query($q:String!,$after:String)").replace(
                  "{edges{node{", "{pageInfo{hasNextPage endCursor} edges{node{")


def fetch_by_tag(tag, cache=None, verbose=True):
    """-> {order_id: node} for every order carrying `tag`, in the same shape.

    🔴 THE SHEET IS NOT THE COHORT. `fetch_by_name(list(sheet))` makes the sheet drive the
    fetch, so every order-side check silently required a sheet that no check actually
    reads -- the counts, slot checks, tag drift, Fixed_Route and cracker slots all read
    Shopify only. On RMFG_20260904 that delay meant 10 count exceptions, 25 empty CEX-CR
    slots, 6 bare CEX-EC and 2 missing ship tags sat uncomputed for an hour with the data
    already cached (Kurt 2026-09-04: "you could have done it all that shit without the
    sheet").

    It is also the only way to see DRIFT-IN: an order tagged into the cohort but absent
    from the sheet is invisible to a sheet-driven fetch by construction.
    """
    if cache and os.path.exists(cache):
        d = json.load(open(cache, encoding="utf8"))
        if verbose:
            print(f"  cache hit: {len(d)} orders")
        return d
    url, hdr = _auth()
    out, after, page = {}, None, 0
    while True:
        page += 1
        j = {}
        for a in range(8):
            try:
                r = requests.post(url, headers={**hdr, "Content-Type": "application/json"},
                                  json={"query": PAGED,
                                        "variables": {"q": f"tag:{tag}", "after": after}},
                                  timeout=60)
                j = r.json()
            except Exception as e:                                  # noqa: BLE001
                j = {"err": str(e)}
            if j.get("data", {}).get("orders"):
                break
            time.sleep(2 + 2 * a)
        else:
            raise SystemExit(f"shopify tag fetch failed: {str(j)[:400]}")
        d = j["data"]["orders"]
        for e in d["edges"]:
            out[e["node"]["name"].lstrip("#")] = e["node"]
        if verbose and page % 10 == 0:
            print(f"    page {page}: {len(out)} orders", flush=True)
        if not d["pageInfo"]["hasNextPage"]:
            break
        after = d["pageInfo"]["endCursor"]
    if verbose:
        print(f"  fetched {len(out)} orders tagged {tag}")
    if cache:
        json.dump(out, open(cache, "w", encoding="utf8"))
    return out
