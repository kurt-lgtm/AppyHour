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

**Problem:** Matrixify upload is slow for 2,500+ orders and requires a separate manual step. After upload, I often need to fix orders via Shopify API anyway.

**Change:** Build direct Shopify sync into the React tool using the GraphQL order edit API.

**Logic per order:**
1. Fetch current line items from Shopify
2. Compare against matrix assignments
3. For each child SKU to add:
   - If SKU already on order with qty > 0 → **skip** (duplicate protection)
   - Otherwise → add as $0 variant via `orderEditBegin` → `orderEditAddVariant` → `orderEditCommit`
4. Only touch orders that need changes (skip unchanged = faster)
5. Skip gift redemption orders (Shopify blocks edits on them)

**Duplicate protection** is the key feature from Matrixify we must preserve. Two modes:
- **Conservative:** If ANY SKU would duplicate, skip entire order (matches Matrixify behavior)
- **Smart:** Skip only the duplicate SKU, add the rest

**Rate limiting:** 5-10 concurrent requests with backoff on 429s.

**Output:** Sync report showing: X updated, Y skipped (already correct), Z rejected (duplicates).

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
The canonical list is exported from https://translator.robbinsmfginc.com/ as a CSV. The tool should either:
- Accept this CSV as input (updated weekly when new products onboarded), or
- Pull it from the portal API if available

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

## What the React Tool Does NOT Need to Build

The following capabilities already exist in our planning tools and should NOT be duplicated:

| Capability | Already handled by | How React tool uses it |
|---|---|---|
| Inventory calculation | Fulfillment app (journal-replayed ledger) | Accept inventory CSV as input |
| Tuesday projection | Fulfillment app (`/api/tuesday_projection`) | Not needed — planning app shows this |
| Depletion tracking | Fulfillment app (`/api/import_depletion_from_matrix`) | Not needed — Matrix Commander feeds depletions back |
| Shopify $0 variant sync | Matrix Commander (`sync-shopify`) | Just output the allocation CSV |
| RMFG matrix generation | Matrix Commander (`generate`) | Not needed — MC builds the XLSX from Shopify |
| MFG name validation | Matrix Commander (uses `mfg_translations.csv`) | Not needed — MC validates before sending |
| Gift sheet merging | Matrix Commander (`finalize --gift`) | Not needed — MC handles merge |

**The React tool's scope is focused:** Accept inventory CSV → run allocation logic (rotation, no repeats, adjacency) → output assignment CSV + demand summary. Everything else is handled.

---

## Part 7: The Unified Operator Workflow

After all changes, this is what a Saturday morning looks like:

```
Step 1: Run cold chain app → routing + gel pack tags applied to Shopify orders
        (~10 min, unchanged)

Step 2: Download inventory CSV from fulfillment planning app
        (http://localhost:5187/api/export_inventory_csv — one click)

Step 3: Open the React fulfillment tool
        - Upload inventory CSV from Step 2
        - Select ship tag (e.g., RMFG_20260328)
        - Select production day (SAT)
        - Click "Generate"

Step 4: Review allocation output
        - Demand summary: spot-check totals look reasonable
        - Download the assignment CSV

Step 5: Open Matrix Commander (http://localhost:5188)
        - Click "Generate from Shopify" with RMFG tag
        - Tool syncs $0 variants to Shopify directly (replaces Matrixify)
        - Tool generates RMFG matrix XLSX directly (replaces RMFG portal)
        - Validates: MFG names, CEX-EC, parent fill, zips, sort
        - Gift orders included in matrix, excluded from Shopify sync
        - File named: AHB_WeeklyProductionQuery_MM-DD-YY_vF.xlsx

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
| Demand summary output | **P0** | Enables pre-upload validation |
| Pre-output validation gate | **P1** | Catches errors before they hit Shopify/RMFG |
| Shopify sync (replace Matrixify) | **P1** | Eliminates slow upload + post-fix cycle |
| Generate RMFG matrix directly | **P1** | Eliminates RMFG portal + all manual reformatting |
| Swap suggestions | **P2** | Reduces shortage investigation from 60 min to 2 min |
| Gift redemption merge | **P1** | Eliminates separate tool + manual merge — needed for vacation handoff |

## Questions for Developer

1. Can we do a 30-min walkthrough of the current React tool so I can show the exact workflow?
2. What's the turnaround on P0 changes?
3. The Python prototype (`matrix_commander.py`) has the validation logic implemented — do you want to reference it directly, or should I write pseudocode?
4. For Shopify sync: do you already have GraphQL order edit code in the React app, or would that be new?
