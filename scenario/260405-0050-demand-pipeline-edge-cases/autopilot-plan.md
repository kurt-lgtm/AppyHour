# Autopilot Forecasting Plan

**Goal:** Never run out of inventory. Decisions are made automatically.

## What You Already Have (unused signals)

| Signal | Where It Lives | Current Status |
|--------|---------------|----------------|
| `shopify_first_order_demand` | settings JSON | Computed weekly, never consumed |
| `shopify_recurring_demand` | settings JSON | Computed weekly, never consumed |
| `first_order_count_3d` | shopify_sync | Only used for MONG projection |
| `shopify_weekly` per-SKU avg | settings JSON | Used for runway, but flat 8-week average |
| Recharge queued charges | STATE | Used for cut order, but snapshot-in-time only |
| `depletion_history` | settings JSON | Logged but not used for trend analysis |

## Phase 1: Trend-Aware Demand (replace flat average)

**Problem:** 8-week flat average masks growth, decline, and seasonal shifts.

**Fix:** Exponential weighted moving average (EWMA) with configurable decay.

```python
# In shopify_sync, replace flat average with EWMA
# alpha = 0.3 means recent weeks weighted 3x more than old weeks
def ewma_weekly(week_sku_totals, alpha=0.3):
    """Exponential weighted moving average per SKU."""
    sorted_weeks = sorted(week_sku_totals.keys())  # oldest first
    ewma = defaultdict(float)
    for wk in sorted_weeks:
        for sku, qty in week_sku_totals[wk].items():
            if sku not in ewma:
                ewma[sku] = float(qty)
            else:
                ewma[sku] = alpha * qty + (1 - alpha) * ewma[sku]
    return {sku: round(val) for sku, val in ewma.items()}
```

**Impact:** Reacts 2-3x faster to demand shifts. Holiday surge fades in 3 weeks instead of 8.

**Config:** `shopify_ewma_alpha` in settings (default 0.3, range 0.1-0.5).

## Phase 2: Acquisition/Churn Growth Multiplier

**Problem:** Cut order is based on current queued charges — no forward-looking signal.

**Fix:** Track week-over-week first-order trend as a growth rate, apply as multiplier.

```python
# New: compute growth rate from historical first_order_demand
def compute_growth_multiplier(settings):
    """Week-over-week first-order growth rate as demand multiplier."""
    history = settings.get("first_order_weekly_history", [])
    if len(history) < 3:
        return 1.0  # not enough data
    
    # Last 4 weeks of first-order counts
    recent = history[-4:]
    if recent[0] == 0:
        return 1.0
    
    # Compound weekly growth rate
    growth_rate = (recent[-1] / recent[0]) ** (1.0 / (len(recent) - 1))
    
    # Clamp to prevent wild swings: 0.85 to 1.20 (±15-20%)
    return max(0.85, min(1.20, growth_rate))
```

**Where to apply:** Multiply `rmfg_sat_demand` by growth_multiplier before cut order generation.

**Data collection:** Each shopify_sync appends `sum(first_order_total.values())` to `first_order_weekly_history` list.

## Phase 3: Auto-Reorder Triggers

**Problem:** Operator must manually check runway and decide to order.

**Fix:** When runway drops below threshold, auto-generate PO draft and alert.

```
For each SKU where runway_weeks < reorder_threshold:
    1. Calculate deficit = (demand_weekly * safety_weeks) - available
    2. Round up to vendor case_qty (from vendor_catalog)
    3. Create draft PO entry in settings["auto_po_drafts"]
    4. If Slack webhook configured: send alert with PO summary
    5. If auto_po_threshold > 0 and deficit > threshold: auto-submit PO
```

**Thresholds (configurable):**
- `reorder_runway_weeks`: 2 (trigger when <2 weeks of stock)
- `safety_weeks`: 1 (extra week buffer in PO qty)
- `auto_po_threshold`: 0 (default off — draft only until operator trusts it)

## Phase 4: Anomaly Detection + Self-Healing

**Problem:** Demand spikes/drops from promos, outages, or data bugs go undetected.

**Fix:** Flag when current demand deviates >20% from EWMA trend.

```
anomaly_ratio = abs(current_week - ewma_prediction) / ewma_prediction
if anomaly_ratio > 0.20:
    FLAG as anomaly
    if anomaly_ratio > 0.50:
        PAUSE auto-reorder for this SKU
        ALERT operator: "Demand anomaly detected on {sku}: {current} vs expected {ewma}"
```

**Self-healing syncs:**
- If recharge_sync fails 2x in a row: force retry with 60s backoff
- If data age > 4 hours on a Wednesday: force re-sync and Slack alert
- If inventory goes negative after depletion: flag as "stale snapshot" warning

## Phase 5: Churn Detection via Recharge

**Problem:** No signal for subscriber churn rate.

**Fix:** Use Recharge API to track cancellation velocity.

```
# New endpoint: /api/churn_signal
# Pulls recent cancellations from Recharge API
# GET /subscriptions?status=cancelled&updated_at_min={7_days_ago}
# Compare cancel count vs active count = weekly churn rate
# If churn_rate > 5%: reduce demand forecast by churn_rate
# If churn_rate < 2%: boost forecast by acquisition surplus
```

**Frequency:** Weekly (Sunday night auto-run), results cached in settings.

## Implementation Priority

| Phase | Effort | Impact on Stockout Prevention | Impact on Working Capital |
|-------|--------|-------------------------------|--------------------------|
| 1. EWMA | Small (1 function) | Medium — faster reaction to shifts | High — less seasonal overstock |
| 2. Growth multiplier | Small (1 function + history tracking) | High — anticipates demand changes | High — right-sizes orders |
| 3. Auto-reorder | Medium (PO draft + Slack alert) | Very High — removes human delay | Medium — faster reorder = less safety stock needed |
| 4. Anomaly detection | Medium (monitoring + alerts) | High — catches data bugs before they become stockouts | Medium — prevents panic over-orders |
| 5. Churn detection | Medium (new API call + integration) | Medium — adjusts for decline | High — prevents overstock during churn |

**Recommended order:** 1 → 2 → 3 → 4 → 5

Phases 1+2 together give you the biggest bang: trend-aware demand + growth signal = the pipeline knows where demand is GOING, not just where it's BEEN. Phase 3 makes it truly autopilot.

## Phase 6: New Cheese Ramp-Up Buffer

**Business model:** Set curations (MONG, MDT, OWC, etc.) with continuous new cheese rotation. New cheeses replace old ones in curation recipes periodically.

**Problem:** When a new cheese SKU enters rotation, it has zero historical demand. EWMA returns 0. Growth multiplier has nothing to multiply. The pipeline orders nothing — stockout on day 1.

**Two demand patterns:**

| Pattern | Examples | Forecasting Approach |
|---------|----------|---------------------|
| Stable curations | MONG, MDT, SPN (months of history) | EWMA — smooth, reactive, accurate |
| New cheese introductions | New CH- replacing old CH- in a recipe | Inherit demand from replaced SKU, blend over 3 weeks |

**Fix: Inherited demand with fade-in blend.**

- **Week 0 (launch):** 100% of old cheese's demand — order full quantity
- **Week 1:** 67% inherited + 33% actual
- **Week 2:** 33% inherited + 67% actual
- **Week 3+:** 100% actual EWMA — fully autonomous

**Operator workflow:** When rotating a cheese, set `sku_ramp[new_sku] = {replaces: old_sku, intro_date: "2026-04-12", ramp_weeks: 3}`. Pipeline auto-inherits demand. After 3 weeks, ramp entry is inert.

**Auto-detection (future):** When `curation_recipes` changes (new SKU replaces old in a slot), auto-create ramp entry from the diff.

**Curation-Level Demand Floor:** Even without ramp config, set a floor: minimum demand for any SKU = sum of boxes across all curations containing it. Never order less than what curations physically require.

**Config:**

| Key | Type | Default | Purpose |
|-----|------|---------|---------|
| `sku_ramp` | dict | {} | Per-SKU ramp config: {replaces, intro_date, ramp_weeks} |
| `demand_floor_enabled` | bool | true | Use curation recipe to floor demand |
| `auto_ramp_on_recipe_change` | bool | false | Auto-create ramp entries on recipe updates |

## Config Keys (all in settings JSON)

| Key | Type | Default | Purpose |
|-----|------|---------|---------|
| `shopify_ewma_alpha` | float | 0.3 | EWMA decay factor (higher = more reactive) |
| `first_order_weekly_history` | list[int] | [] | Weekly first-order totals for growth calc |
| `growth_multiplier_enabled` | bool | true | Apply growth multiplier to demand |
| `growth_multiplier_clamp` | [float, float] | [0.85, 1.20] | Min/max growth multiplier |
| `reorder_runway_weeks` | float | 2.0 | Auto-reorder trigger threshold |
| `safety_weeks` | float | 1.0 | Extra buffer in auto-PO quantity |
| `auto_po_enabled` | bool | false | Auto-submit POs (vs draft only) |
| `anomaly_threshold_pct` | float | 0.20 | Demand anomaly detection threshold |
| `anomaly_pause_threshold_pct` | float | 0.50 | Pause auto-reorder on extreme anomaly |
| `churn_check_enabled` | bool | false | Enable weekly churn detection |
