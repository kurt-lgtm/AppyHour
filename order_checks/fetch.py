"""Shopify cohort fetch. Filters in-loop so a big window stays cheap."""
from __future__ import annotations
import json, os, sys, requests

def _auth():
    for p in (r"C:\Users\Work\Claude Projects\AppyHour\AppyHourMCP",
              r"C:\Users\Work\Claude Projects\AppyHour\GelPackCalculator"):
        if p not in sys.path:
            sys.path.insert(0, p)
    from utils import get_shopify_auth
    return get_shopify_auth()

FIELDS = ("id,name,tags,email,customer,line_items,discount_codes,"
          "fulfillment_status,cancelled_at,created_at")


def fetch_cohort(tags_wanted, created_at_min, cache=None, verbose=True):
    """Orders carrying ANY of `tags_wanted`. Cache is a json path (skips the pull)."""
    if cache and os.path.exists(cache):
        rows = json.load(open(cache, encoding="utf8"))
        if verbose: print(f"  cache hit: {len(rows)} orders")
        return {o["name"].lstrip("#"): o for o in rows}
    base, headers = _auth()
    url, page, seen, keep = f"{base}/orders.json", 0, 0, []
    params = {"status": "any", "limit": 250, "created_at_min": created_at_min, "fields": FIELDS}
    want = set(tags_wanted)
    while url:
        page += 1
        r = requests.get(url, headers=headers, params=params if page == 1 else None, timeout=60)
        r.raise_for_status()
        batch = r.json().get("orders", [])
        if not batch:
            break
        seen += len(batch)
        for o in batch:
            if want & {t.strip() for t in (o.get("tags") or "").split(",")}:
                keep.append(o)
        url = r.links.get("next", {}).get("url")
        if verbose and page % 10 == 0:
            print(f"  page {page} seen={seen} kept={len(keep)}", flush=True)
    if verbose: print(f"  fetched {len(keep)} of {seen} scanned")
    if cache:
        json.dump(keep, open(cache, "w", encoding="utf8"))
    return {o["name"].lstrip("#"): o for o in keep}
