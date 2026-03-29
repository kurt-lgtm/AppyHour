# Swap Manager — Implementation Plan (v2, post-critic)

## Overview
Add a "Swap Manager" **view** to the fulfillment_web Flask SPA. Extends existing swap backend (`shopify_swap.py` + 4 routes in app.py) with multi-swap support, matrix upload mode, Recharge sync, swap history, and a dedicated UI panel.

## What Already Exists (DO NOT rebuild)
- **`shopify_swap.py`** — `find_swap_targets()`, `execute_bulk_swap()`, `lookup_variant_gid()`, `execute_swap()` with correct auth `(store_url, token)` params
- **app.py lines 4098-4211** — `/api/swap_preview`, `/api/swap_execute`, `/api/swap_progress`, `/api/swap_cancel`
- **`_swap_progress` / `_swap_cancel`** — background thread + cancel flag pattern
- **`ship_dates.py`** — `compute_ship_week()` for auto-detecting current ship tag

## What's Missing (What We Build)
1. **Multi-swap support** — current routes handle ONE old→new pair; we need N pairs per session
2. **Matrix upload mode** — upload Excel, auto-detect all discrepancies
3. **Dedicated UI view** — instead of triggering swaps from shortage rows
4. **Ship tag picker** — dropdown of active tags
5. **Recharge bundle sync** — update unconverted charges after Shopify swaps
6. **Swap history** — log of past swaps in settings JSON
7. **Export CSV** — download results
8. **sku_mappings.json** — consolidate NAME_TO_SKU from duplicated scripts

## Files to Create

### 1. `fulfillment_web/sku_mappings.json`
Single source of truth for product name → SKU mappings.
```json
{
  "name_to_sku": { "Praline Pecans": "AC-PRPE", ...60+ entries },
  "zero_dollar_variants": { "AC-PPCM": "gid://shopify/ProductVariant/49887127666968", ... }
}
```

## Files to Modify

### 2. `fulfillment_web/app.py` — Add new routes (~150 lines)
Add directly as `@app.route()` decorators (matching existing pattern, NO separate module).

**New routes:**

| Route | Method | Purpose |
|-------|--------|---------|
| `/api/swap/ship-tags` | GET | Fetch distinct `_SHIP_*` tags from unfulfilled orders |
| `/api/swap/multi-preview` | POST | Preview N swap pairs: `{ship_tag, swaps: [{old, new}, ...]}` |
| `/api/swap/multi-execute` | POST | Execute N swap pairs sequentially (reuses existing background thread pattern) |
| `/api/swap/matrix-upload` | POST | Upload Excel → parse → return detected discrepancies as swap pairs |
| `/api/swap/recharge-sync` | POST | Sync bundle_selections on unconverted Recharge charges |
| `/api/swap/history` | GET | Return swap_history from settings |
| `/api/swap/export-csv` | POST | Export results as CSV download |

**Implementation notes:**
- Multi-preview calls `find_swap_targets()` + `lookup_variant_gid()` per pair from `shopify_swap.py`
- Multi-execute reuses `_swap_progress` / `_swap_cancel` globals + daemon thread pattern
- Matrix upload uses `openpyxl` + `sku_mappings.json` (same logic as `_gen_swap_csv.py`)
- Recharge sync uses settings `recharge_api_token` with `X-Recharge-Version: 2021-11`
- History appended to `STATE["saved"]["swap_history"]` and persisted via `save_settings()`

### 3. `fulfillment_web/templates/index.html` — Add view panel + toolbar button
- Toolbar button: `&#9674; Swaps` next to existing buttons
- View panel: `<div id="swapmanager-view">` with config bar, sidebar, preview table, action bar

### 4. `fulfillment_web/static/app.js` — Add swap view JS (~300 lines)
- Add `'swapmanager'` to views in `switchView()`
- `loadShipTags()` — populate dropdown
- `addSwapPair()` / `removeSwapPair()` — manual mode
- `uploadMatrix()` — file upload, parse response into swap pairs
- `previewSwaps()` → `renderSwapPreview(data)` — multi-pair preview
- `executeSwaps()` — with existing progress polling pattern
- `syncRecharge()` — post-swap Recharge sync
- `exportSwapCsv()` — download
- `renderSwapHistory()` — past swaps sidebar

### 5. `fulfillment_web/static/styles.css` — Minimal additions
- `.swap-config-bar`, `.swap-sidebar`, `.swap-pair-row`, `.swap-mode-toggle`
- `.swap-upload-zone` (drag-drop), `.swap-progress-bar`
- Reuse existing `.panel`, `.btn-*`, table styles

## Existing Routes — Keep As-Is
- `/api/swap_preview` — still works for single-pair (shortage row clicks)
- `/api/swap_execute` — still works for single-pair
- `/api/swap_progress` — reused by multi-execute polling
- `/api/swap_cancel` — reused by multi-execute cancel

## Data Flow

```
Manual Mode:
  Pick ship tag → add old→new pairs → [Multi-Preview] → review table → [Execute]

Matrix Mode:
  Pick ship tag → upload Excel → auto-detect pairs → review table → [Execute]

Multi-Execute (background thread):
  For each swap pair:
    1. lookup_variant_gid(new_sku)
    2. find_swap_targets(ship_tag, old_sku)
    3. execute_bulk_swap(targets, old_sku, new_gid, dry_run=False)
    4. Update progress
  Log to swap_history
  Optional: sync Recharge

Recharge Sync:
  1. Fetch queued charges (cursor pagination, status=queued)
  2. For charges with ship date matching tag:
     - GET /bundle_selections?purchase_item_ids={sub_id}
     - Find items with old_sku, replace with new_sku
     - PUT /bundle_selections/{bs_id} with updated items
```

## UI Layout

```
┌──────────────────────────────────────────────────────────────┐
│ [Runway][Dashboard][Cal][Log][CutOrder][Invoices][⬥ Swaps]  │
├──────────────────────────────────────────────────────────────┤
│ Ship Tag: [_SHIP_2026-03-30 ▼]  Mode: [Manual | Matrix]     │
├─────────────┬────────────────────────────────────────────────┤
│ SWAP PAIRS  │  PREVIEW TABLE                                 │
│             │  # │ Order  │ Remove  │ Add     │ Qty          │
│ CH-LEON     │  1 │ #4521  │ CH-LEON │ CH-LOU  │ 1            │
│  → CH-LOU   │  2 │ #4522  │ CH-LEON │ CH-LOU  │ 1            │
│ [x]        │  3 │ #4523  ��� AC-PRPE │ AC-MARC │ 1            │
│             │                                                │
│ AC-PRPE     │  SUMMARY: 47 orders, 2 rules                  │
│  → AC-MARC  │  CH-LEON→CH-LOU: 31 | AC-PRPE→AC-MARC: 16    │
│ [x]        │                                                │
│ [+ Add]     │  [Preview] [Execute] [Sync RC] [Export CSV]    │
│             │  Status: Ready                                 │
│─────────────│                                                │
│ HISTORY     │                                                │
│ 03-23: 2sw  │                                                │
│ 03-16: 3sw  │                                                │
└─────────────┴────────────────────────────────────────────────┘
```

## Swap History Schema
```json
{
  "swap_history": [
    {
      "date": "2026-03-29T17:30:00",
      "ship_tag": "_SHIP_2026-03-30",
      "swaps": [{"old": "CH-LEON", "new": "CH-LOU", "orders": 31}],
      "mode": "manual",
      "total_orders": 47,
      "total_failed": 0,
      "recharge_synced": false
    }
  ]
}
```

## Testing Plan
1. **Unit** — sku_mappings loading, matrix Excel parsing, swap pair validation
2. **Route** — Flask test client for new `/api/swap/*` endpoints (mocked Shopify/Recharge)
3. **Integration** — Multi-preview dry run against real Shopify
4. **Manual QA** — Both modes end-to-end in browser

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| app.py already 8K lines | New routes are ~150 lines in existing swap section, not a new module |
| Multi-swap reuses single-pair progress globals | Wrap in a multi-step loop, progress shows "Pair 1/3: CH-LEON..." |
| Matrix Excel format varies | Same robust header detection as _gen_swap_csv.py |
| Recharge write token security | Read from settings JSON `recharge_api_token`, never hardcode |
| openpyxl not installed | Already in use by _gen_swap_csv.py; add to requirements if needed |
