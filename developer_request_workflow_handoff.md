# Developer Request: Unified Fulfillment Tool

## Context

The weekly fulfillment process currently requires 2-3 hours of manual work between the React tool generating its output and the final matrix being emailed to RMFG. Most of that time is spent on inventory checks, shortage resolution, Shopify syncing (via Matrixify), gift redemption processing, QC validation, and sheet merging.

**The goal: Make the React tool the single unified pipeline that handles everything.** One tool, one run, one output. An operator with minimal training should be able to run the full Saturday fulfillment in under 30 minutes.

I built a Python prototype (`matrix_commander.py`) that implements the validation and inventory checking logic. This document describes what needs to be built into the React tool, using the prototype as a spec. The prototype is available for reference but should NOT be a separate tool — everything below should live in the React app.

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
- Tab name must be `Access_LIVE`
- Column N (14) must be `ProductionDay` with `SAT` or `TUE`
- OrderIDs must be numeric (no `#`, no commas)
- Zip codes must preserve leading zeroes (text format)
- No duplicate product columns
- Product column headers: `AHB (S_REG): Product Name`

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

## Part 6: Gift Redemption Integration (P2 — Nice to Have)

**Problem:** Gift redemption orders are currently processed in a separate React tool, then manually merged with the main matrix.

**Change:** Process gift orders in the same run. The only difference:
- Gift orders get children assigned in the XLSX matrix output (RMFG ships them)
- Gift orders are EXCLUDED from Shopify sync (Shopify blocks all edits)
- Gift orders are EXCLUDED from the Matrixify CSV (if still using Matrixify)

This eliminates the separate tool and the manual sheet merge.

---

## Part 7: The Unified Operator Workflow

After all changes, this is what a Saturday morning looks like:

```
Step 1: Run cold chain app → routing + gel pack tags applied to Shopify orders
        (~10 min, unchanged)

Step 2: Download inventory CSV from planning tool
        (Export button, save file — 30 seconds)

Step 3: Open the React fulfillment tool
        - Upload inventory CSV
        - Select ship tag (e.g., RMFG_20260328)
        - Select production day (SAT)
        - Click "Generate"

Step 4: Review the pre-output dashboard
        - Validation checks: all green? → proceed
        - Shortages: review swap suggestions, accept or adjust
        - MFG name missing? → STOP, contact [name] to onboard SKU
        - Demand summary: spot-check totals look reasonable

Step 5: Click "Run" (or "Generate & Sync")
        - Tool generates Matrixify CSV + Production Matrix XLSX
        - Tool syncs $0 variants to Shopify directly (or uploads via Matrixify)
        - Gift orders processed and merged automatically
        - Final XLSX validated and saved

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
| Swap suggestions | **P2** | Reduces shortage investigation from 60 min to 2 min |
| Gift redemption merge | **P2** | Eliminates separate tool + manual merge |

## Questions for Developer

1. Can we do a 30-min walkthrough of the current React tool so I can show the exact workflow?
2. What's the turnaround on P0 changes?
3. The Python prototype (`matrix_commander.py`) has the validation logic implemented — do you want to reference it directly, or should I write pseudocode?
4. For Shopify sync: do you already have GraphQL order edit code in the React app, or would that be new?
