# Developer Request: Inventory CSV Input for Fulfillment Tool

## Problem

The current workflow requires me to **manually upload inventory levels to Shopify** before each fulfillment run. This is the biggest bottleneck in the weekly process and introduces errors that cascade through the entire pipeline.

### What I Do Today (Every Saturday & Tuesday)

1. Get inventory numbers from our planning system (cut orders + intakes - depletions)
2. For each child SKU (e.g., CH-LEON), there are **two Shopify products**: a paid variant and a $0 variant
3. I set "On Hand" for the **paid variant** first
4. Then take whatever Shopify reports as still "Available" and set that as "On Hand" for the **$0 variant**
5. This takes 30-60 minutes and is error-prone
6. The fulfillment tool reads these Shopify inventory levels to make allocation decisions

### Why This Causes Problems

1. **No ship-week awareness.** If 50 units of CH-LEON are committed to next week's orders on Shopify, the tool sees them as unavailable — even though I'm getting a restock before then. The tool thinks we're short when we're not.

2. **Paid vs $0 splitting is fragile.** The $0 variant inventory depends on what's left after the paid variant, which depends on what Shopify has committed. One wrong number cascades.

3. **Restocks aren't factored in.** If I know 200 units of CH-TTBRIE arrive Thursday, next week's orders should be fine. But Shopify doesn't know about future restocks, so the tool avoids allocating that SKU.

4. **Late/missing snapshots.** Sometimes the RMFG inventory snapshot doesn't arrive on time. I still need to run fulfillment, so I estimate — and estimates lead to bad allocations that need manual fixing later.

**The result:** I spend 1-2 extra hours after each fulfillment run fixing shortages and swaps that wouldn't exist if the tool had accurate inventory data to begin with.

---

## What I Need Changed

### 1. Accept an Inventory CSV as Input

Instead of reading Shopify inventory levels, the tool should accept a CSV file with available quantities:

```csv
sku,available_qty
CH-LEON,342
CH-TTBRIE,298
CH-BARI,156
CH-MCPC,487
MT-LONZ,501
MT-TUSC,388
AC-DTCH,540
AC-PRPE,612
AC-TCRISP,445
...
```

**Rules:**
- If a SKU is in the CSV, use that quantity for allocation decisions
- If a SKU is NOT in the CSV, treat it as 0 (don't fall back to Shopify inventory)
- The tool should validate that all SKUs it needs are present in the CSV and warn if any are missing

I will generate this CSV from my planning tools, which calculate real availability:
`last confirmed inventory + cut order yields + expected intakes - depletions`

This number is accurate, accounts for restocks, and doesn't have the paid/$0 splitting problem.

### 2. Return a Demand Summary

After the tool runs allocation, output a summary of what it allocated:

```csv
sku,total_allocated,parent_breakdown
CH-LEON,197,"CEX-EC-MONG: 197"
CH-MCPC,315,"CEX-EC-MDT: 273, CEX-EC-OWC: 42"
CH-TTBRIE,312,"AHB-MED: 245, AHB-LGE: 67"
MT-TUSC,388,"AHB-MED: 312, AHB-LGE: 76"
AC-DTCH,540,"AHB-MED: 432, AHB-LGE: 108"
PK-TCUST,2357,"AHB-MED: 1847, AHB-LGE: 510"
...
```

This lets me cross-check total demand against my inventory before uploading anything. If I see a problem, I can fix it before it hits Shopify — not after.

### 3. Keep the Matrixify Upload CSV Format the Same

No changes needed to the output format. The same CSV structure works:

```
Line: Product ID, Line: Product Handle, Line: Command, Line: Quantity, Line: Type, Command, Name, ID, product_id, product_name, child_sku, parent_sku
```

I'm building a tool on my end that will process this output (validate, check for issues, and sync to Shopify). The upload format stays the same.

---

## Bonus (If Feasible)

### Restock-Aware Allocation

Accept an optional restocks CSV:

```csv
sku,restock_qty,expected_date
CH-TTBRIE,200,2026-04-02
CH-LEON,150,2026-04-03
MT-ASPK,75,2026-04-01
```

**Logic:** For orders shipping AFTER the restock date, the tool can allocate against restocked quantities. For orders shipping before, only use current available.

This would eliminate 90% of "false shortage" situations where I know product is coming but the tool doesn't.

### Gift Redemption in the Same Tool

Currently gift redemption orders are processed in a separate React tool. If the main tool could handle gift orders in the same run (flagging them as "Shopify-uneditable" but still generating the matrix assignments), it would eliminate a separate processing step and a manual sheet merge on my end.

---

## Summary of Changes

| Change | Priority | Impact |
|--------|----------|--------|
| Accept inventory CSV input | **Must have** | Eliminates manual Shopify inventory upload, fixes root cause of bad allocations |
| Return demand summary CSV | **Must have** | Enables pre-upload validation, catches problems early |
| Restock-aware allocation | Nice to have | Eliminates false shortages from upcoming deliveries |
| Gift redemption in same tool | Nice to have | Eliminates separate app + manual merge |

## Questions for You

1. How does the tool currently read Shopify inventory? (API call, cached, real-time?) — so I know what code path to replace
2. Is the inventory check per-SKU or does it also consider per-location? (We only care about the RMFG TX location)
3. Can the CSV input be a command-line argument or does it need to be in the UI? Either works for me
4. For the demand summary — can it be a separate file output, or does it need to be in the main export?
