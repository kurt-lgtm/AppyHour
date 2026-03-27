# Fulfillment App Simplification Plan

## Context
The app currently requires multiple clicks to reach the most useful view (Runway), shows model parameters that are set-and-forget in the main header, and has no unified view of inventory movements. These changes make it faster to use and easier to understand.

## Changes Overview

### 1. Default to Runway View on Load
**Files:** `app.js` (DOMContentLoaded + runAll), `index.html` (active class)

- Change default active tab from `view-dashboard` to `view-runway` in index.html (line 22)
- In `runAll()` (line 1779), after pipeline completes, call `switchView('runway')` and `loadRunway()`
- Dashboard stays accessible as a tab — just not the landing page
- Morning briefing can show as a toast/banner on the runway view instead of a dashboard panel

### 2. Inline Shortage Actions on Runway Rows
**Files:** `app.js` (renderRunwayGrid), `app.py` (/api/suggest_fixes already exists), `styles.css`

- After rendering runway grid, fetch `/api/suggest_fixes`
- For each SKU with `worst_status === 'SHORTAGE'` or runway < 2 weeks, show an action chip on the row:
  - Has wheel inventory → `"Cut X wheels"` chip (links to PO draft)
  - Has vendor in catalog → `"PO X units"` chip (links to PO draft)
  - Has substitution suggestion → `"Sub: CH-XXXX"` chip
- Action chips are small, colored pills right-aligned in the runway column area
- Click a chip → opens a confirmation, then executes (e.g., adds to draft PO)
- Expand row pattern: click the SKU to see full shortage detail with all fix options

**Existing code to reuse:**
- `/api/suggest_fixes` (app.py) — already returns fix suggestions per SKU
- `/api/substitutions` — surplus→shortage substitution suggestions
- `showSubstitutions()` in app.js — existing rendering pattern

### 3. Move Model Params from Runway Header to Settings
**Files:** `index.html` (runway-header), `app.js` (renderRunway)

- Remove the 3 model parameter items from `#runway-header`:
  - `#rw-repeat-rate` (repeat rate)
  - `#rw-reship-pct` (reship %)
  - `#rw-churn-wk` (weekly churn %)
- Keep the 3 useful summary items:
  - `#rw-sku-count` (SKUs with demand)
  - `#rw-avg-forecast` (avg runway weeks)
  - `#rw-at-risk` (shortage/tight count)
- Model params are already editable in Settings → Forecasting section
- In `renderRunway()`, stop populating the removed elements

### 4. Auto-Run Full Pipeline on App Open
**Files:** `app.js` (runAll function)

- `runAll()` already executes on DOMContentLoaded with 300ms delay — this is ALREADY DONE
- Sequence: Dropbox sync → Recharge pull → Shopify pull → Calculate → Calendar → Briefing
- Enhancement: After pipeline completes, automatically switch to runway view and call `loadRunway()`
- Show a progress indicator during auto-run (mascot already handles this via expressions)

### 5. Activity Log / Timeline Tab
**Files:** `index.html` (new tab + view container), `app.js` (new render functions), `app.py` (new endpoint), `styles.css` (timeline styles)

#### Backend: `/api/activity_log` endpoint (app.py)
- Aggregate from existing data sources into unified timeline:
  - `depletion_history` → DEPLETION events (outflow)
  - `open_pos` with status/eta → PO_SUBMITTED, PO_RECEIVED events (inflow)
  - `production_yield_history` → PRODUCTION events (inflow)
  - `transfer_history` → TRANSFER events (inflow/outflow)
  - `reconciliation_history` → RECONCILIATION events (status)
  - Waste ledger (from settings `waste_ledger`) → WASTE events (outflow)
- Each event: `{type, date, direction: "in"|"out"|"status", summary, total_units, skus: [{sku, qty}]}`
- Sort by date descending (newest first)
- Optional query param: `?days=30` to limit lookback

#### Frontend: Timeline rendering (app.js)
- New `loadActivityLog()` function called on tab switch
- Vertical timeline with date grouping
- Each entry: icon (▲▼◆●) + type label + summary + total units
- Click to expand SKU-level detail (same pattern as demand breakdown)
- Color coding: green for inflows, red for outflows, blue for status/actions

#### HTML: Tab definition (index.html)
- Add `view-log` button in nav bar (between Calendar and Settings)
- Add `#log-view` container with timeline wrapper
- Remove `showOrderList()` button from toolbar (absorbed by log)

#### CSS: Timeline styles (styles.css)
- `.tl-entry` — timeline row with left icon strip
- `.tl-date-group` — date header separator
- `.tl-icon` — colored circle/arrow
- `.tl-in` / `.tl-out` / `.tl-status` — directional colors
- `.tl-detail` — expandable SKU breakdown (same pattern as `.rw-breakdown-row`)

### 6. Remove Order List
**Files:** `index.html` (remove toolbar button), `app.js` (keep `showOrderList()` but don't expose in nav)

- Remove the "Order List" button from the toolbar (line 38 in index.html)
- The function can stay in app.js for now — it's called by the briefing in some flows
- Order-level detail is better accessed through the Activity Log's depletion entries

## Tab Order (Final)
```
[Runway] [Dashboard] [Calendar] [Activity] [Cut Order] [Settings]
```

Runway is default. Invoices tab stays if it has active use, otherwise remove.

## Implementation Sequence

1. **Move model params** (15 min) — Remove from runway header HTML, stop populating in JS
2. **Default to runway + auto-switch** (10 min) — Change active class, add switchView call in runAll
3. **Inline shortage actions** (45 min) — Fetch suggestions, render action chips, wire click handlers
4. **Activity Log backend** (30 min) — New endpoint aggregating all history sources
5. **Activity Log frontend** (45 min) — New tab, timeline rendering, expandable detail rows
6. **Remove Order List button** (5 min) — Delete button from toolbar
7. **Verify and rebuild EXE** (10 min)

## Verification
1. App opens → auto-runs → lands on Runway view with data
2. Shortage SKUs show inline action chips
3. Model params gone from runway header, still in Settings
4. Activity tab shows timeline with depletions, POs, production, transfers, waste
5. Click events expand to show SKU detail
6. No Order List button in toolbar
7. Calendar view still works
8. EXE builds and launches correctly
