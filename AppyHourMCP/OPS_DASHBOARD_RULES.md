# OPS_DASHBOARD_RULES.md — single source of truth · change rules HERE first

🔴 **PRE-CHANGE GATE:** read this before touching the ops dashboard pipeline
(`AppyHourMCP/tools/ops_dashboard_builder.py`, tabs `DATA_Weekly` + `Dashboard`
in spreadsheet `190AmXF8hy-M8lmt8q9uhOkyOMi7AmU0jJAd1KOpjWdA`).

## 🧭 North Star
One glance tells Kurt/Dan whether shipping+CS issues are getting better or worse
this week vs the last six — rates, costs, and mix, computed from the two systems
of record, refreshed automatically, failing loudly when a feeder dies.

## Negatives first — the failure this replaces (2026-07-27)
- 🔴 **Never hardcode cross-tab cell refs** (`='Ops Summary Report '!D28`). That
  design scrambled the old Dashboard the moment the feeder layout drifted, and it
  showed GARBAGE SILENTLY for months. Dashboard formulas may reference ONLY
  `DATA_Weekly` fixed columns; the builder owns that schema.
- 🔴 **One writer.** `ops_dashboard_builder.py` is the only thing that writes
  `DATA_Weekly` and the Dashboard formula block. No hand edits, no second script.
  (Three-writer fields are how Contact Reason rotted.)
- 🔴 **Denominators come from `shipping.db.fulfillments`** (canonical
  `C:\AppyHourData\shipping.db` via `appyhour_lib.paths.db_path()`), NEVER from a
  sheet tab. The old `Shipments` tab died 03/16/2026 and nothing noticed.
- 🔴 **No fabricated data:** weeks with a dead feeder render as blank + a loud
  `STALE` cell, never carried-forward numbers.
- Issue counts come from `UPDATE_Operational Issues` (raw tab of record). Its
  known limits apply: Gorgias-side undercount vs Slack until categorization is
  fixed; NO_SOURCE_SIGNAL rows are excluded from nothing (they carry an Issue
  Type or don't — counts use col H as-is).

## Tab contract
- **`DATA_Weekly`** (fixed schema, row 1 headers, one row per Mon-start week,
  last 12 weeks, oldest→newest):
  `week_start | orders_shipped | issues_total | warm | delayed | lost_misdeliv |
   damaged | undeliverable | fulfillment_issues | reships_requested | credits |
   refunds | resolution_cost | issue_rate | reship_rate | cost_per_order`
- 🔴 **`reships_requested` comes from the RESHIP REPORT Raw Data**
  (sheet `1weQz0AO…`, dedup by Order, bucketed by Requested week) — NEVER from
  the Gorgias Resolution field, which counts intentions (denied/duplicate
  reships; measured +14% to +63% over reality, 2026-07-28). One source of
  record per number. Cost model: each reship costed at $65 (full) — partials
  aren't split in Raw Data; documented overstatement, not fabrication.
  Credits/refunds stay Gorgias-resolution-sourced (order-deduped).
- Weeks fully before the raw tab's earliest receipt date render BLANK, not zero.
- **`Dashboard`**: KPI rows + `SPARKLINE` over `DATA_Weekly` columns only.
- Issue classes map from col-H Issue Type by substring, first match:
  Arrived Warm→warm · Delayed→delayed · Lost/Misdeliver→lost_misdeliv ·
  Ice Pack/Damaged/Box→damaged · Cannot be→undeliverable · `Order::*`→fulfillment.
  Multi-issue cells (comma-joined) count once per class present.
- Costs: `RESOLUTION_COSTS` table (imported from `ops_summary_builder.py` — do
  not fork it). Unknown/blank resolution = $0 and counted in `credits`? NO —
  unknown = uncosted, excluded from resolution counts (never guessed).
- Rates: `issues_total / orders_shipped` and `(full+partial reships) /
  orders_shipped`, per week; orders_shipped = fulfillments rows tagged
  `_SHIP_<that Monday>` excluding `Reship`-tagged outbound (reship-exclusion
  rule, metric definition 2026-07-09).

## Cadence & freshness
- Runs inside the Wednesday `ops-issues-weekly-update` task AFTER sync+enrich;
  CLI: `python AppyHourMCP/tools/ops_dashboard_builder.py --commit`.
- Freshness: builder stamps `DATA_Weekly!A1` note with run timestamp; if
  `fulfillments` max age >3d it writes the STALE banner instead of numbers
  (freshness_sweep also alarms independently).

## Retired (2026-07-27)
`Shipments`, `Ops Summary Report`, `Cost of Issues` (old form) — renamed
`zz_archive_*`, kept read-only for history, feed nothing. Deleting them outright
is a Kurt decision. `Cost of Issues` lifetime summary preserved in the archive
($52,852.50 / 1,380 issues through 07/06/2026).

## CHANGE LOG
- 2026-07-28 · v1.1 · reship columns rewired to reship-report Raw Data (Kurt:
  report is more accurate — Shopify-verified vs CS-entered Resolution field);
  order-dedup on credits/refunds; pre-coverage weeks blank; % / $ formats;
  "wk-to-date" labels.
- 2026-07-27 · v1 · initial build replacing hardcoded-ref Dashboard (Kurt "go").
