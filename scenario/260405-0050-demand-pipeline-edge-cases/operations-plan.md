# Elevate Foods — Operations Plan & Software Roadmap

**Date:** 2026-04-05
**Team:** You (Owner/Ops), Tommy (Product + Procurement), Anik (Developer), AI Assistant (Tools)

---

## 1. Role Organization

### You — Chief Operator / Decision Maker
**Weekly time target: 2 hours (down from 8+)**

| Day | Task | Time | How |
|-----|------|------|-----|
| Monday AM | Review PO draft from system | 10 min | Dashboard → approve/reject PO lines |
| Monday AM | Review shortage alerts | 10 min | Dashboard → red/amber/green per SKU |
| Wednesday AM | Approve cut order | 15 min | Dashboard → one-click export XLSX |
| Wednesday PM | Review swap suggestions (if shortage) | 15 min | Dashboard → approve suggested swaps |
| Friday PM | Glance at weekend ship readiness | 5 min | Dashboard → ship readiness score |
| Ad-hoc | Reship/credit decisions | 30 min | Gorgias → system suggests resolution |

**What you DON'T do anymore:**
- Calculate demand manually
- Hunt for shortages in spreadsheets
- DM Dan/Tommy about POs (system does this)
- Build cut order from scratch
- Second-guess forecast numbers

### Tommy — Director of Product + Procurement
**Weekly time target: 3 hours**

| Day | Task | Time | How |
|-----|------|------|-----|
| Monday AM | Review & approve PO draft | 15 min | Dashboard or Slack notification → approve |
| Monday AM | Check supplier lead time conflicts | 10 min | Dashboard → supplier calendar view |
| Wednesday | Set next month's recipes (monthly) | 1 hr/month | Settings → curation recipe editor |
| Wednesday | Review new cheese ramp-up plan | 15 min | Dashboard → auto-ramp shows inherited demand |
| Thursday | Confirm RMFG has POs received | 10 min | Email/Slack confirmation |
| Friday | Review inventory health for weekend | 10 min | Dashboard → runway view |
| Monthly | Plan 2-month cheese rotation | 2 hrs | Recipe planning tool → system auto-calculates demand impact |

**Tommy's superpowers the system should leverage:**
- He knows cheese aging timelines → input supplier lead times per SKU
- He knows seasonal availability → flag SKUs with limited windows
- He can plan 2-3 months ahead → system should show 3-month horizon with his rotation plan

### Anik — Developer
**Builds the software. Specs below.**

### AI Assistant — Tools & Automation
**Builds MCP tools, scripts, automation alongside Anik.**

### Michelle — Marketing (no change)
**One new responsibility:** When scaling ad budget >2x, post in #ads-team with spend amount. System picks up the signal and bumps safety buffer.

---

## 2. Weekly Schedule (The New Cycle)

```
FRIDAY
  └─ System: Inventory snapshot auto-pulled from Dropbox/RMFG
  └─ System: Recharge sync (queued charges for next 4 weeks)
  └─ System: Shopify sync (unfulfilled orders + rolling average)
  └─ You: Glance at weekend readiness (5 min)

SATURDAY
  └─ RMFG: Ships Saturday orders
  └─ System: Auto-depletion from shipment XLSX (writes journal entry ✓)
  └─ System: Tuesday projection calculated

SUNDAY
  └─ System: Churn check (Recharge cancellations, weekly)
  └─ System: Growth multiplier recalculated from first-order trend

MONDAY
  └─ System: PO draft generated (runway < 2 weeks → draft PO line)
  └─ System: Slack notification to Tommy: "3 SKUs need POs — review"
  └─ Tommy: Reviews and approves POs (15 min)
  └─ You: Review shortage alerts, approve POs (10 min)
  └─ System: Approved POs emailed to suppliers

TUESDAY
  └─ RMFG: Ships Tuesday orders (first orders, reships)
  └─ System: Auto-depletion from Tuesday shipment

WEDNESDAY
  └─ System: Cut order auto-generated from: max(curation_floor, ewma × growth)
  └─ System: Swap suggestions if any SKU short
  └─ You: Review and approve cut order (15 min)
  └─ You: Approve swaps if needed (15 min)
  └─ System: Cut order XLSX exported and emailed to RMFG

THURSDAY
  └─ Tommy: Confirms RMFG received POs and cut order
  └─ System: Tracks PO acknowledgment status
```

---

## 3. Software to Build (for Anik + AI Assistant)

### Sprint 1: Foundation Fixes (Week 1)

| # | Feature | Assigned | Effort | Status |
|---|---------|----------|--------|--------|
| 1.1 | Fix forecast_demand AttributeError | Claude | Small | **DONE** |
| 1.2 | Fix auto_deplete journal entries | Claude | Small | **DONE** |
| 1.3 | Fix save_settings race condition | Claude | Small | **DONE** |
| 1.4 | Fix CEX-EC MONTHLY demand drop | Claude | Small | **DONE** |
| 1.5 | Fix zero charges fallback | Claude | Small | **DONE** |
| 1.6 | Fix MONG Monday night 7x inflation | Anik | Small | TODO |
| 1.7 | Fix CEX-EC split int() truncation | Anik | Small | TODO |
| 1.8 | Resolve 3,000 legacy numeric variant IDs | Anik | Medium | TODO |
| 1.9 | Audit 42 zero-inventory SKUs | Tommy + System | Medium | TODO |

**Spec for 1.6 (MONG Monday inflation):**
File: `fulfillment_web/app.py` ~line 6593
Problem: `if days_to_monday == 0 and now_et.hour >= 23: days_to_monday = 7` → projects 7 days of first orders instead of ~1 hour.
Fix: When `days_to_monday == 0`, set `days_to_monday = 0` (project only remaining hours today). Remove the `hour >= 23` override.

**Spec for 1.7 (CEX-EC truncation):**
File: `fulfillment_web/app.py` ~line 6203
Problem: `int(qty * pct)` truncates to 0 when qty=1, pct<1.0
Fix: Accumulate fractional demand, round at the end: `week_demands[...][sku] += qty * pct` (keep as float), then `int(round(...))` when storing to STATE.

**Spec for 1.8 (Legacy variant IDs):**
The subscription data shows ~3,000 subs on numeric Shopify variant IDs (e.g., `49882126942488: 1,741`). These need to be resolved to actual box types (AHB-MED, AHB-LGE, etc.) via Shopify variant lookup. Build a one-time migration script + ongoing resolver.

### Sprint 2: Curation-Floor Dashboard (Week 2-3)

| # | Feature | Assigned | Effort |
|---|---------|----------|--------|
| 2.1 | `compute_curation_floor()` function | Anik | Medium |
| 2.2 | `/api/curation_floor` endpoint | Anik | Small |
| 2.3 | Dashboard view: SKU → Available → Floor → Runway → Status | Anik | Medium |
| 2.4 | Red/amber/green alerting with configurable thresholds | Anik | Small |
| 2.5 | Tray box type integration into curation floor | Anik | Medium |

**Spec for 2.1 (curation floor):**
```python
def compute_curation_floor(settings, subscription_counts):
    """Minimum demand per SKU based on active curation recipes.
    
    For each curation with active subscribers:
      For each SKU in that curation's recipe:
        floor[sku] += subscription_count[curation] * qty_per_box
      Plus: pr_cjam cheese (1 per box)
      Plus: cex_ec cheese (~40% of large boxes)
    
    Returns: {sku: floor_demand}
    """
    recipes = settings.get("curation_recipes", {})
    pr_cjam = settings.get("pr_cjam", {})
    cex_ec = settings.get("cex_ec", {})
    monthly_recipes = settings.get("monthly_box_recipes", {})
    
    floor = defaultdict(int)
    
    # Set curations
    for curation, recipe in recipes.items():
        box_count = subscription_counts.get(curation, 0)
        if box_count == 0:
            continue
        for sku, qty in recipe:
            floor[normalize_sku(sku)] += box_count * qty
        # PR-CJAM: 1 per box
        pj = pr_cjam.get(curation, {})
        if isinstance(pj, dict) and pj.get("cheese"):
            floor[normalize_sku(pj["cheese"])] += box_count
        # CEX-EC: ~40% of large boxes
        ec = cex_ec.get(curation, "")
        if ec and isinstance(ec, str):
            large_count = subscription_counts.get(f"{curation}_LGE", 0)
            floor[normalize_sku(ec)] += int(large_count * 0.4)
    
    # Monthly box recipes (current month)
    current_month = datetime.date.today().strftime("%Y-%m")
    if current_month in monthly_recipes:
        for box_type, slots in monthly_recipes[current_month].items():
            box_count = settings.get("monthly_box_counts", {}).get(box_type, 0)
            for slot, sku, qty in slots:
                floor[normalize_sku(sku)] += box_count * qty
    
    return dict(floor)
```

**Spec for 2.5 (Tray integration):**
AHB-MCUST-TRAY has 410 subscribers. Trays need their own recipe definition in `curation_recipes` or `monthly_box_recipes`. The tray recipe determines which SKUs get 410 units of floor demand. Without this, tray demand is invisible to the floor calculation — **this is why trays broke inventory.**

### Sprint 3: EWMA + Growth Multiplier (Week 3-4)

| # | Feature | Assigned | Effort |
|---|---------|----------|--------|
| 3.1 | EWMA function (replace flat 8-week avg) | Anik | Small |
| 3.2 | Growth multiplier from first-order trend | Anik | Small |
| 3.3 | `first_order_weekly_history` tracking in shopify_sync | Anik | Small |
| 3.4 | Final demand = max(floor, ewma × growth) | Anik | Small |
| 3.5 | Settings UI for EWMA alpha and growth clamp | Anik | Small |

### Sprint 4: PO Draft Generator (Week 4-5)

| # | Feature | Assigned | Effort |
|---|---------|----------|--------|
| 4.1 | `/api/po_draft` endpoint | Anik | Medium |
| 4.2 | PO draft UI in dashboard (review + approve) | Anik | Medium |
| 4.3 | Slack notification to Tommy on Monday | Anik/AI | Small |
| 4.4 | Email PO to supplier on approval | Anik/AI | Medium |
| 4.5 | Vendor catalog with lead times, MOQ, case qty | Tommy (data) + Anik (UI) | Medium |

**PO draft formula:**
```
For each SKU where runway_weeks < reorder_threshold:
    deficit = (weekly_demand * (reorder_weeks + safety_weeks)) - available
    po_qty = ceil(deficit / vendor_case_qty) * vendor_case_qty
    po_line = {sku, qty: po_qty, vendor, unit_cost, total_cost, eta}
```

### Sprint 5: Auto-Ramp on Rotation (Week 5-6)

| # | Feature | Assigned | Effort |
|---|---------|----------|--------|
| 5.1 | Recipe diff detection (old vs new month) | Anik | Medium |
| 5.2 | Auto-create sku_ramp entries from diff | Anik | Small |
| 5.3 | Demand inheritance blend (100→67→33→0% over 3 weeks) | Anik | Small |
| 5.4 | Recipe planning tool for Tommy (2-month horizon) | Anik | Medium |

### Sprint 6: Anomaly Detection & Alerts (Week 6-7)

| # | Feature | Assigned | Effort |
|---|---------|----------|--------|
| 6.1 | Demand anomaly detection (>20% EWMA deviation) | Anik | Small |
| 6.2 | Stale data guard (block cut order if data >4h old on Wed) | Anik | Small |
| 6.3 | Self-healing syncs (auto-retry on failure) | Anik | Small |
| 6.4 | Slack alert channel integration | AI Assistant | Small |
| 6.5 | Weekly ops summary auto-post | AI Assistant | Small |

### Sprint 7: Churn Detection (Week 7-8)

| # | Feature | Assigned | Effort |
|---|---------|----------|--------|
| 7.1 | Recharge cancellation rate endpoint | Anik | Medium |
| 7.2 | Churn signal → forecast adjustment | Anik | Small |
| 7.3 | Acquisition vs churn dashboard widget | Anik | Small |

---

## 4. Tray Launch: What Broke and How to Fix It

**410 subscribers on AHB-MCUST-TRAY.** This is a new box type that doesn't fit the existing curation model:

### Why It Broke
1. **No curation recipe for TRAY** — `resolve_curation_from_box_sku("AHB-MCUST-TRAY")` likely returns None or an unrecognized curation
2. **No floor demand** — curation floor calculation doesn't know what SKUs go in a tray
3. **No PR-CJAM/CEX-EC assignment** — trays may or may not include bonus items
4. **410 boxes × ~4-7 items each = 1,600-2,800 units of invisible demand**
5. **Demand pipeline counted tray orders as "direct" SKUs** but missed curation-resolved demand (PR-CJAM, CEX-EC)

### How to Fix
1. **Define tray recipe** in `curation_recipes["TRAY"]` — what SKUs go in a tray?
2. **Add TRAY to `resolve_curation_from_box_sku()`** — `AHB-MCUST-TRAY` → "TRAY"
3. **Configure PR-CJAM and CEX-EC for TRAY** (if applicable)
4. **Add TRAY to curation floor** — 410 units × qty per SKU
5. **If trays are monthly curations:** add to `monthly_box_recipes` with slot definitions

**Anik's task:** Add TRAY as a recognized curation in the demand pipeline. This is Sprint 1 priority alongside the bug fixes.

---

## 5. Software Architecture (for Anik)

### What Exists
- `fulfillment_web/app.py` — 250KB Flask backend (all endpoints, calculation engine)
- `static/app.js` — 195KB client-side JS (state, rendering, mascot, calendar)
- `static/styles.css` — 60KB dark theme
- `templates/index.html` — 49KB single-page app
- `AppyHourMCP/` — MCP server for Claude Code integration
- `appyhour/reorder.py` — pure forecasting functions

### What to Build
All new features go into the existing Flask app. No new frameworks, no rewrites. Incremental.

**New endpoints needed:**

| Endpoint | Method | Purpose | Sprint |
|----------|--------|---------|--------|
| `/api/curation_floor` | GET | Return floor demand per SKU | 2 |
| `/api/demand_final` | GET | Return max(floor, ewma × growth) per SKU | 3 |
| `/api/po_draft` | GET | Return auto-generated PO draft lines | 4 |
| `/api/po_approve` | POST | Mark PO lines as approved, trigger email | 4 |
| `/api/recipe_diff` | GET | Compare current vs next month recipes | 5 |
| `/api/anomaly_check` | GET | Return SKUs with demand anomalies | 6 |
| `/api/churn_signal` | GET | Return acquisition/churn metrics | 7 |

**New dashboard views:**

| View | What It Shows | Sprint |
|------|-------------|--------|
| **Floor Demand** | SKU → Available → Floor → EWMA → Final → Runway → Status | 2 |
| **PO Drafts** | Vendor → SKU → Qty → Cost → Approve button | 4 |
| **Recipe Planner** | Next month recipes → demand impact preview → auto-ramp preview | 5 |
| **Health Monitor** | Data freshness, sync status, anomaly flags | 6 |

### Tech Decisions for Anik
- **No new frameworks** — extend existing Flask + vanilla JS
- **Settings JSON is the database** — all config persists there
- **STATE dict is runtime cache** — lost on restart, rebuilt from settings
- **Inventory journal is source of truth** — not STATE["rmfg_inventory"]
- **Always `load_settings()` fresh before `save_settings()`** — race condition fix
- **New functions in `appyhour/reorder.py`** — keep pure logic separate from Flask routes

---

## 6. Success Milestones

| Week | Milestone | How You Know It Worked |
|------|-----------|----------------------|
| 1 | Foundation fixes live | forecast_demand MCP tool returns data. No more MONG inflation. |
| 2 | Tray integrated | Tray demand visible in dashboard. 410 × items counted. |
| 3 | Curation floor live | Every SKU shows floor demand. Red alerts match real shortages. |
| 4 | EWMA replaces flat avg | Demand numbers feel right. No more holiday hangover. |
| 5 | PO drafts working | Tommy gets Monday Slack with PO suggestions. One-click approve. |
| 6 | Auto-ramp on rotation | Tommy changes May recipe. System auto-orders new cheeses. |
| 7 | Anomaly detection | System catches demand spike from ad budget change before you do. |
| 8 | Churn signal | Dashboard shows subscriber growth trend. Forecast adjusts automatically. |

**The "never stockout" test:** Go 4 consecutive weeks with zero shortage incidents in #core-team Slack. If you make it, the system is working.
