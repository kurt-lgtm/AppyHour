# Elevate Foods — Business Synthesis & Autopilot Roadmap

**Date:** 2026-04-05
**Sources:** Recharge API (13,489 subs), Shopify orders, Slack (#core-team, #product, #ads-team, #inventoryandforecasting, #reship-and-order-requests), scenario analysis (25 failure modes), codebase review (app.py demand pipeline)

---

## 1. The Business Today

### What You Sell
Artisan cheese & charcuterie subscription boxes. Two sizes (MED 7-item, LGE 15-item) across 11+ curations. Recently revamped to **7-item customizable box** model — customers choose a curation track that determines their recipe each month.

### Subscription Base: 13,489 Active

| Segment | Subs | % | Demand Pattern |
|---------|------|---|----------------|
| **Set curations** (MDT, SPN, OWC, HHIGH, ALPN, ISUN, MONG) | ~8,700 | 65% | Stable — same recipe until rotation. EWMA forecasting works. |
| **Monthly curations** (MS, NMS, MED, LGE, CMED) | ~4,200 | 31% | New recipe every month. Need ramp-up buffer for new SKUs. |
| **Dietary variants** (NNRS, NCRS, CORS) | ~200 | 1.5% | Restricted swap pool. Excluded from auto-substitution. |
| **BYO / Tray / Other** | ~400 | 3% | Custom — unpredictable, use historical average. |

### Top 5 Curations by Volume

| Curation | MED | LGE | Total | Monthly Recipe? |
|----------|-----|-----|-------|----------------|
| MDT | 2,027 | 1,200 | 3,227 | No (set) |
| SPN | 1,127 | 500 | 1,627 | No (set) |
| OWC | 974 | 397 | 1,371 | No (set) |
| HHIGH | 881 | 455 | 1,336 | No (set) |
| MS | 347 | 474 | 821 | **Yes (monthly)** |

Plus ~3,000 subscriptions on legacy numeric variant IDs — likely monthly boxes not yet migrated to curation-suffix SKU format. These are a forecasting blind spot.

### Revenue Drivers
- ~200 new signups/week at ~$83 CPA via Meta ads
- Seasonal/limited-release boxes (Valentine's, Easter, St. Patrick's) drive spikes
- Upsell: AHB-X specialty one-time boxes (Mediterranean Escape, Savor Spain, Holiday Brunch)
- Reships and credits cost ~$20 per incident

### The Team
| Person | Role | Current Pain |
|--------|------|-------------|
| **Kurt** | Inventory/fulfillment ops | Manually decides swaps, reships, cut orders every week. Reactive. |
| **Dan** | Procurement | Discovers shortages via Slack DMs days before ship. No early warning. |
| **Tommy** (new) | Director of Product | Will accelerate cheese rotation — amplifies forecasting challenge. |
| **Michelle** | Product/Marketing | Scales ad budget without inventory signal. Seasonal box launches cause OOS. |
| **RMFG** | Fulfillment center | Receives cut order, cuts/portions/packs. Needs accurate POs 3-5 days ahead. |

---

## 2. What's Broken (Root Causes)

### A. Demand Pipeline Has 6 Critical Bugs
Found via scenario analysis. 4 fixed tonight, 2 remaining:

| # | Bug | Status | Impact |
|---|-----|--------|--------|
| 25 | auto_deplete doesn't write journal entries → dual inventory truth | **FIXED** | Conflicting inventory numbers |
| 17 | save_settings race condition → data loss on restart | **FIXED** | Lost demand data |
| 4 | Bare CEX-EC + MONTHLY drops demand silently | **FIXED** | Under-orders extra cheese |
| 1 | Zero charges returns 404 instead of cached data | **FIXED** | No demand → no cut order |
| 10 | MONG projection 7x inflation on Monday nights | TODO | Massive over-order of MONG cheese |
| 11 | CEX-EC split int() truncation → zero demand for qty=1 | TODO | Chronic shortage on split cheeses |

### B. Forecasting Pipeline Is Broken
`forecast_demand` and `get_reorder_alerts` MCP tools throw `AttributeError: 'float' object has no attribute 'get'`. A config entry has a float where a dict is expected. **No automated alerts are available until this is fixed.**

### C. No Forward-Looking Signals
| Signal | Available? | Used? |
|--------|-----------|-------|
| Queued charges (next 4 weeks) | Yes | Yes — but snapshot-in-time only |
| First-order trend (acquisition) | Computed | **No** — saved but never consumed |
| Recurring vs first-order split | Computed | **No** |
| Ad spend changes | In Slack only | **No** — marketing → demand disconnect |
| Churn/cancellation rate | Available via API | **No** |
| Supplier lead times | In Dan's head | **No** — not in system |
| Seasonal calendar | In Michelle's head | **No** — no promo demand buffer |

### D. Operational Gaps (from Slack)
- **Shortages are discovered reactively** — Dan finds out via Slack DMs, not dashboards
- **Substitutions anger customers** — unauthorized swaps (Valdeón Blue, Petit Boo) create CS tickets
- **Seasonal boxes are chaos** — Valentine's, Easter boxes introduce new SKUs with zero forecast buffer
- **Overstock happens too** — 10K insulation order, CH-MAU3 "start dumping"
- **~42 of 80 cheese SKUs at zero inventory** — unclear which are discontinued vs active demand

---

## 3. The Vision: "Never Stockout, Decisions Made For Me"

### What "Autopilot" Means (revised per your feedback)

**Not full autopilot yet.** The path is:

```
TODAY                    PHASE 1-2              PHASE 3-4              FUTURE
Manual everything  →  Dashboard + alerts  →  PO drafts + approval  →  Full auto
Kurt decides       →  System warns        →  System suggests       →  System acts
```

**Phase 1-2 goal:** Dashboard that shows the truth. Alerts that warn 2 weeks ahead. Kurt and Dan never get blindsided again.

**Phase 3-4 goal:** System drafts POs. Dan reviews and clicks "approve." Cut order auto-generates with correct quantities. Swaps suggested with one-click apply.

---

## 4. The Roadmap

### Phase 0: Fix What's Broken (immediate — this session or next)

| Task | Effort | Impact |
|------|--------|--------|
| Fix forecast_demand AttributeError | Small | Unblocks all forecasting tools |
| Fix MONG projection Monday night 7x inflation | Small | Prevents weekly over-order |
| Fix CEX-EC split int() truncation | Small | Fixes chronic split cheese shortage |
| Audit 42 zero-inventory SKUs (active vs discontinued) | Medium | Accurate inventory baseline |
| Resolve 3,000 legacy numeric variant IDs | Medium | 22% of subs are a forecasting blind spot |

### Phase 1: Curation-Floor Dashboard (1-2 sessions)

**The single most impactful change.** Every SKU in an active recipe gets a demand floor = subscription count × qty per box.

| What | How |
|------|-----|
| Floor calculation | `floor[sku] = sum(boxes_per_curation[c] * recipe[c][sku] for c in curations)` |
| Dashboard view | SKU → Available → Floor Demand → Runway → Status (red/amber/green) |
| Alert threshold | Red when available < floor × 1.5 (1.5 weeks buffer) |
| Data sources | `curation_recipes` + subscription counts from Recharge API |

**With your real numbers:**
- MDT recipe includes CH-EBRIE → floor = 3,227 units
- If only 2,000 CH-EBRIE on hand → RED alert, 0.6 weeks runway
- This would have caught **every** Slack shortage before it became urgent

**For monthly curations (MS, NMS, MED, LGE, CMED):**
- Floor recalculates when next month's recipe is entered
- Before recipe is set: use previous month's floor as estimate
- After recipe is set: exact floor from new SKU assignments

### Phase 2: Trend-Aware Forecasting (1 session)

Replace flat 8-week average with EWMA. Add growth multiplier from first-order trend.

| Component | What It Does |
|-----------|-------------|
| EWMA (α=0.3) | Recent weeks weighted 3x more than old. Reacts to demand shifts in 2-3 weeks instead of 8. |
| Growth multiplier | Week-over-week first-order trend. If acquisition up 10% → demand forecast bumped 10%. Clamped ±20%. |
| Final demand | `max(curation_floor, ewma * growth_multiplier)` — floor guarantees minimum, forecast captures growth. |

**Impact on working capital:** Less seasonal overstock (EWMA fades holiday spike in 3 weeks vs 8). Less shortage-driven emergency orders (growth multiplier anticipates demand).

### Phase 3: PO Draft Generator (1-2 sessions)

**Dan's autopilot.** System generates weekly PO draft every Monday.

| Feature | Detail |
|---------|--------|
| Trigger | `runway_weeks < 2.0` for any SKU |
| PO quantity | `(weekly_demand * safety_weeks) - available`, rounded up to `vendor_catalog.case_qty` |
| Output | Draft PO in dashboard with vendor, SKU, qty, estimated cost |
| Notification | Slack message to Dan: "3 SKUs need POs this week — review in dashboard" |
| Approval | One-click approve in dashboard → marks PO as submitted |

**Stretch:** Auto-detect when ad budget scales (if Michelle posts in #ads-team about spend increase → flag as demand surge risk → bump safety buffer for 2 weeks).

### Phase 4: Auto-Ramp on Cheese Rotation (1 session)

When Tommy/Michelle update next month's recipes:

| Step | What Happens |
|------|-------------|
| Recipe diff | System compares this month's recipe to next month's. Detects new SKUs. |
| Demand inheritance | New CH-NEW replaces CH-OLD in MDT slot → CH-NEW inherits CH-OLD's demand (3,227 units) |
| Ramp blend | Week 1: 100% inherited. Week 2: 67/33. Week 3: 33/67. Week 4+: 100% actual. |
| PO draft | New cheese auto-appears in Monday's PO draft with correct quantity. |
| Floor guarantee | Even if ramp calc is wrong, curation floor catches it. |

**This is what makes Tommy's job possible.** He can rotate cheeses aggressively without creating supply chain chaos.

### Phase 5: Anomaly Detection & Churn (future)

| Feature | Trigger | Action |
|---------|---------|--------|
| Demand anomaly | Current week >20% off EWMA prediction | Flag in dashboard, alert Kurt |
| Extreme anomaly | >50% deviation | Pause auto-PO for that SKU, require manual review |
| Churn detection | Weekly Recharge cancellation count | If churn > 5%: reduce forecast. If < 2%: boost. |
| Self-healing sync | Recharge/Shopify sync fails 2x | Auto-retry with 60s backoff, Slack alert if still failing |
| Stale data guard | Demand data > 4 hours old on Wednesday | Force re-sync, block cut order export until fresh |

---

## 5. How It All Connects

```
                    ┌─────────────────────────────────┐
                    │     SUBSCRIPTION BASE (13,489)   │
                    │  Recharge API → queued charges    │
                    │  Shopify API → unfulfilled orders │
                    └────────────┬────────────────────┘
                                 │
                    ┌────────────▼────────────────────┐
                    │      DEMAND PIPELINE (fixed)     │
                    │  recharge_sync → 4-week demand   │
                    │  shopify_sync → EWMA + growth    │
                    │  MONG projection (fixed)         │
                    │  CEX-EC splits (fixed)           │
                    └────────────┬────────���───────────┘
                                 │
              ┌──────────────────┼──────────────────────┐
              │                  │                       │
   ┌──────────▼───────┐  ┌──────▼──────────┐  ┌────────▼────────┐
   │  CURATION FLOOR   │  │  EWMA FORECAST  │  │  GROWTH MULT    │
   │  recipe × subs    │  │  trend-aware    │  │  acquisition    │
   │  = minimum demand │  │  weighted avg   │  │  signal         │
   └──────────┬───────┘  └──────┬──────────┘  └────────┬────────┘
              │                  │                       │
              └──────────────────┼───────────────────────┘
                                 │
                    ┌────────────▼────────────────────┐
                    │  FINAL DEMAND = max(floor, ewma) │
                    │  × growth_multiplier             │
                    └────────────┬──────���─────────────┘
                                 │
              ┌──────────────────┼──────────────────────┐
              │                  │                       │
   ┌──────────▼───────┐  ┌──────▼──────────┐  ┌────────▼────────┐
   │  DASHBOARD        │  │  PO DRAFTS      │  │  AUTO-RAMP      │
   │  runway + alerts  │  │  for Dan        │  │  for Tommy      │
   │  for Kurt         │  │  weekly Monday  │  │  on rotation     │
   └──────────────────┘  └─────────────────┘  └────��────────────┘
                                 │
                    ┌────────────▼────────────────────┐
                    │  INVENTORY JOURNAL (single truth) │
                    │  snapshot → depletions → calc     │
                    │  auto_deplete writes entries ✓    │
                    └─────────────────────────────────┘
```

---

## 6. Success Metrics

| Metric | Current (estimated) | Target | How to Measure |
|--------|-------------------|--------|----------------|
| Weekly shortage incidents | 2-4 per week (from Slack) | 0 | Count "short", "OOS", "swap" in #core-team |
| Shortage lead time | 0-3 days (reactive) | 14+ days (proactive) | Days between alert and ship date |
| Dan's weekly PO time | Hours of Slack DMs | 15 min review + approve | Self-reported |
| Kurt's Wednesday cut order time | 2-3 hours manual | 30 min review + approve | Self-reported |
| Customer reships from wrong items | ~5-10/week | <2/week | Gorgias ticket count |
| Excess inventory waste | Unknown (CH-MAU3 "dumping") | <5% of weekly volume | SKUs with >4 week runway |
| New cheese launch stockouts | ~1 per rotation | 0 | Shortages in first 2 weeks of new SKU |
| Forecast accuracy (MAE) | Unknown | <15% weekly error | Forecast vs actual shipment comparison |

---

## 7. Immediate Next Steps

1. **Fix forecast_demand AttributeError** — unblocks all forecasting MCP tools
2. **Fix remaining 2 bugs** (MONG Monday inflation, CEX-EC truncation)
3. **Build curation-floor calculation** — the single highest-impact feature
4. **Resolve legacy variant IDs** — 22% of subs are invisible to forecasting
5. **Add floor demand to dashboard** — Kurt sees red/amber/green per SKU
6. **Wire up PO draft to Slack** — Dan gets Monday morning PO summary
