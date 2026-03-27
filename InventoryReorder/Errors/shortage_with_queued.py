"""Shortage check: _SHIP_2026-03-23 orders + 3/21 queued charges vs inventory + PO#436."""
import requests, json, time, csv
from collections import defaultdict

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
with open(SETTINGS, encoding="utf-8") as f:
    settings = json.load(f)

STORE = settings["shopify_store_url"]
TOKEN = settings["shopify_access_token"]
REST_BASE = f"https://{STORE}.myshopify.com/admin/api/2024-01"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

PICKABLE = ("CH-", "MT-", "AC-")

# ── 1. Shopify _SHIP_2026-03-23 demand ──
print("Fetching Shopify _SHIP_2026-03-23 orders...")
shopify_demand = defaultdict(int)
url = f"{REST_BASE}/orders.json"
params = {
    "status": "open",
    "fulfillment_status": "unfulfilled",
    "limit": 250,
    "fields": "id,name,tags,line_items",
}
page = 0
order_count = 0
while url:
    page += 1
    resp = requests.get(url, headers=HEADERS, params=params if page == 1 else None, timeout=30)
    resp.raise_for_status()
    batch = resp.json().get("orders", [])
    for o in batch:
        tags = [t.strip() for t in (o.get("tags") or "").split(",")]
        if "_SHIP_2026-03-23" in tags:
            order_count += 1
            for li in o.get("line_items", []):
                sku = (li.get("sku") or "").strip()
                qty = li.get("fulfillable_quantity", li.get("quantity", 0))
                if qty > 0 and any(sku.startswith(p) for p in PICKABLE):
                    shopify_demand[sku] += qty
    link = resp.headers.get("Link", "")
    url = None
    if 'rel="next"' in link:
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split("<")[1].split(">")[0]
                params = None
    time.sleep(0.5)

print(f"  {order_count} orders, {sum(shopify_demand.values())} pickable units")

# ── 2. Queued charges for 3/21 ──
print("\nParsing queued charges for 2026-03-21...")
queued_demand = defaultdict(int)
queued_charges = 0
CSV_PATH = r"C:\Users\Work\Claude Projects\charges_queued-2026.03.20-10_31_27-7da7632d082d4eedaffca232ea525511.csv"
with open(CSV_PATH, encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        sched = (row.get("scheduled_at") or "").strip()
        if not sched.startswith("2026-03-21"):
            continue
        sku = (row.get("line_item_sku") or "").strip()
        qty = int(float(row.get("line_item_quantity", 1)))
        queued_charges += 1
        if any(sku.startswith(p) for p in PICKABLE):
            queued_demand[sku] += qty

print(f"  {queued_charges} line items, {sum(queued_demand.values())} pickable units")

# ── 3. Combine demand ──
total_demand = defaultdict(int)
for sku, qty in shopify_demand.items():
    total_demand[sku] += qty
for sku, qty in queued_demand.items():
    total_demand[sku] += qty

print(f"\nCombined demand: {sum(total_demand.values())} units across {len(total_demand)} SKUs")

# ── 4. Inventory + PO ──
inventory = {
    "CH-BARI": 58, "CH-BLR": 2460, "CH-BAP": 151, "CH-BLUELEM": 240,
    "CH-CSGOD": 47, "CH-CTGOD": 9, "CH-BBLUE": 91, "CH-FOWC": 49,
    "CH-EBCC": 1040, "CH-MSMG": 104, "CH-IPAC": 142, "CH-FONTAL": 85,
    "CH-HCGU": 32, "CH-IPRW": 154, "CH-KM39": 142, "CH-LEON": 55,
    "CH-LOSC": 148, "CH-MCPC": 526, "CH-MAU3": 173, "CH-MAFT": 1486,
    "CH-PVEC": 251, "CH-BRZ": 315, "CH-SOT": 477, "CH-RASHI": 31,
    "CH-TOPR": 317, "CH-TIP": 47, "CH-UROSE": 117, "CH-UCONE": 57,
    "CH-WMANG": 965, "CH-WWBC": 1, "CH-WWDI": 11, "CH-PBRIE": 196,
    "CH-TTBRIE": 450, "CH-GPBRIE": 2, "CH-ALP": 0,
    "CH-LOU": 0, "CH-RQCAV": 0, "CH-GUUB": 0, "CH-EBRIE": 0,
    "CH-SHADOW": 0, "CH-RACL": 0, "CH-ALPHA": 0,
    "MT-ASPK": 1707, "MT-CAPO": 691, "MT-SFEN": 3897, "MT-JAHH": 3282,
    "MT-JAMS": 834, "MT-LONZ": 2058, "MT-PRO": 8480, "MT-PP": 128,
    "MT-4PP": 24, "MT-SLRWG": 3545, "MT-SPAP": 1759, "MT-PSS": 2978,
    "MT-TUSC": 3708, "MT-SOP": 513,
    "AC-EFLAT": 2210, "AC-PFLAT": 1071, "AC-TCRISP": 1808, "AC-ACRISP": 1307,
    "AC-RBOL": 4899, "AC-APMB": 238, "AC-BACO": 116, "AC-BLBALS": 8067,
    "AC-CFPH": 5264, "AC-FLH": 744, "AC-FOJ": 576, "AC-GBEF": 3993,
    "AC-PBLINI": 17, "AC-RHB": 228, "AC-HON": 977, "AC-RPJ": 3519,
    "AC-SBPBJ": 98, "AC-SLL": 895, "AC-SRHUB": 6147, "AC-MUSTCH": 2927,
    "AC-PMULB": 200, "AC-DALM": 640, "AC-DCRAN": 77, "AC-APR": 1,
    "AC-DTCH": 501, "AC-LFOLIVE": 1, "AC-MEMB": 56, "AC-PPCM": 715,
    "AC-MARC": 130, "AC-SDF": 614, "AC-SMAL": 614, "AC-RHAZ": 0,
}

po = {
    "CH-ALP": 50, "CH-BARI": 100, "CH-EBRIE": 170, "CH-BRZ": 600,
    "CH-FONTAL": 150, "CH-HCGU": 300, "CH-LEON": 1000, "CH-LOSC": 200,
    "CH-MCPC": 774, "CH-RQCAV": 300, "CH-GUUB": 45, "CH-LOU": 500,
    "CH-WWDI": 250, "CH-WWBC": 200, "AC-RHAZ": 500,
}

available = dict(inventory)
for sku, qty in po.items():
    available[sku] = available.get(sku, 0) + qty

# ── 5. Shortages ──
shortages = []
ok_skus = []
for sku in sorted(set(total_demand.keys())):
    dmd = total_demand[sku]
    avail = available.get(sku, 0)
    gap = avail - dmd
    sh_qty = shopify_demand.get(sku, 0)
    q_qty = queued_demand.get(sku, 0)
    if gap < 0:
        shortages.append((sku, dmd, sh_qty, q_qty, avail, gap))
    else:
        ok_skus.append((sku, dmd, sh_qty, q_qty, avail, gap))

print(f"\n{'='*70}")
print(f"  SHORTAGES ({len(shortages)} SKUs)")
print(f"{'='*70}")
print(f"{'SKU':<16} {'Total':>6} {'Ship23':>6} {'RC21':>6} {'Avail':>6} {'Short':>6}")
print("-" * 55)
shortages.sort(key=lambda x: x[5])
for sku, dmd, sh, q, avail, gap in shortages:
    po_note = f"  (PO +{po[sku]})" if sku in po else ""
    print(f"{sku:<16} {dmd:>6} {sh:>6} {q:>6} {avail:>6} {gap:>6}{po_note}")

total_short = sum(abs(s[5]) for s in shortages)
print(f"\nTotal shortage: {total_short} units across {len(shortages)} SKUs")
