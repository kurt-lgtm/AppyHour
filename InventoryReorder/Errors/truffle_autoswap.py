# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///
"""Auto-swap truffle meat for a never-had non-truffle meat on Christine Farley's
Shopify order, once the Recharge charge has landed as an unfulfilled Shopify order.

Customer: Christine Farley, cfarley04@gmail.com, Recharge customer 239000650.
Trigger meat to remove: any truffle meat (MT-PTUF etc — match 'truffle' in title/sku, MT- only).
Replacement: first meat in PREFERRED that she has never received, has a $0 curation
variant, and is not already on the order. One-for-one swap, $0 variant => no upcharge.

Outputs a single JSON line to stdout describing the result. Does NOT touch Slack
(the scheduled Claude run reads this JSON and posts to Slack).

Run with --execute to actually edit the order; default is dry-run.
"""
import json, sys, requests, time

SETTINGS = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
EXECUTE = "--execute" in sys.argv
CUST_EMAIL = "cfarley04@gmail.com"
RC_CUSTOMER = "239000650"

# Preference order — premium first. Candidates are intersected with ACTIVE inventory
# (the SKU<->MFG-name list); anything not in inventory or in archived_skus is rejected,
# so discontinued SKUs that still have a stray Shopify variant (e.g. MT-GEN) can't slip in.
PREFERRED = ["MT-BRAS", "MT-IBHP", "MT-CAPO", "MT-JAMS", "MT-SOP", "MT-TUSC",
             "MT-JAHH", "MT-JUNI", "MT-ASPK", "MT-PP"]

with open(SETTINGS, encoding="utf-8") as f:
    S = json.load(f)

# Active meat universe = inventory MT- SKUs minus archived (dead) SKUs.
ARCHIVED = {s.strip().upper() for s in S.get("archived_skus", [])}
ACTIVE_MEATS = {k.strip().upper() for k in S.get("inventory", {})
                if k.strip().upper().startswith("MT-")} - ARCHIVED
STORE = S["shopify_store_url"]; TOK = S["shopify_access_token"]
REST = f"https://{STORE}.myshopify.com/admin/api/2024-01"
GQL = f"https://{STORE}.myshopify.com/admin/api/2024-01/graphql.json"
H = {"X-Shopify-Access-Token": TOK, "Content-Type": "application/json"}


def gql(q, v=None):
    r = requests.post(GQL, headers=H, json={"query": q, "variables": v or {}}, timeout=30)
    r.raise_for_status()
    d = r.json()
    if d.get("errors"):
        raise RuntimeError(d["errors"])
    return d["data"]


def rest(path, params=None):
    r = requests.get(f"{REST}{path}", headers=H, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def out(d):
    # Always surface cross-cutting flags if they've been computed.
    for k in ("archived_seen", "truffle_cheese"):
        v = globals().get(k)
        if v and k not in d:
            d[k] = v
    print(json.dumps(d))
    sys.exit(0)


def is_truffle(sku, title):
    t = f"{sku} {title}".lower()
    return "truffle" in t


def gid_zero(sku):
    """Return the $0.00 curation variant gid for a SKU, or None."""
    d = gql('query($q:String!){productVariants(first:25,query:$q){edges{node{id sku price}}}}',
            {"q": f"sku:{sku}"})
    ms = [e["node"] for e in d["productVariants"]["edges"] if (e["node"].get("sku") or "").strip().upper() == sku]
    zero = [m for m in ms if m.get("price") == "0.00"]
    return zero[0]["id"] if zero else None


# 1. Find her open orders (by email). Need the truffle one that is unfulfilled.
orders = rest("/orders.json", {"email": CUST_EMAIL, "status": "open", "limit": 50,
                               "fields": "id,name,fulfillment_status,tags,line_items"}).get("orders", [])

# Build her full meat history across ALL her orders (any status) for the never-had check.
all_orders = rest("/orders.json", {"email": CUST_EMAIL, "status": "any", "limit": 250,
                                   "fields": "id,name,line_items"}).get("orders", [])
had_meats = set()
for o in all_orders:
    for li in o.get("line_items", []):
        sku = (li.get("sku") or "").strip().upper()
        if sku.startswith("MT-"):
            had_meats.add(sku)

# Flag any ARCHIVED (dead) SKU appearing on her open orders — e.g. MT-GEN resurfacing.
archived_seen = []
# Alert-only: a truffle CHEESE (CH-) is NOT auto-swapped (cheese rules differ) — just surfaced.
truffle_cheese = []
for o in orders:
    for li in o.get("line_items", []):
        sku = (li.get("sku") or "").strip().upper()
        fq = li.get("fulfillable_quantity", 0)
        if fq <= 0:
            continue
        if sku in ARCHIVED:
            archived_seen.append({"order": o["name"], "sku": sku})
        if sku.startswith("CH-") and is_truffle(sku, li.get("title", "")):
            truffle_cheese.append({"order": o["name"], "sku": sku, "title": li.get("title", "")})

# Find the target order: open, unfulfilled, has a truffle MT- line with fq>0.
target = None
truffle_line = None
for o in orders:
    if (o.get("fulfillment_status") or "") not in (None, "", "partial"):
        continue  # already fulfilled
    for li in o.get("line_items", []):
        sku = (li.get("sku") or "").strip().upper()
        fq = li.get("fulfillable_quantity", 0)
        if sku.startswith("MT-") and fq > 0 and is_truffle(sku, li.get("title", "")):
            target = o; truffle_line = li; break
    if target:
        break

if not target:
    # Check whether a truffle is sitting on an already-fulfilled order (missed window).
    for o in orders:
        for li in o.get("line_items", []):
            sku = (li.get("sku") or "").strip().upper()
            if sku.startswith("MT-") and is_truffle(sku, li.get("title", "")) and (o.get("fulfillment_status") or "") not in (None, ""):
                out({"action": "missed_window", "order": o["name"], "archived_seen": archived_seen,
                     "note": f"truffle {sku} on already-fulfilled order — too late to swap"})
    out({"action": "none", "archived_seen": archived_seen,
         "note": "no unfulfilled order with a truffle meat"})

# 2. Candidate pool = ACTIVE inventory meats, PREFERRED order first then the rest.
on_order = {(li.get("sku") or "").strip().upper() for li in target["line_items"]
            if li.get("fulfillable_quantity", 0) > 0}
removed_sku = (truffle_line.get("sku") or "").strip().upper()
ordered = [c for c in PREFERRED if c in ACTIVE_MEATS] + sorted(ACTIVE_MEATS - set(PREFERRED))
chosen = None; chosen_gid = None
tried = []
for cand in ordered:
    if cand in had_meats or cand in on_order:
        tried.append(f"{cand}:skip(had/on-order)"); continue
    if is_truffle(cand, ""):
        tried.append(f"{cand}:skip(truffle)"); continue
    g = gid_zero(cand)
    if not g:
        tried.append(f"{cand}:skip(no-$0-variant)"); continue
    chosen = cand; chosen_gid = g; break

if not chosen:
    out({"action": "no_candidate", "order": target["name"], "removed": removed_sku,
         "had_meats": sorted(had_meats), "active_meats": sorted(ACTIVE_MEATS),
         "tried": tried, "archived_seen": archived_seen,
         "note": "no active-inventory replacement passed all filters"})

result = {"action": "swap" if EXECUTE else "swap_dryrun", "order": target["name"],
          "order_id": target["id"], "removed": removed_sku, "added": chosen,
          "had_meats": sorted(had_meats), "archived_seen": archived_seen}

if not EXECUTE:
    out(result)

# 3. Execute order edit: remove truffle, add replacement $0 variant.
ogid = f"gid://shopify/Order/{target['id']}"
b = gql("mutation($id:ID!){orderEditBegin(id:$id){calculatedOrder{id lineItems(first:60){edges{node{id sku quantity}}}} userErrors{field message}}}",
        {"id": ogid})
if b["orderEditBegin"]["userErrors"]:
    out({**result, "action": "error", "error": b["orderEditBegin"]["userErrors"]})
calc = b["orderEditBegin"]["calculatedOrder"]
cid = calc["id"]
# set truffle line qty 0
tline = [e["node"] for e in calc["lineItems"]["edges"]
         if (e["node"]["sku"] or "").strip().upper() == removed_sku and e["node"]["quantity"] > 0]
if not tline:
    out({**result, "action": "error", "error": f"{removed_sku} not in edit session"})
sq = gql("mutation($id:ID!,$li:ID!){orderEditSetQuantity(id:$id,lineItemId:$li,quantity:0){userErrors{field message}}}",
         {"id": cid, "li": tline[0]["id"]})
if sq["orderEditSetQuantity"]["userErrors"]:
    out({**result, "action": "error", "error": sq["orderEditSetQuantity"]["userErrors"]})
av = gql("mutation($id:ID!,$v:ID!){orderEditAddVariant(id:$id,variantId:$v,quantity:1,allowDuplicates:true){userErrors{field message}}}",
         {"id": cid, "v": chosen_gid})
if av["orderEditAddVariant"]["userErrors"]:
    out({**result, "action": "error", "error": av["orderEditAddVariant"]["userErrors"]})
cm = gql('mutation($id:ID!){orderEditCommit(id:$id,notifyCustomer:false,staffNote:"truffle auto-swap: %s -> %s (never-had non-truffle meat)"){order{name} userErrors{field message}}}' % (removed_sku, chosen),
         {"id": cid})
if cm["orderEditCommit"]["userErrors"]:
    out({**result, "action": "error", "error": cm["orderEditCommit"]["userErrors"]})
out(result)
