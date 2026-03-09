# Inventory Reorder System — Development Guide

## Architecture

Single-file tkinter application (`inventory_reorder.py`) following the same patterns as `GelPackCalculator/gel_pack_shopify.py`.

## Key Patterns

- **Dark theme:** ttk.Style with "clam" theme, color constants `_BG/_BG2/_BG3/_FG/_FG2/_ACC/_SEP`
- **Settings:** JSON file next to exe/script, load at init, save on close and on demand
- **Threading:** Recharge/Shopify API calls run on daemon threads, UI updates via `root.after(0, callback)`
- **Treeview tags:** Row coloring for CRITICAL/WARNING/OK/OVERSTOCK/PLAN status alerts
- **Modal dialogs:** `tk.Toplevel` with `grab_set()` + `transient()`, dark themed
- **Column mapping:** CSV import shows a dialog to map CSV headers to app fields
- **Forecast engine:** Pure functions for cohort-based demand projection

## Data Flow

### Legacy Flow (flat churn)
```
Recharge API  ──> recharge_demand {sku: weekly_qty}
                      │  (or queued charges ÷ 4.33 if available for current month)
                      ├── apply churn rate
                      ├── decompose bundles
                      ▼
Shopify API   ──> shopify_api_demand {sku: projected_next_week}
                      │  (linear regression trend, replaces flat averages)
                      ▼
Shopify Forecast ──> shopify_forecast {sku: weekly_qty}
                      │
                      ├── decompose bundles
                      ▼
Manual Demand ────> manual_demand {sku: weekly_qty}
                      │
                      ▼
              Combined Demand (per component SKU)
                      │
                      ▼
              daily_usage = total_weekly / 7
                      │
                      ▼
              Reorder Point = (daily_usage × (lead_time + fulfillment_buffer)) + safety_stock
                      │
                      ▼
              Compare to On Hand → Status (OK / REORDER / CRITICAL / OUT OF STOCK)
```

### Cohort-Based Forecast Flow (v2.0)
```
Cohorts [{start_month, size, track}, ...]
    │
    ▼
Retention Matrix (7 curations × 7 months)
    │  % of original cohort on each curation per month
    ▼
Curation Counts per month
    │
    ├── × Curation Recipe (SKU list per curation)
    ├── + PR-CJAM assignment (1 per box)
    ├── + CEX-EC assignment (~40% of boxes)
    ▼
SKU Demand per month
    │
    ├── vs On Hand inventory
    ├── vs Open PO qty
    ├── vs Wheel Supply (weight × count × 2.67)
    ▼
Net Position = on_hand + open_PO + wheel_supply - demand
    │
    ▼
Reorder Alerts (CRITICAL / WARNING / PLAN)
    with Action Types (PO / MFG / Transfer)
```

## Data Stores (all in settings JSON)

| Key | Type | Description |
|-----|------|-------------|
| `inventory` | `{sku: {qty, name, category, warehouse, unit_cost, expiration_dates?, warehouse_qty?}}` | Current stock levels. `warehouse_qty: {Primary: N, Woburn: N}` for split-warehouse items. `qty` = total across all warehouses |
| `sku_settings` | `{sku: {purchase_lt, production_lt, shipping_lt, safety_stock, churn_pct}}` | Per-SKU overrides |
| `bundle_map` | `{bundle_sku: [[component_sku, qty], ...]}` | Bundle decomposition |
| `recharge_demand` | `{sku: weekly_qty}` | Last Recharge API pull (subscriptions) |
| `recharge_queued` | `{month: {sku: qty}}` | Queued charges by scheduled month |
| `shopify_forecast` | `{sku: weekly_qty}` | Manual Shopify forecast |
| `shopify_api_demand` | `{sku: projected_weekly}` | Shopify API demand (trend-projected) |
| `shopify_trend_data` | `{sku: {weekly_avg, trend_slope, projected_next_week, pct_of_orders}}` | Shopify trend analysis |
| `manual_demand` | `{sku: weekly_qty}` | Manual demand adjustments |
| `cohorts` | `[{start_month, size, track}, ...]` | Subscriber cohort data |
| `retention_matrix` | `{curation: [mo1_pct, ..., mo7_pct]}` | % of original cohort per curation per month |
| `churn_rates` | `{track: {month_1, month_2_plus, ...}}` | Per-track churn rates |
| `repeat_rate` | `float` | Repeat rate (default 0.56) |
| `curation_recipes` | `{curation: [[sku, qty], ...]}` | Items per box per curation |
| `pr_cjam` | `{curation: {cheese: sku, jam: sku}}` | PR-CJAM bonus pairing (cheese unique, jam overlap OK) |
| `cex_ec` | `{curation: cheese_sku}` | CEX-EC extra cheese assignments |
| `wheel_inventory` | `{sku: {weight_lbs, count, target_sku}}` | Cheese wheel raw inventory |
| `open_pos` | `[{sku, qty, eta, type, vendor, status}, ...]` | Open purchase orders |
| `forecast_months` | `int` | Forecast horizon (default 3) |
| `monthly_box_recipes` | `{month: {box_type: [[slot, sku, qty], ...]}}` | Monthly curated box recipes (MED/CMED/LGE) |
| `monthly_box_counts` | `{box_type: count}` | Subscription counts per box type |
| `fulfillment_buffer` | `string` | Fulfillment buffer days (default "10") |
| `expiration_warning_days` | `string` | Days threshold for expiration warnings (default "14") |
| `sku_translations` | `{product_name: sku_code}` | Saved SKU name translations from fulfillment CSV |
| `depletion_history` | `[{date, file, day, skus, total, total_orders, reship_count, reship_pct}]` | Log of applied depletions (supports undo, reship tracking) |
| `reship_buffer_pct` | `float` | Rolling reship % from fulfillment history (auto-calculated, editable) |
| `actual_retention` | `{start_month: {size, retention: [mo1_pct, ...]}}` | Actual retention curves from Recharge |
| `customer_lifecycle` | `{email: {first_order_date, order_count, last_order_date, months_active}}` | Customer lifecycle from Shopify |
| `customization_variance` | `{sku: {expected, actual, variance_factor}}` | Customization swap tracking (reserved) |
| `vendor_catalog` | `{sku: {vendor, unit_cost, case_qty, moq, wheel_weight_lbs}}` | Vendor catalog for auto-PO |
| `exec_assignments` | `{curation: [sku, ...]}` | EX-EC cheese assignment history |
| `clickup_api_token` | `string` | ClickUp REST API token |
| `clickup_list_id` | `string` | ClickUp list ID for task sync |
| `gcal_refresh_token` | `string` | Google Calendar OAuth2 refresh token |
| `gcal_client_id` | `string` | Google Calendar OAuth2 client ID |
| `gcal_client_secret` | `string` | Google Calendar OAuth2 client secret |
| `dropbox_refresh_token` | `string` | Dropbox OAuth2 refresh token |
| `dropbox_app_key` | `string` | Dropbox app key |
| `dropbox_app_secret` | `string` | Dropbox app secret |
| `dropbox_shared_link` | `string` | Dropbox shared folder link (use when folder isn't in your account) |
| `reconciliation_history` | `[{date, mismatches, skus_checked}]` | Reconciliation log |
| `slack_webhook_url` | `string` | Slack incoming webhook URL |
| `slack_notify_critical` | `bool` | Send Slack for critical reorder alerts |
| `slack_notify_expiring` | `bool` | Send Slack for expiring inventory |
| `slack_notify_shortfall` | `bool` | Send Slack for fulfillment shortfalls |
| `smtp_host` | `string` | SMTP host (default "smtp.gmail.com") |
| `smtp_port` | `string` | SMTP port (default "587") |
| `smtp_user` | `string` | SMTP username |
| `smtp_password` | `string` | SMTP password (app password for Gmail) |
| `depletion_email_to` | `string` | Depletion report recipient email |
| `depletion_email_from` | `string` | Depletion report sender email |
| `auto_refresh_interval` | `int` | Auto-refresh interval in minutes (0=off, default 30) |
| `auto_po_threshold` | `int` | Auto-PO deficit threshold in units (0=off) |
| `auto_sync_clickup` | `bool` | Auto-sync schedule to ClickUp after refresh |
| `auto_sync_gcal` | `bool` | Auto-sync schedule to Google Calendar after refresh |
| `webhook_port` | `int` | Webhook HTTP server port (default 8765) |
| `webhook_secret_shopify` | `string` | Shopify webhook secret |
| `webhook_secret_recharge` | `string` | Recharge webhook secret |
| `bulk_conversions` | `{keyword: {sku, packet_oz}}` | Bulk raw → finished packet conversions |
| `production_yield_history` | `[{date, sku, expected, actual, factor}]` | Cheese production yield entries |
| `adjusted_conversion_factors` | `{sku: float}` | Rolling avg conversion factors from yield history |
| `archived_skus` | `[sku, ...]` | SKUs hidden from dashboard (right-click to archive/unarchive) |
| `warehouses` | `{name: {label, is_fulfillment, capabilities}}` | Warehouse definitions. Primary = RMFG TX (fulfillment). Woburn = receive/process/crossdock/store (all SKU types) |
| `transfer_history` | `[{date, sku, qty, from_warehouse, to_warehouse}]` | Inter-warehouse transfer log |
| `processing_queue` | `[{id, sku, source_material, target_qty, status, warehouse, created, completed, actual_yield}]` | AC-/CH- processing job queue |
| `yield_discrepancies` | `[{date, sku, type, expected_qty, actual_qty, variance, yield_date, snapshot_date, status}]` | Yield vs snapshot flags |
| `yield_reconciliation_window_days` | `int` | Days to look back for yield-snapshot matching (default 3) |
| `yield_reconciliation_threshold_pct` | `int` | % variance before flagging (default 5) |
| `yield_reconciliation_threshold_min` | `int` | Minimum unit variance before flagging (default 2) |

## Reorder Formula (Legacy)

```
Total Lead Time = purchase_lt + production_lt + shipping_lt
Daily Usage = combined_weekly_demand / 7
Reship Multiplier = 1 + (reship_buffer_pct / 100)
Adjusted Daily Usage = Daily Usage × Reship Multiplier
Reorder Point = (Adjusted Daily Usage × (Total Lead Time + Fulfillment Buffer)) + Safety Stock
```

## Forecast Formula Chain (v3.0)

```
For each target month, for each cohort:
  age = target_month - cohort_start_month + 1
  For each curation:
    boxes = cohort_size × retention_matrix[curation][age-1]
    sku_demand += boxes × recipe[curation]
    sku_demand += boxes × pr_cjam[curation].cheese  (1 per box)
    sku_demand += boxes × pr_cjam[curation].jam    (1 per box)
    sku_demand += boxes × cex_ec[curation]   (0.4 per box, large only)

Queued Charge Overlay (v2.1):
  For months with queued charge data:
    Replace cohort sku_demand with queued charges (with churn + bundle decomposition)
  Months without queued data: cohort projections remain unchanged

Wheel Supply = weight_lbs × count × 2.67  (per wheel SKU)
Bulk Supply = total_oz(raw material) / packet_oz  (per AC- SKU, Primary only)
Net Position = on_hand + open_PO + wheel_supply + bulk_supply - forecast_demand
```

## Cross-Dock Timeline

```
Thursday:  Bulk arrives at Woburn
Friday:    Cross-dock pickup (Woburn → RMFG TX)
+9 days:   Available at Primary for fulfillment (2nd Saturday)
```

`CROSSDOCK_LEAD_DAYS = 9`

## Warehouse Model

- **Primary (RMFG TX)**: Fulfillment warehouse. Only Primary inventory counts toward reorder alerts
- **Woburn MA**: Receives bulk, processes AC- items, stores/cross-docks MT-/CH-/AC- finished goods
- Inventory supports `warehouse_qty: {Primary: N, Woburn: N}` for SKUs split across warehouses
- Helpers: `_qty_at(sku, warehouse)`, `_set_qty_at(sku, warehouse, qty)`, `_primary_inventory()`

## Inventory Snapshot Format (Dropbox/RMFG)

Columns: Ingredient, Product SKU, MFG Name, KitchenLocation, Quantity1, Unit1, Total, RMFG, GRIP_CA, WIP, Expiration Dates

## Status Levels

| Status | Condition | Color |
|--------|-----------|-------|
| OUT OF STOCK | on_hand = 0 and has demand | Dark red |
| CRITICAL | on_hand ≤ 50% of reorder point (or ≤3 days supply) | Dark red |
| REORDER / WARNING | on_hand ≤ reorder point (or ≤10 days supply) | Amber/yellow |
| OK | on_hand > reorder point | Default |
| OVERSTOCK | on_hand > 3× reorder point | Dark green |
| PLAN | Will run out within forecast horizon | Blue |

## Alert Action Types

| Action | Description |
|--------|-------------|
| PO | Need vendor purchase order (MT-, AC- finished goods without bulk, CH- wheels) |
| MFG | Need to cut/wrap/label cheese (CH- with wheels) or process AC- items (AC- with bulk raw materials) |
| Transfer | Need to move finished goods to fulfillment location (Woburn -> Primary) |

## Tabs

1. **Dashboard** — Main view: sortable/filterable treeview with reorder alerts and expiration warnings. Wheel Pot. and Bulk Pot. columns show potential supply from processing. Expiration column shows days until earliest expiration; summary bar includes expiring SKU count. Alert bell + yield flag badges in top bar. Snapshot button shows Current vs Potential comparison. Workflow Guide button. Warehouse filter dropdown. Double-click to edit SKU settings. Keyboard shortcuts: F5 refresh, Ctrl+F filter, Ctrl+S snapshot, Ctrl+W workflow.
2. **Demand Sources** — Configure Recharge API, Shopify forecast, manual adjustments. Combined demand summary.
3. **Inventory** — Import CSV (auto-detects cheese wheels and expiration dates), manually add/edit/remove SKUs. "Deplete & Email" one-click button. Expiration columns show earliest date and batch count with row coloring for expired/expiring items. Warehouse filter + Transfer Woburn->Primary + Transfer History buttons.
4. **Forecasting** — Cohort-based forecast: run forecast, view by month, curation breakdown, SKU demand, reorder alerts.
5. **Calendar** — Monthly grid view of action schedule (PO/MFG/Crossdock/Fulfillment dates). Generate Schedule, Sync to ClickUp, Sync to Google Calendar, Check Dropbox, EX-EC Suggestions, Fulfillment Preview, Reconcile Inventory, Processing Queue.
6. **Settings** — Global defaults, churn, bundles, API tokens, curation/forecasting config, supply pipeline (+ processing queue, yield history), yield reconciliation settings, warehouse config, vendor catalog, integrations (ClickUp, Google Calendar, Dropbox), Slack notifications, Email/SMTP, Automation (auto-refresh, auto-PO, auto-sync), Webhooks (Shopify/Recharge).

## Dialogs

| Dialog | Purpose |
|--------|---------|
| `ColumnMappingDialog` | Map CSV columns to app fields on import |
| `BundleMappingEditor` | Edit bundle → component SKU mappings |
| `SkuSettingsDialog` | Per-SKU lead times and safety stock |
| `ManualDemandDialog` | Per-SKU weekly demand adjustments |
| `ShopifyForecastDialog` | Weekly forecast for Shopify bundles |
| `CurationRecipeDialog` | Edit curation recipes, PR-CJAM, CEX-EC assignments with duplicate detection |
| `MonthlyBoxRecipeDialog` | Edit monthly curated box recipes (AHB-MED/CMED/LGE) with overlap detection |
| `CohortManagerDialog` | Manage subscriber cohorts, import from charges CSV |
| `RetentionMatrixDialog` | Edit 7×7 retention matrix, churn rates, repeat rate |
| `OpenPODialog` | Manage open POs/MFG orders/transfers, CSV import |
| `_RetentionComparisonDialog` | Actual vs modeled retention curves with cancellation reason segmentation |
| `_VendorCatalogDialog` | Editable vendor catalog (vendor, unit_cost, case_qty, moq, wheel_weight) |
| `_AutoPOPreviewDialog` | Preview auto-generated POs grouped by vendor with export |
| `_show_snapshot_comparison` | Current vs Potential inventory with color-coded rows (green/amber/red) |
| `_show_transfer_dialog` | Record Woburn->Primary inventory transfers |
| `_show_transfer_history` | Read-only log of past warehouse transfers with CSV export |
| `_show_processing_queue` | Processing queue: add/start/complete AC-/CH- processing jobs, CSV export |
| `_show_yield_discrepancies` | Yield discrepancy flags: acknowledge/resolve snapshot vs yield mismatches |
| `_show_workflow_guide` | Daily/weekly workflow guide with step-by-step instructions |

## Constants

- `CURATION_ORDER`: `["MONG", "MDT", "OWC", "SPN", "ALPT", "ISUN", "HHIGH"]`
- `WHEEL_TO_SLICE_FACTOR`: `2.67`
- `REPEAT_RATE`: `0.56`
- `MONTHLY_BOX_TYPES`: `["AHB-MED", "AHB-CMED", "AHB-LGE"]`
- `MONTHLY_BOX_SLOTS`: Slot templates for each monthly box type (9 slots MED/CMED, 15 slots LGE)
- Defaults: `DEFAULT_RETENTION_MATRIX`, `DEFAULT_CURATION_RECIPES`, `DEFAULT_PR_CJAM`, `DEFAULT_CEX_EC`, `DEFAULT_CHURN_RATES`

## Build

```batch
build_exe.bat
```
Or manually:
```bash
pip install requests pyinstaller
python -m PyInstaller --onefile --windowed --name "InventoryReorder" inventory_reorder.py
```

## Dependencies

- Python 3.8+
- `tkinter` (bundled with Python)
- `requests` (for Recharge/Shopify API — app runs without it if APIs aren't used)

## Backward Compatibility

- Existing flat churn model continues to work if no cohorts are configured
- All new features are additive — no breaking changes to existing data model
- Legacy `bundle_map` coexists with new `curation_recipes`
- Settings JSON is forward-compatible (new keys have defaults)
