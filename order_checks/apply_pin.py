"""Append a customer's Fixed_Route pin to their live order(s). Dry-run unless --apply.

🔴 APPEND ONLY. ShipRouting/lib/fixed_route.py, Kurt 2026-08-20 verbatim:
   "as long as Fixed_Route tag is in the order, we don't strip it."
Never remove or rewrite an existing _AHB! token - two routing tags on one RMFG row is
ambiguous at induction. The CUSTOMER PROFILE is authoritative; the order follows it.

Why this exists: the "Customer Specific Routing" Shopify Flow only fires on order_created,
so an order that already existed when the pin was set never picks it up.
"""
from __future__ import annotations
import re, sys, requests

ROUTE = re.compile(r"!.*?_AHB!")


def _auth():
    for p in (r"C:\Users\Work\Claude Projects\AppyHour\AppyHourMCP",
              r"C:\Users\Work\Claude Projects\AppyHour\GelPackCalculator"):
        if p not in sys.path:
            sys.path.insert(0, p)
    from utils import get_shopify_auth
    return get_shopify_auth()


def main(argv):
    names = [a.lstrip("#") for a in argv if a.isdigit() or a.lstrip("#").isdigit()]
    APPLY = "--apply" in argv
    base, hdr = _auth()
    for n in names:
        r = requests.get(f"{base}/orders.json", headers=hdr,
                         params={"status": "any", "name": "#" + n, "limit": 5,
                                 "fields": "id,name,tags,customer,email"}, timeout=30)
        r.raise_for_status()
        hits = [o for o in r.json()["orders"] if o["name"].lstrip("#") == n]
        if not hits:
            print(f"#{n}  NOT FOUND"); continue
        o = hits[0]
        # 🔴🔴 NEVER edit Shopify on a Gift Redemption order (Kurt 2026-08-28). Contents
        # are driven from Matrixify; no order edit, tag write or line add belongs here.
        if any(t.strip().lower() == "gift redemption"
               for t in (o.get("tags") or "").split(",")):
            print(f"#{n}  REFUSING - Gift Redemption, Shopify is not editable for gifts")
            continue
        cust = o.get("customer") or {}
        ctags = cust.get("tags") or ""
        pins = ROUTE.findall(ctags)
        cur = [t.strip() for t in (o.get("tags") or "").split(",") if t.strip()]
        print(f"#{n}  {cust.get('first_name','')} {cust.get('last_name','')} <{o.get('email','')}>")
        if "fixed_route" not in ctags.lower():
            print("   SKIP - customer profile has no Fixed_Route"); continue
        if not pins:
            print("   SKIP - Fixed_Route on profile but no !..._AHB! pin to copy"); continue
        if ROUTE.findall(o.get("tags") or ""):
            print(f"   SKIP - order already routed {ROUTE.findall(o.get('tags') or '')} "
                  f"(append-only: never overwrite an existing pin)"); continue
        want = list(cur)
        for t in ("Fixed_Route", pins[0]):
            if t.lower() not in [x.lower() for x in want]:
                want.append(t)
        added = [t for t in want if t not in cur]
        print(f"   profile pin : {pins[0]}")
        print(f"   add         : {', '.join(added) or '(already present)'}")
        if not added:
            continue
        if not APPLY:
            print("   DRY-RUN - pass --apply to write"); continue
        pr = requests.put(f"{base}/orders/{o['id']}.json", headers=hdr,
                          json={"order": {"id": o["id"], "tags": ", ".join(want)}}, timeout=30)
        print(f"   PUT -> HTTP {pr.status_code}")
        chk = requests.get(f"{base}/orders/{o['id']}.json", headers=hdr,
                           params={"fields": "id,name,tags"}, timeout=30).json()["order"]
        got = [t.strip() for t in (chk.get("tags") or "").split(",")]
        ok = "Fixed_Route" in got and pins[0] in got
        print(f"   verify      : {'OK' if ok else 'FAILED'}  {ROUTE.findall(chk.get('tags') or '')}")


if __name__ == "__main__":
    main(sys.argv[1:])
