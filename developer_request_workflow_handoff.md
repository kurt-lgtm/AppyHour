# Developer Request: Unified Fulfillment Tool

## Context

The weekly fulfillment process currently requires 2-3 hours of manual work across **4 separate tools**:
1. **React fulfillment tool** — generates Matrixify upload CSV (child SKUs → parent SKUs)
2. **Matrixify** — uploads the CSV to Shopify (slow, ~30-60 min for 2,500 orders)
3. **RMFG Translator portal** (translator.robbinsmfginc.com) — reads Shopify orders by tag, generates the production matrix XLSX
4. **Gift redemption React tool** — separate tool for gift orders that Shopify can't edit

Plus manual steps: inventory upload to Shopify, shortage investigation, Shopify API swaps, downloading from RMFG portal, reformatting the XLSX (rename tab, add ProductionDay column, fix zips, sort orders, auto-space columns), merging gift sheet, renaming file, MFG name validation, and emailing.

**The goal: Replace ALL of this with one unified React tool.** The tool should:
- Accept inventory CSV (not read Shopify)
- Allocate child SKUs to parents (existing logic)
- Sync to Shopify directly via GraphQL (replace Matrixify)
- Generate the RMFG matrix XLSX directly (replace the RMFG Translator portal)
- Handle gift redemption in the same run (replace the separate gift tool)
- Validate everything before output (MFG names, CEX-EC counts, format)

One tool, one run, one output. An operator with minimal training should be able to run the full Saturday fulfillment in under 30 minutes.

I built a Python prototype (`matrix_commander.py`) that implements the validation, inventory checking, Shopify sync, and finalize logic. This document describes what needs to be built into the React tool, using the prototype as a spec.

---

## Flow Summary: Current vs Target

### Current Flow (4 tools, 2-3 hours)

```
┌─ BEFORE FULFILLMENT ──────────────────────────────────────────────┐
│  1. Manually upload inventory to Shopify (paid + $0 variants)     │
│     Tool: Shopify Admin (manual)          Time: 30-60 min         │
│                                                                    │
│  2. Cold chain app applies routing + gel pack tags                 │
│     Tool: GelPackCalculator               Time: 10 min            │
└────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─ ALLOCATION ──────────────────────────────────────────────────────┐
│  3. React tool generates PR-CJAM Matrixify CSV                    │
│     Tool: React fulfillment tool          Time: 5 min             │
│                                                                    │
│  4. Upload PR-CJAM CSV to Matrixify, WAIT for processing          │
│     Tool: Matrixify                       Time: 30-60 min (wait)  │
│                                                                    │
│  5. React tool generates 2nd pass Matrixify CSV (all other parents)│
│     Tool: React fulfillment tool          Time: 5 min             │
│                                                                    │
│  6. Upload 2nd CSV to Matrixify, WAIT again                       │
│     Tool: Matrixify                       Time: 30-60 min (wait)  │
└────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─ SHORTAGE RESOLUTION ─────────────────────────────────────────────┐
│  7. Download SKU demand, cross-check against inventory             │
│     Tool: Manual / spreadsheet            Time: 20 min            │
│                                                                    │
│  8. If shortages → fix via Shopify API swaps                       │
│     Tool: Shopify Admin / API scripts     Time: 20-60 min         │
└────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─ MATRIX GENERATION ───────────────────────────────────────────────┐
│  9. Go to RMFG Translator portal, filter by tag, download XLSX     │
│     Tool: translator.robbinsmfginc.com    Time: 5 min             │
│                                                                    │
│ 10. Reformat XLSX: rename tab, add ProductionDay, fix zips, sort   │
│     Tool: Excel (manual)                  Time: 20-30 min         │
│                                                                    │
│ 11. Process gift redemption orders in separate React tool           │
│     Tool: Gift redemption React app       Time: 10 min            │
│                                                                    │
│ 12. Merge gift sheet, check MFG names, rename file                 │
│     Tool: Excel (manual)                  Time: 10 min            │
│                                                                    │
│ 13. Email final XLSX to RMFG                                       │
│     Tool: Email                           Time: 1 min             │
└────────────────────────────────────────────────────────────────────┘

Total: 2-3 hours, 4+ tools, many manual steps
```

### Target Flow (1 tool, 15-20 minutes)

```
┌─ BEFORE FULFILLMENT ──────────────────────────────────────────────┐
│  1. Cold chain app applies routing + gel pack tags                 │
│     Tool: GelPackCalculator               Time: 10 min            │
│     (could eventually be built into React tool too)                │
└────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─ ONE TOOL DOES EVERYTHING ────────────────────────────────────────┐
│  2. Open React fulfillment tool                                    │
│     - Auto-loads inventory CSV from planning app API               │
│       (http://localhost:5187/api/export_inventory_csv)             │
│     - Select RMFG ship tag (e.g., RMFG_20260328)                  │
│     - Select ship day (SAT or TUE)                                 │
│     - Enter ship date (Monday or Tuesday date)                     │
│                                                                    │
│  3. Click "Generate"                                               │
│     a) Allocate ALL child SKUs in ONE pass                         │
│        (PR-CJAM + CEX-EC + AHB-MED/LGE + extras — unified)       │
│     b) Show demand summary + shortage report                       │
│     c) Suggest swaps for shortages → operator approves             │
│                                                                    │
│  4. Click "Sync & Build"                                           │
│     a) Sync $0 variants to Shopify via GraphQL (~5 min)            │
│        - Skips gift orders (Shopify blocks edits)                  │
│        - Skips duplicates (reads current line items first)         │
│     b) Generate RMFG matrix XLSX directly from Shopify data        │
│        - Access_LIVE tab, ProductionDay, sorted, zips fixed        │
│        - Gift orders included in matrix                            │
│        - MFG name validation (all SKUs onboarded?)                 │
│        - File: AHB_WeeklyProductionQuery_MM-DD-YY_vF.xlsx         │
│                                                                    │
│  5. Review validation dashboard                                    │
│     - All checks green? → proceed                                  │
│     - Red flags? → fix before sending                              │
│                                                                    │
│  6. Click "Send to RMFG"                                           │
│     - Emails final XLSX                                            │
│                                                                    │
│  7. (Auto) Depletion fed back to planning app                      │
│     - POST to http://localhost:5187/api/import_depletion_from_matrix│
│     - Planning app auto-projects Tuesday inventory                  │
└────────────────────────────────────────────────────────────────────┘

Total: 15-20 minutes, 1 tool, operator just clicks buttons
```

### What Gets Eliminated

| Manual Step | How It's Eliminated |
|---|---|
| Upload inventory to Shopify | Planning app provides CSV directly |
| Two-pass Matrixify upload + wait | Unified single-pass allocation + direct GraphQL sync |
| Manual shortage investigation | Auto shortage report + swap suggestions |
| RMFG portal download | Tool generates matrix directly from Shopify |
| Excel reformatting (tab, column, zips, sort) | Tool outputs correct format from the start |
| Separate gift redemption tool | Gift orders processed in same run |
| Manual sheet merge | Auto-merged into final XLSX |
| MFG name checking | Auto-validated before output |
| File renaming | Auto-named with ship date |

---

## Part 1: Inventory CSV Input (P0 — Must Have)

**Problem:** I currently upload inventory to Shopify manually before each run — splitting between paid and $0 variants for every SKU. This takes 30-60 minutes and is error-prone. The React tool reads these Shopify inventory levels, so bad input = bad allocation decisions = hours of fixing.

**Change:** The React tool should accept an inventory CSV as input instead of reading Shopify inventory levels.

**Input format:**
```csv
sku,available_qty
CH-LEON,342
CH-TTBRIE,298
MT-LONZ,501
AC-DTCH,540
...
```

**Rules:**
- If a SKU is in the CSV, use that quantity for allocation decisions
- If a SKU is NOT in the CSV, treat it as 0 available (flag a warning)
- Validate that all SKUs the tool needs are present — warn on missing ones
- This completely replaces reading Shopify inventory levels for allocation

**I will generate this CSV from my planning tools**, which calculate real availability from: `last confirmed inventory + cut order yields + expected intakes - depletions`. This is more accurate than Shopify because it accounts for restocks and doesn't have the paid/$0 splitting problem.

---

## Part 1b: Unify PR-CJAM and Second-Pass Allocations (P0 — Must Have)

**Problem:** The React tool currently generates TWO separate uploads:
1. First: PR-CJAM assignments (cheese + jam pairings)
2. Second: All other parents (CEX-EC, AHB-MED, AHB-LGE, EX-EC, EX-EA, etc.)

The second pass must wait for the first to finish uploading via Matrixify before it can run, because it needs to see what PR-CJAM items are already on Shopify to avoid duplicating a cheese. **This two-pass workflow is the #1 reason uploads take so long** — it doubles the Matrixify wait time.

**Change:** Generate ALL assignments in a single pass. One output CSV containing PR-CJAM + CEX-EC + AHB-MED/LGE + EX-EC + EX-EA + everything.

**Why this is now safe:** We're replacing Matrixify with direct Shopify sync (GraphQL order edit API). The sync reads current order line items before each edit and skips any SKU that's already on the order. Duplicate detection happens at sync time, not at generation time. So there's no need to split into two passes.

**What changes in the React tool:**
1. Run PR-CJAM allocation and all other allocations together in one pass
2. Output one combined CSV (same format, just all rows together)
3. The sync tool handles the rest — it won't add CH-MAU3 twice even if PR-CJAM-MONG and CEX-EC-MONG both resolve to it

**Impact:** Cuts total sync time from 60+ minutes (two Matrixify uploads with wait) to ~5 minutes (one direct sync pass). This is the CEO's "uploads take too long" concern resolved.

---

## Part 2: Demand Summary Output (P0 — Must Have)

After allocation, the tool should output a summary of what it allocated:

```csv
sku,total_allocated,parent_breakdown
CH-LEON,197,"CEX-EC-MONG: 197"
CH-MCPC,315,"CEX-EC-MDT: 273, CEX-EC-OWC: 42"
CH-TTBRIE,312,"AHB-MED: 245, AHB-LGE: 67"
MT-TUSC,388,"AHB-MED: 312, AHB-LGE: 76"
```

This lets the operator cross-check demand vs inventory before uploading anything. Problems caught here cost 0 time to fix. Problems caught after Matrixify upload cost hours.

---

## Part 3: Pre-Output Validation Gate (P1 — Should Have)

Before the tool generates its Matrixify CSV and XLSX output, it should run these checks and **show results to the operator**. Critical failures should block output.

### Check 1: All SKUs Mapped to MFG Names (BLOCKING)
Every child SKU assigned must exist in RMFG's product list. If a new SKU isn't onboarded at RMFG, the matrix file will have a missing column.

**Implementation:** Maintain a mapping of `sku → mfg_name`. Before output, verify every allocated SKU exists. If not: `BLOCKED: CH-NEWCHEESE not onboarded at RMFG.`

### Check 2: CEX-EC Cheese Match (BLOCKING)
For every order with a CEX-EC-{curation} parent, verify the expected extra cheese is in the assignments.

| Curation | Expected Cheese | Notes |
|----------|----------------|-------|
| MONG | CH-BAP | |
| MDT | (split) | 64% CH-MCPC + 36% CH-MSMG |
| OWC | CH-FOWC | |
| SPN | CH-MAU3 | |
| ALPN | CH-TOPR | |
| ISUN | CH-WMANG | |
| HHIGH | CH-WMANG | |
| NMS | CH-WMANG | |
| BYO | CH-WMANG | |
| SS | CH-WMANG | |

### Check 3: No Duplicate Child SKUs (BLOCKING)
The tool already handles this. Ensure it's explicitly logged — if a duplicate would be created, log the order number and SKU, skip that assignment.

### Check 4: Inventory Sufficient (WARNING, not blocking)
Compare total demand per SKU against the inventory CSV. If short:
- Show which SKUs, how many short
- Suggest swaps from substitution families (see Part 5)
- Let operator decide whether to proceed or adjust

### Check 5: Matrix Format (BLOCKING)
These match the existing QC checker in the cold chain app (`gel_pack_webview.py:qc_check_file`):

- **Tab name** must be `Access_LIVE` (not renamed or on wrong sheet)
- **Column 14** must be `ProductionDay` with value `SAT` or `TUE`
- **OrderIDs** must be numeric (no `#`, no commas)
- **Zip codes** must be stored as text to preserve leading zeroes — if stored as number, leading zeros are lost (e.g., `01234` becomes `1234`). Also flag 4-digit zips that need a leading zero.
- **No duplicate product columns** in the header row
- **No duplicate OrderIDs** — same order appearing multiple times
- **OrderIDs sorted** smallest to largest
- **Product column headers** must match format: `AHB (S_REG): Product Name`
- **Low item count** — flag orders with fewer than 10 items (excluding Reship-tagged and Tray orders)

### Check 6: Routing & Tag Validation (WARNING)
These are currently handled by the cold chain app's QC checker but should also be validated here:

- Every order must have at least one routing tag (e.g., `!ANY - Dallas_AHB!`)
- **Exclusive carrier tags** can't combine on the same order
- **Tuesday-specific**: No Anaheim/OnTrac routing on Tuesdays (OnTrac doesn't ship Tuesday)
- **Tuesday-specific**: CA and FL orders require `!FedEx 2Day OneRate` tag
- **Unknown `!` tags** — any bang-prefixed tag not in the known set should be flagged
- **Gift Redemption orders** should be detected and excluded from routing checks (they ship separately)

### Check 7: SKU Column Completeness (BLOCKING)
- Every product column `AHB (S_REG): Product Name` must map to a known SKU via `NAME_TO_SKU`
- If a product name doesn't map, the tool can't track demand for that product
- The current mapping has 96 entries (see `AppyHourMCP/tools/constants.py`)

---

## Part 4: Shopify Sync — Replace Matrixify (P1 — Should Have)

**Problem:** Matrixify upload is slow for 2,500+ orders and requires a separate manual step.

**Change:** Direct Shopify GraphQL sync. Working Python implementation: `matrix_commander.py` lines 900-1280.

### Step 1: Look up $0 variant GIDs (once, before sync loop)

```
// Collect all unique child SKUs from the allocation output
skus_needed = Set of all child SKUs (CH-LEON, MT-TUSC, AC-PRPE, etc.)

// Query Shopify for variant GIDs — batch 10 SKUs per query
for each batch of 10 SKUs:
    query = `productVariants(first: 50, query: "sku:CH-LEON OR sku:MT-TUSC OR ...")`
    // Returns: [{ id: "gid://shopify/ProductVariant/12345", sku: "CH-LEON", price: "0.00" }]
    // Pick the CHEAPEST variant per SKU (the $0 one)
    for each result:
        if result.price < stored_price[result.sku]:
            variant_gids[result.sku] = result.id

// Result: variant_gids = { "CH-LEON": "gid://shopify/ProductVariant/12345", ... }
```

### Step 2: Sync each order (parallel, 5-10 workers)

```
for each order in allocation_output (parallel):
    // Skip gift redemption
    if "Gift Redemption" in order.tags:
        log("SKIP", order, "gift redemption")
        continue

    // Fetch current line items
    current_skus = {}
    for li in order.line_items:
        if li.fulfillable_quantity > 0 and li.sku:
            current_skus[li.sku] = li.fulfillable_quantity

    // Determine what to add
    to_add = []
    for sku in order.assigned_child_skus:
        if sku in current_skus:
            log("SKIP_SKU", order, sku, "already on order")  // duplicate protection
        else if sku in variant_gids:
            to_add.append(sku)

    if to_add is empty:
        log("SKIP", order, "already correct")
        continue

    // GraphQL order edit: begin → add variants → commit
    mutation orderEditBegin($id: ID!) {
        orderEditBegin(id: $id) {
            calculatedOrder { id }
            userErrors { field message }
        }
    }
    // variables: { id: "gid://shopify/Order/{order.id}" }
    calc_id = result.calculatedOrder.id

    for each sku in to_add:
        mutation orderEditAddVariant($id: ID!, $variantId: ID!, $quantity: Int!) {
            orderEditAddVariant(id: $id, variantId: $variantId, quantity: 1,
                                allowDuplicates: false) {
                calculatedOrder { id }
                userErrors { field message }
            }
        }
        // variables: { id: calc_id, variantId: variant_gids[sku] }
        // NOTE: allowDuplicates: false — Shopify itself rejects if variant already exists

    mutation orderEditCommit($id: ID!) {
        orderEditCommit(id: $id) {
            order { id }
            userErrors { field message }
        }
    }
    // ALWAYS notifyCustomer: false (don't email customer about backend edit)

    log("UPDATED", order, to_add)
```

### Step 3: Rate limiting

```
// Shopify GraphQL: 1000 cost points, restores 50/sec
// Each mutation ≈ 10 points
// At 5 workers × 3 mutations/order = 150 points/sec burst → OK with backoff
// At 10 workers → may hit limit, add 100ms delay between orders

if response.extensions.cost.throttleStatus.currentlyAvailable < 100:
    sleep(2)  // back off when running low

if response.status == 429:
    sleep(retry_after_header or 2)
    retry once
```

### Performance estimate
- 2,500 orders × 3 API calls × 200ms = ~25 min sequential
- With 5 parallel workers: **~5 minutes**
- Skipping unchanged orders makes repeat runs even faster

---

## Part 4b: Generate RMFG Matrix Directly — Replace RMFG Translator Portal (P1 — Should Have)

**Problem:** After syncing to Shopify, I currently go to the RMFG Translator portal (translator.robbinsmfginc.com), filter by RMFG tag, download the matrix, and then manually reformat it:
- Rename tab `Worksheet` → `Access_LIVE`
- Insert `ProductionDay` column at position N with `SAT` or `TUE`
- Fix zip codes (leading zeroes lost), sort orders by OrderID ascending
- Auto-space columns for readability
- Merge with gift redemption sheet (gift orders sorted in with regular orders)
- Rename file to `AHB_WeeklyProductionQuery_MM-DD-YY_vF.xlsx`
- Validate all SKUs against MFG translations

This takes 20-30 minutes of tedious manual work every fulfillment day.

**Change:** The React tool should generate the RMFG matrix XLSX directly, skipping the portal entirely.

**What the tool already knows at this point:**
- Which orders have which child SKUs (from the allocation step)
- Order metadata (address, tags, phone, email) from Shopify
- Routing + gel tags (already on Shopify from cold chain app)
- MFG name translations (from the translations CSV — 227 entries mapping SKU → `AHB (S_REG): Product Name`)

**Output format (must match exactly):**
- Tab name: `Access_LIVE`
- Column layout:
  - Col A: OrderID (numeric, sorted ascending)
  - Col B: Name (customer name)
  - Col C: Distribution Type ("SHIPPING")
  - Col D: Total (item count)
  - Col E: Phone Number
  - Col F: Email
  - Col G: Address
  - Col H: Address 2
  - Col I: City
  - Col J: State
  - Col K: Zip (text format, leading zeroes preserved)
  - Col L: Tags (comma-separated Shopify tags)
  - Col M: Notes
  - Col N: ProductionDay (`SAT` or `TUE`)
  - Col O+: Product columns `AHB (S_REG): Product Name` with 1/0 values
- File name: `AHB_WeeklyProductionQuery_MM-DD-YY_vF.xlsx` (date = ship week tag date)
- Gift redemption orders included (they ship from RMFG regardless)
- All product column headers must match MFG translations exactly
- Columns auto-sized for readability

**MFG Translations mapping** (SKU → column header):
The canonical list is exported from https://translator.robbinsmfginc.com/ as a CSV (227 entries). Format: `SKU,"AHB (S_REG): Product Name"` — no header row. The tool should accept this CSV as input (updated weekly when new products onboarded).

### Pseudocode: Generate RMFG Matrix

Working Python implementation: `matrix_commander.py` function `generate_matrix_xlsx()`.

```
// Input: rmfg_tag, ship_day, ship_date, mfg_translations_csv

// Step 1: Load MFG translations
mfg_translations = {}  // sku -> "AHB (S_REG): Product Name"
for row in read_csv(mfg_translations_csv):
    mfg_translations[row[0]] = row[1]
// Example: { "CH-LEON": "AHB (S_REG): Leonora", "MT-TUSC": "AHB (S_REG): Toscano Salame" }

// Step 2: Fetch orders from Shopify by RMFG tag
orders = fetch_all_orders(tag: rmfg_tag, fields: "id,name,email,phone,tags,note,shipping_address,line_items")
// Need full address data — fetch with shipping_address field

// Step 3: Determine product columns
// Collect all food/packaging SKUs across all orders
all_skus = Set()
for order in orders:
    for li in order.line_items where li.fulfillable_quantity > 0:
        if li.sku starts with "CH-", "MT-", "AC-", "PK-", "TR-":
            all_skus.add(li.sku)

// Build column headers from MFG translations
product_columns = []  // [(sku, header_string)]
for sku in sorted(all_skus):
    header = mfg_translations[sku]  // e.g., "AHB (S_REG): Leonora"
    if header not found:
        WARN("SKU not onboarded at RMFG: " + sku)
    product_columns.append((sku, header))

// Step 4: Build XLSX
// Sheet name: "Access_LIVE"
// Row 1: headers
headers = ["OrderID", "Name", "Distribution Type", "Total", "Phone Number",
           "Email", "Address", "Address 2", "City", "State", "Zip",
           "Tags", "Notes", "ProductionDay"] + [col[1] for col in product_columns]

// Data rows (one per order)
rows = []
for order in orders:
    addr = order.shipping_address

    // Count food items
    food_count = sum(li.fulfillable_quantity for li in order.line_items
                     where li.sku starts with "CH-", "MT-", "AC-")

    // Build product assignment cells (1 or empty per column)
    order_skus = {li.sku: li.fulfillable_quantity for li in order.line_items
                  where li.fulfillable_quantity > 0}

    row = [
        int(order.name.replace("#", "")),  // OrderID as number
        addr.first_name + " " + addr.last_name,
        "SHIPPING",
        food_count,                         // Total
        addr.phone or order.phone,
        order.email,
        addr.address1,
        addr.address2 or "",
        addr.city,
        addr.province_code,                 // State as 2-letter code
        zero_pad(addr.zip, 5),              // Leading zeroes preserved
        order.tags,                         // Full comma-separated tag string
        order.note or "",
        ship_day,                           // "SAT" or "TUE"
    ]
    // Append product columns
    for (sku, _) in product_columns:
        row.append(order_skus.get(sku, None))  // 1 or empty

    rows.append(row)

// Sort by OrderID ascending
rows.sort(by: row[0])

// Auto-size columns
// File name: AHB_WeeklyProductionQuery_{ship_date}_vF.xlsx
// ship_date format: MM-DD-YY (this is the SHIP date — Monday for SAT, Tuesday for TUE)
```

**This is the highest-impact change** — it eliminates the RMFG portal dependency entirely and removes all manual reformatting.

---

## Part 5: Shortage Resolution with Swap Suggestions (P2 — Nice to Have)

When inventory check finds shortages, suggest swaps from these families:

| Family | SKUs | Notes |
|--------|------|-------|
| Brie | CH-TTBRIE, CH-TIP, CH-EBRIE, CH-PBRIE, CH-GPBRIE | Interchangeable bries |
| Alpine / Semi-hard | CH-BARI | Limited options currently |

**Rules:**
- Only suggest swaps within the same family
- Only suggest if the substitute has surplus (available > demand)
- Show surplus quantity
- Never suggest CH-MAFT — permanent exclusion list

**Bonus — restock awareness:**
Accept optional restocks input:
```csv
sku,restock_qty,expected_date
CH-TTBRIE,200,2026-04-02
```
For orders shipping AFTER the restock date, allocate against restocked quantities.

---

## Part 6: Gift Redemption Integration (P1 — Should Have)

**Problem:** Gift redemption orders are currently processed in a separate React tool, then manually merged with the main matrix.

**Change:** Process gift orders in the same run. The only difference:
- Gift orders get children assigned in the XLSX matrix output (RMFG ships them)
- Gift orders are EXCLUDED from Shopify sync (Shopify blocks all edits)
- Gift orders are EXCLUDED from the Matrixify CSV (if still using Matrixify)

This eliminates the separate tool and the manual sheet merge.

---

## What Already Exists as Python Prototypes (Replicate in React)

I've built working prototypes for everything below. The React tool should absorb ALL of this so a substitute operator uses ONE tool, not four.

| Capability | Python prototype | What it does | Replicate in React? |
|---|---|---|---|
| Inventory CSV input | Fulfillment app `export_inventory_csv` | Journal-replayed calculated inventory | **YES — accept this CSV** |
| Allocation + rotation | React tool (existing) | Child SKU → parent assignment | Already built |
| Shopify $0 variant sync | `matrix_commander.py sync-shopify` | Direct GraphQL orderEdit, replaces Matrixify | **YES — build this in** |
| RMFG matrix generation | `matrix_commander.py generate` | Fetch from Shopify, build Access_LIVE XLSX | **YES — build this in** |
| Pre-output validation | `matrix_commander.py validate` | 9 QC checks (zips, MFG names, fill, etc.) | **YES — build this in** |
| Shortage detection + swap suggestions | `matrix_commander.py check` | Demand vs inventory, substitution families | **YES — build this in** |
| Gift redemption merge | `matrix_commander.py finalize --gift` | Merge gift XLSX, sort, format | **YES — build this in** |
| MFG name validation | `mfg_translations.csv` check | Verify all SKUs onboarded at RMFG | **YES — build this in** |
| Depletion feedback | Fulfillment app `import_depletion_from_matrix` | Auto-feed depletions back to planning | **NICE TO HAVE — call API** |
| Tuesday projection | Fulfillment app `tuesday_projection` | Post-Saturday shortage forecast | **NICE TO HAVE — call API** |

**The Python prototypes are your spec.** The code is in:
- `AppyHour/matrix_commander.py` — CLI tool (~2100 lines) with all validation, sync, generate, swap, finalize logic
- `AppyHour/matrix_commander_web/` — Flask web UI (dark theme) showing how the UX should flow
- `AppyHour/mfg_translations.csv` — 227 MFG name translations from RMFG portal

**Goal for the substitute operator:** One React app. Open it. Pick the ship tag. Click Generate. Review. Click Send. Done in 15 minutes. No Matrix Commander, no fulfillment app, no cold chain app, no RMFG portal.

---

## Part 7: The Unified Operator Workflow

After all changes, this is what a Saturday morning looks like:

```
Step 1: Run cold chain app → routing + gel pack tags applied to Shopify orders
        (~10 min — could also be built into React tool eventually)

Step 2: Open the React fulfillment tool
        - Tool auto-loads inventory CSV from planning app API
          (http://localhost:5187/api/export_inventory_csv)
        - Select ship tag (e.g., RMFG_20260328)
        - Select production day (SAT)
        - Enter ship date (Monday date for SAT, Tuesday date for TUE)
        - Click "Generate"

Step 3: Tool runs the full pipeline:
        - Allocates child SKUs to parents (existing logic)
        - Syncs $0 variants to Shopify via GraphQL (replaces Matrixify)
        - Generates RMFG matrix XLSX directly (replaces RMFG portal)
        - Runs all validation checks
        - Merges gift redemption orders
        - Formats: Access_LIVE tab, ProductionDay, sorted, zips, auto-sized
        - Names file: AHB_WeeklyProductionQuery_MM-DD-YY_vF.xlsx

Step 4: Review the dashboard
        - Validation checks: all green? → proceed
        - Shortages: review swap suggestions, accept or adjust
        - Demand summary: spot-check totals

Step 5: Click "Send to RMFG"
        - Emails the final XLSX

Step 6: Email Production Matrix XLSX to RMFG
        (Attach file, send — 1 minute. Or auto-email if built in.)
```

**Total operator time: ~15-20 minutes.** No Shopify inventory upload. No manual shortage investigation. No separate gift tool. No sheet merging. No MFG name checking.

---

## Part 8: Error Scenarios for the Operator

| Scenario | Tool Behavior | Operator Action |
|----------|--------------|-----------------|
| Inventory CSV not provided | Tool blocks with message | Get CSV from planning tool or contact [name] |
| SKU not in MFG list | BLOCKS output | Contact [name] to onboard at RMFG |
| CEX-EC cheese missing | BLOCKS output | Check curation config, contact [name] |
| Shortage detected | WARNING + swap suggestions | Accept swaps or contact [name] |
| Duplicate child SKU | Skips order, logs it | Review logged orders after run |
| Gift orders found | Auto-processed, excluded from Shopify sync | No action needed |
| Shopify sync fails on some orders | Logs failures | Retry or fix manually |
| Cold chain app down | No routing/gel tags | DO NOT proceed — contact [name] |

---

## Reference: Current SKU Mappings

The complete product name → SKU mapping (96 entries, verified against Shopify March 2026) is in:
`AppyHour/AppyHourMCP/tools/constants.py`

The Python prototype that implements validation + inventory checking:
`AppyHour/matrix_commander.py`

---

## Priority Summary

| Change | Priority | Why |
|--------|----------|-----|
| Inventory CSV input | **P0** | Eliminates manual Shopify upload, root cause of bad allocations |
| Unify PR-CJAM + second pass | **P0** | Cuts upload time from 60+ min to 5 min — CEO's top concern |
| Demand summary output | **P0** | Enables pre-upload validation |
| Shopify sync (replace Matrixify) | **P1** | Direct GraphQL, 5-10x faster, built-in duplicate protection |
| Generate RMFG matrix directly | **P1** | Eliminates RMFG portal + all manual reformatting |
| Pre-output validation gate | **P1** | Catches errors before they hit Shopify/RMFG |
| Gift redemption merge | **P1** | Eliminates separate tool + manual merge — needed for vacation handoff |
| Swap suggestions | **P2** | Reduces shortage investigation from 60 min to 2 min |

## Questions for Developer

1. Can we do a 30-min walkthrough of the current React tool so I can show the exact workflow?
2. What's the turnaround on P0 changes?
3. The Python prototype (`matrix_commander.py`) has the validation logic implemented — do you want to reference it directly, or should I write pseudocode?
4. For Shopify sync: do you already have GraphQL order edit code in the React app, or would that be new?
