"""Fix split street addresses (house number in address1, street name in address2).

Kurt approved 2026-08-27. Motivating burn: 8 Veho $20 Address Correction charges,
6 of 8 on orders already tagged `invalid_address` in Shopify and shipped anyway.
Root cause is this split, which leaves a carrier reading address1 = "77".

Scope (from the read-only scans, see _outputs/reports/):
  - Recharge: the 24 split addresses that back an ACTIVE subscription (of 143 total
    split addresses across 47,758 -- the other 119 have no active sub, left alone).
  - Shopify: the 2 OPEN UNFULFILLED orders with the same split, plus each fixed
    customer's default address so the next renewal does not recreate the break.

Rules honored:
  - Pure concatenation. Nothing is invented -- no typo "corrections", no zip edits.
    (~/.claude/rules/never-fabricate.md)
  - A trailing unit designator STAYS IN address2 (Kurt 2026-08-27), never jammed
    into address1. Only explicit unit tokens split off; anything ambiguous is SKIPPED
    for manual review rather than guessed.
  - Match guard: an address is only written when its CURRENT live values still equal
    what the scan saw. Never clobber an address the customer already fixed.
  - Recharge v2021-11, timeout=30, retry/backoff (recharge-api skill).
  - Dry-run is the default. --apply writes.

Run:  python fix_split_addresses_2026-08-27.py            # dry-run
      python fix_split_addresses_2026-08-27.py --apply
"""
import csv
import re
import sys
import time

sys.path.insert(0, r"C:\Users\Work\Claude Projects\AppyHour\AppyHourMCP")
sys.path.insert(0, r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder")
import requests  # noqa: E402
from utils import get_shopify_auth  # noqa: E402
from inventory_reorder import load_settings as load_inv_settings  # noqa: E402

REPORTS = r"C:\Users\Work\Claude Projects\_outputs\reports"
RC_ACTIVE_CSV = REPORTS + r"\recharge-split-address-ACTIVE-2026-08-27.csv"
SHOPIFY_CSV = REPORTS + r"\shopify-split-address-OPEN-2026-08-27.csv"

# EXPLICIT unit tokens only.
#
# 🔴 The dry-run burn (2026-08-27): an earlier version also treated a trailing bare
# letter after a street suffix as a unit. That is WRONG -- a trailing letter is far
# more often a DIRECTIONAL that belongs to the street name:
#     "15401 E 40th St S"     -> the S is South, not unit S
#     "4609 Jim Mitchell Trl w" -> the w is West, not unit W
# Splitting those mangles a valid address into an invalid one, which is the exact
# failure this script exists to fix. A bare trailing letter is now always kept in
# address1. Ambiguity resolves toward LEAVING THE CUSTOMER'S STRING INTACT.
UNIT_KW = r"(?:apt|apartment|unit|ste|suite|bldg|building|fl|floor|rm|room|lot|trlr|#)"
UNIT_RE = re.compile(r"^(?P<street>.*?)[\s,]+(?P<unit>" + UNIT_KW + r"\.?\s*\S+)$", re.I)


def split_unit(address2):
    """-> (street_part, unit_part_or_empty). Returns (None, None) if ambiguous."""
    a2 = (address2 or "").strip()
    m = UNIT_RE.match(a2)
    if m:
        return m.group("street").strip().rstrip(","), m.group("unit").strip()
    if re.search(r"\b" + UNIT_KW + r"\b", a2, re.I):
        return None, None  # unit keyword we could not cleanly split -- do not guess
    return a2, ""


def plan_row(a1, a2):
    """-> (new_address1, new_address2) or (None, None) when ambiguous."""
    street, unit = split_unit(a2)
    if street is None:
        return None, None
    return (str(a1 or "").strip() + " " + street).strip(), unit


# ---------------------------------------------------------------- Recharge

RC_BASE = "https://api.rechargeapps.com"


def rc_headers(token):
    return {"X-Recharge-Access-Token": token, "X-Recharge-Version": "2021-11"}


def rc_call(method, path, headers, params=None, json_body=None, retries=5):
    last = None
    for attempt in range(retries):
        try:
            r = requests.request(method, RC_BASE + path, headers=headers,
                                 params=params, json=json_body, timeout=30)
            if r.status_code == 429:
                time.sleep(int(r.headers.get("retry-after", "5")))
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout as e:
            last = e
            time.sleep(2 * (attempt + 1))
        except Exception as e:
            last = e
            time.sleep(2)
    raise last if last else RuntimeError("recharge %s %s exhausted retries" % (method, path))


def fix_recharge(token_r, token_w, apply):
    rows = list(csv.DictReader(open(RC_ACTIVE_CSV, encoding="utf-8")))
    print("\n=== RECHARGE -- %d active-sub split addresses ===" % len(rows))
    hdr_r, hdr_w = rc_headers(token_r), rc_headers(token_w)
    done = skipped = drifted = failed = 0
    for row in rows:
        aid = row["address_id"]
        who = (row["first"] or "") + " " + (row["last"] or "")
        new1, new2 = plan_row(row["address1"], row["address2"])
        if new1 is None:
            print("  SKIP-AMBIGUOUS  %-24s '%s' | '%s'" % (who, row["address1"], row["address2"]))
            skipped += 1
            continue
        try:
            live = rc_call("GET", "/addresses/%s" % aid, hdr_r).get("address", {})
        except Exception as e:
            print("  GET-ERR         %-24s %s" % (who, e))
            failed += 1
            continue
        cur1 = (live.get("address1") or "").strip()
        cur2 = (live.get("address2") or "").strip()
        if cur1 != row["address1"].strip() or cur2 != row["address2"].strip():
            print("  DRIFT-SKIP      %-24s live='%s' | '%s' (scan saw '%s' | '%s')"
                  % (who, cur1, cur2, row["address1"], row["address2"]))
            drifted += 1
            continue
        print("  %-15s %-24s '%s' | '%s'  ->  '%s' | '%s'"
              % ("APPLY" if apply else "WOULD", who, cur1, cur2, new1, new2))
        if apply:
            try:
                rc_call("PUT", "/addresses/%s" % aid, hdr_w,
                        json_body={"address1": new1, "address2": new2})
                done += 1
            except Exception as e:
                print("      PUT-ERR %s" % e)
                failed += 1
        else:
            done += 1
        time.sleep(0.5)
    print("  -- recharge: %d %s, %d ambiguous, %d drifted, %d failed"
          % (done, "updated" if apply else "planned", skipped, drifted, failed))
    return {"done": done, "skipped": skipped, "drifted": drifted, "failed": failed}


# ---------------------------------------------------------------- Shopify


def fix_shopify_orders(base, hdr, apply):
    try:
        rows = list(csv.DictReader(open(SHOPIFY_CSV, encoding="utf-8")))
    except FileNotFoundError:
        print("\n=== SHOPIFY ORDERS -- scan CSV absent, nothing to do ===")
        return {"done": 0}
    print("\n=== SHOPIFY -- %d open unfulfilled orders ===" % len(rows))
    done = drifted = failed = skipped = 0
    for row in rows:
        oid, name = row["id"], row["order"]
        new1, new2 = plan_row(row["address1"], row["address2"])
        if new1 is None:
            print("  SKIP-AMBIGUOUS  %s" % name)
            skipped += 1
            continue
        r = requests.get("%s/orders/%s.json" % (base, oid), headers=hdr,
                         params={"fields": "id,name,shipping_address,customer"}, timeout=30)
        r.raise_for_status()
        o = r.json()["order"]
        a = o.get("shipping_address") or {}
        cur1 = (a.get("address1") or "").strip()
        cur2 = (a.get("address2") or "").strip()
        if cur1 != row["address1"].strip() or cur2 != row["address2"].strip():
            print("  DRIFT-SKIP      %s live='%s' | '%s'" % (name, cur1, cur2))
            drifted += 1
            continue
        print("  %-15s %-9s '%s' | '%s'  ->  '%s' | '%s'"
              % ("APPLY" if apply else "WOULD", name, cur1, cur2, new1, new2))
        if apply:
            try:
                pr = requests.put("%s/orders/%s.json" % (base, oid), headers=hdr, timeout=30,
                                  json={"order": {"id": int(oid),
                                                  "shipping_address": {"address1": new1,
                                                                       "address2": new2}}})
                pr.raise_for_status()
                done += 1
            except Exception as e:
                print("      PUT-ERR %s" % e)
                failed += 1
        else:
            done += 1
        time.sleep(0.5)
    print("  -- shopify orders: %d %s, %d ambiguous, %d drifted, %d failed"
          % (done, "updated" if apply else "planned", skipped, drifted, failed))
    return {"done": done, "drifted": drifted, "failed": failed, "skipped": skipped}


def fix_shopify_customer_defaults(base, hdr, apply):
    """Same join on the CUSTOMER default address, so the next renewal does not
    recreate the split. Guarded on the exact address1+address2 the scan saw."""
    rows = list(csv.DictReader(open(RC_ACTIVE_CSV, encoding="utf-8")))
    print("\n=== SHOPIFY CUSTOMER DEFAULTS -- %d candidates ===" % len(rows))
    done = nomatch = failed = skipped = 0
    for row in rows:
        who = (row["first"] or "") + " " + (row["last"] or "")
        new1, new2 = plan_row(row["address1"], row["address2"])
        if new1 is None:
            skipped += 1
            continue
        try:
            q = "%s %s" % (row["first"] or "", row["last"] or "")
            r = requests.get("%s/customers/search.json" % base, headers=hdr,
                             params={"query": q.strip(), "limit": 10}, timeout=30)
            r.raise_for_status()
            custs = r.json().get("customers", [])
            if not custs:
                print("  NO-CUSTOMER     %s" % who)
                nomatch += 1
                continue
            hit = 0
            for c in custs:
                ar = requests.get("%s/customers/%s/addresses.json" % (base, c["id"]),
                                  headers=hdr, timeout=30)
                ar.raise_for_status()
                for ad in ar.json().get("addresses", []):
                    if (ad.get("address1") or "").strip() != row["address1"].strip():
                        continue
                    if (ad.get("address2") or "").strip() != row["address2"].strip():
                        continue
                    print("  %-15s %-24s cust=%s addr=%s  ->  '%s' | '%s'"
                          % ("APPLY" if apply else "WOULD", who, c["id"], ad["id"], new1, new2))
                    if apply:
                        pr = requests.put(
                            "%s/customers/%s/addresses/%s.json" % (base, c["id"], ad["id"]),
                            headers=hdr, timeout=30,
                            json={"address": {"address1": new1, "address2": new2}})
                        pr.raise_for_status()
                    hit += 1
                    time.sleep(0.3)
            if hit:
                done += hit
            else:
                print("  NO-MATCH        %s (already fixed?)" % who)
                nomatch += 1
        except Exception as e:
            print("  ERR             %s: %s" % (who, e))
            failed += 1
        time.sleep(0.3)
    print("  -- shopify customers: %d %s, %d no-match, %d ambiguous, %d failed"
          % (done, "updated" if apply else "planned", nomatch, skipped, failed))
    return {"done": done, "nomatch": nomatch, "failed": failed}


def main():
    apply = "--apply" in sys.argv
    settings = load_inv_settings()
    token_r = settings.get("recharge_api_token", "")
    token_w = settings.get("recharge_api_token_write", "") or token_r
    if not token_r:
        print("ERROR: no recharge_api_token")
        return 1
    base, hdr = get_shopify_auth()
    print("MODE: %s" % ("APPLY (writes live)" if apply else "DRY-RUN (no writes)"))
    fix_recharge(token_r, token_w, apply)
    fix_shopify_orders(base, hdr, apply)
    fix_shopify_customer_defaults(base, hdr, apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
