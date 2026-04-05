# Demand Pipeline Scenario Exploration — Summary

**Date:** 2026-04-05
**Scope:** `recharge_sync`, `shopify_sync`, `MONG projection`, `auto_deplete`, `compute_running_inventory`
**Iterations:** 25 | **Score:** 818
**Business Goal:** Never run out of inventory while keeping working capital growing

## Severity Breakdown

| Severity | Count | % |
|----------|-------|---|
| CRITICAL | 6 | 24% |
| HIGH | 10 | 40% |
| MEDIUM | 9 | 36% |
| **Total** | **25** | |

## Dimension Coverage (12/12 = 100%)

| Dimension | Scenarios | Key Finding |
|-----------|-----------|-------------|
| Error path | 2 | Zero charges → 404 with no fallback; API failures silently swallowed in background |
| Concurrent | 4 | No mutex on sync; STATE/settings race conditions; dual inventory truth |
| Temporal | 5 | Saturday boundary bug; cache staleness; MONG projection drops to 0 near Monday midnight |
| Data variation | 3 | Bare CEX-EC/MONTHLY drops demand; split rounding loses units; unknown box SKU silent fail |
| Integration | 2 | 429 retry insufficient; API version deprecation risk |
| State transition | 1 | Cold start restores stale data; fresh sync failure leaves stale as truth |
| Recovery | 3 | Crash re-depletes; no-snapshot journal goes negative; disk-full corrupts settings |
| Scale | 2 | Large charge set causes long sync window; unfulfilled fetch is O(n) unfiltered |
| Abuse/misuse | 1 | Rapid clicks cause concurrent syncs with no debounce |
| Permission | 1 | Token expiry mid-pagination discards partial data |
| Edge case | 2 | Missing _SHIP_ tag excludes demand; prcjam double-count after restart |
| Happy path | 1 | Acquisition/churn data collected but never used in forecasting |

## Top 6 Critical Scenarios (Stockout or Capital Risk)

### 1. Dual Inventory Source of Truth (#25)
`auto_deplete` modifies `STATE["rmfg_inventory"]` directly but doesn't write a journal entry. `compute_running_inventory()` replays the journal and returns different numbers. Matrix Commander and calculated_inventory endpoints see pre-depletion inventory.

**Impact:** Conflicting inventory data → missed shortage alerts → stockout
**Fix:** auto_deplete must write a journal entry for every depletion

### 2. save_settings Race Condition (#17)
Recharge sync saves settings twice (demand data + cache timestamp). The second save uses a stale object reference that predates Shopify sync's write. Shopify demand data is overwritten.

**Impact:** Lost Shopify demand on restart → under-order → stockout
**Fix:** Always `load_settings()` fresh before each `save_settings()` call

### 3. Bare CEX-EC + MONTHLY Curation Drops Demand (#4)
Large MONTHLY boxes with bare CEX-EC line items resolve curation to "MONTHLY", which is excluded from CEX-EC processing. ~40% of large MONTHLY boxes lose extra cheese demand.

**Impact:** Systematic under-ordering of CEX-EC cheeses → weekly shortages
**Fix:** Define a default CEX-EC assignment for MONTHLY curation, or resolve bare CEX-EC differently

### 4. Zero Queued Charges Returns 404, No Fallback (#1)
If all charges are outside the 28-day window, API returns empty. STATE demand keys are never written. On cold start, this means zero demand everywhere.

**Impact:** No cut order generated → complete stockout next Saturday
**Fix:** Return last-known-good demand with a "stale" flag instead of 404

### 5. Disk Full Corrupts Settings JSON (#22)
Non-atomic file write: if disk fills mid-write, JSON is truncated. Next load returns defaults. All historical data lost.

**Impact:** Total data loss — depletion history, journal, API tokens
**Fix:** Write to temp file first, then atomic rename

### 6. Shopify/Recharge Sync Race Condition (#2)
Background Recharge sync and foreground Shopify sync run concurrently. Shopify reads stale STATE, merges on top. Recharge finishes and overwrites, losing Shopify's additions.

**Impact:** Demand counts wrong in either direction → stockout or overstock
**Fix:** Shopify sync must wait for Recharge sync to complete (sequential pipeline)

## Acquisition & Churn Blindspot (#20)

The pipeline already computes `shopify_first_order_demand` and `shopify_recurring_demand` but **never uses them** in cut order or runway calculations. This means:

- **Growing subscriber base:** Cut order uses historical average → lags behind actual demand → chronic shortages during growth
- **Shrinking subscriber base:** Cut order over-orders → cheese expires → wasted capital
- **Promotional spikes:** 8-week rolling average smooths out surges → over-orders after promo ends

**Recommended signals to incorporate:**
1. First-order trend (week-over-week growth rate) → adjust MONG projection multiplier
2. Recurring vs first-order ratio → detect churn acceleration
3. Weighted recent average (exponential decay) → react faster to demand shifts
4. Recharge cancellation rate (available via API) → forecast demand decline

## Recommendations by Business Goal

### "Never Run Out of Inventory"
1. **Fix critical bugs first:** #25 (dual inventory), #17 (settings race), #4 (CEX-EC MONTHLY), #1 (zero charges)
2. **Add safety buffers:** Journal-based inventory with mandatory snapshot validation
3. **Sequentialize syncs:** Recharge → wait → Shopify → calculate. No concurrent STATE mutations
4. **Atomic settings writes:** Write-then-rename pattern prevents corruption

### "Decisions Made For Me"
1. **Use acquisition/churn signals (#20):** Auto-adjust demand forecast based on subscriber growth rate
2. **Trend-aware averaging (#21):** Replace flat 8-week average with exponential weighted average
3. **Auto-reorder triggers:** When runway < 2 weeks, auto-generate PO draft
4. **Anomaly detection:** Flag when demand deviates >20% from trend → pause auto-order, alert operator
5. **Self-healing syncs:** If sync fails, retry with exponential backoff; if stale >4h, force re-sync and alert
