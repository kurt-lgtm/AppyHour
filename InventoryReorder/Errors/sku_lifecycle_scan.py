"""SKU Lifecycle Scan (weekly workflow).

Reads sku_attributes.json and scans upcoming Recharge queued charges (default 6 months).
Flags:
  - DISCONTINUED SKUs on any upcoming charge  (-> swap to swap_to)
  - SEASONAL SKUs on a charge dated OUTSIDE their season_months
  - ONETIME SKUs that recur on a subscription charge
Writes a dated report CSV. Recurring/unlisted SKUs are ignored.

Usage: python sku_lifecycle_scan.py [months]   (default 6)
"""
import sys, json, csv, time
from datetime import date, timedelta
import requests

REG = r"C:\Users\Work\Claude Projects\AppyHour\GelPackCalculator\sku_attributes.json"
SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
MONTHS = int(sys.argv[1]) if len(sys.argv) > 1 else 6

reg = json.load(open(REG, encoding="utf-8"))["skus"]
rc = json.load(open(SETTINGS, encoding="utf-8"))
H = {"X-Recharge-Access-Token": rc["recharge_api_token"], "Content-Type": "application/json", "X-Recharge-Version": "2021-11"}
def rg(p, par=None, retries=5):
    last = None
    for a in range(retries):
        try:
            r = requests.get("https://api.rechargeapps.com"+p, headers=H, params=par, timeout=30)
            if r.status_code == 429: time.sleep(int(r.headers.get("retry-after", "5"))); continue
            r.raise_for_status(); return r.json()
        except requests.exceptions.Timeout as e: last = e; time.sleep(2*(a+1))
        except Exception as e: last = e; time.sleep(2)
    raise last

# NOTE: date.today() avoided in workflow runner contexts; this is a standalone CLI so it's fine.
start = date.today()
end = start + timedelta(days=MONTHS*31)
WIN_START, WIN_END = start.isoformat(), end.isoformat()

watch = {s: a for s, a in reg.items() if a.get("lifecycle") in ("discontinued", "seasonal", "onetime")}
print(f"watching {len(watch)} non-recurring SKUs; window {WIN_START}..{WIN_END}")

flags = []; ncharges = 0; cursor = None
while True:
    par = {"cursor": cursor, "limit": 250} if cursor else {"status": "queued", "scheduled_at_min": WIN_START, "scheduled_at_max": WIN_END, "limit": 250, "sort_by": "id-asc"}
    d = rg("/charges", par); b = d.get("charges", [])
    if not b: break
    for c in b:
        ncharges += 1
        sched = (c.get("scheduled_at") or "")[:10]
        month = int(sched[5:7]) if len(sched) >= 7 else 0
        cust = c.get("customer", {}) or {}
        eoid = c.get("external_order_id")
        eoid = eoid.get("ecommerce", "") if isinstance(eoid, dict) else (eoid or "")
        for li in c.get("line_items", []):
            s = (li.get("sku") or "").strip()
            a = watch.get(s)
            if not a: continue
            lc = a["lifecycle"]
            reason = None
            if lc == "discontinued":
                reason = "discontinued"
            elif lc == "onetime" and (li.get("purchase_item_type") == "subscription"):
                reason = "onetime-recurring"
            elif lc == "seasonal":
                sm = a.get("season_months", [])
                if month and sm and month not in sm:
                    reason = f"seasonal-out-of-window(month {month}, season {sm})"
            if reason:
                flags.append({"sku": s, "reason": reason, "swap_to": a.get("swap_to", ""),
                              "email": cust.get("email", ""), "scheduled_at": sched,
                              "order": eoid, "charge_id": c.get("id"), "note": a.get("note", "")})
    cursor = d.get("next_cursor")
    if not cursor: break
    time.sleep(0.4)

flags.sort(key=lambda x: (x["sku"], x["scheduled_at"]))
out = rf"C:\Users\Work\Claude Projects\_outputs\reports\sku_lifecycle_scan_{start.isoformat()}.csv"
import os; os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["sku", "reason", "swap_to", "email", "scheduled_at", "order", "charge_id", "note"])
    w.writeheader(); w.writerows(flags)

from collections import Counter
bysku = Counter(x["sku"] for x in flags)
print(f"\nscanned {ncharges} queued charges")
print(f"FLAGGED line items: {len(flags)} across {len(bysku)} SKUs")
for s, c in bysku.most_common():
    a = watch[s]; print(f"  {s:<12} {c:<4} {a['lifecycle']:<13} -> {a.get('swap_to') or '(no target)'}")
print(f"\nreport: {out}")

# Dead-man heartbeat for routine `sku-lifecycle-scan-weekly` (HEARTBEAT_RULES rule 16): beats only
# here, AFTER the report CSV is written — the report IS the work. Graded by
# scripts/automation_health.py EXPECTED["sku-lifecycle-scan"] (10d, weekly). Never raises into
# the host (a ledger hiccup must not fail a scan that already wrote its report).
try:
    import pathlib as _pl
    sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[2]))
    from appyhour_lib.heartbeat import beat
    beat("sku-lifecycle-scan")
except Exception:
    pass
