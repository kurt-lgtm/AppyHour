# ShippingReports

Analytics pipeline for subscription box shipping. Ingests carrier invoices (OnTrac, UPS, FedEx, Veho), Gorgias issues, Parcel Panel tracking → routing recommendations, cost analysis, performance reports.

> 🔴 **Reading shipment data out of DigitalOcean? Read [`DO_READ_CONTRACT.md`](DO_READ_CONTRACT.md)
> FIRST** — the consumer-side SSOT (SPEC, 2026-08-27) for the move off local ParcelPanel pulls onto
> DO as the single ingest. It owns: which tables are IN scope vs blocked (🔴 `fulfillments` and
> `feedback` have **no cloud writer** — keep reading local; cloud `shipments.ship_date` holds **two
> date formats**, so `MAX()`/`BETWEEN` on it are wrong until the ingest normalizes), the
> **order_number** join key (BARE digits, never tracking — FedEx reuses them), the timezone contract
> (`synced_at` is **UTC**; all date math → America/New_York before `.date()`), the per-consumer
> freshness tolerances **with the assert that proves each**, and the non-goals — 🔴 the Apps Script
> exceptions sweep **cannot reach either database** and keeps calling PP directly (out of scope),
> and the FedEx-113/UPS invoice **download** is permanently manual. Recommended read path =
> `ShipRouting/lib/histdb.py`.

> 🔴 **Reship report** (HEADLESS, live): any change to it reads [`RESHIP_REPORT_RULES.md`](RESHIP_REPORT_RULES.md)
> FIRST (constraints SSOT, APPROVED). Canonical = PIVOT sheet `1weQz0AOAZJu7-I2reZ8fIqQ_b10BKWd4sYHn5HAUkGU`,
> bound Apps Script `appsscript/Code.gs` (hourly). Tabs: Raw Data · Triage · Product Mix · Reship (ex-`Product Mix (T)`)
> · Daily · **Hold** (the `_HOLD`→`_CSHOLD`/`_FLOWHOLD`/`_UNRESOLVED` migration backlog — 🔴 **WRITE-ONCE
> per date**, a hold snapshot cannot be back-filled; rules in RESHIP_REPORT_RULES **D33**); a **Reship Report**
> custom menu drives manual refreshes. Carrier = Parcel Panel (Script Property
> `PARCELPANEL_API_KEY`). Local mirror = `scratchpad/rebuild_mix_triage.py`.
> 📖 **Per-tab NORTH STAR + gotchas (read before quoting any tab):** [`TAB_NORTH_STARS.md`](TAB_NORTH_STARS.md).
>
> 🔴 **Carrier Mix pivot** = `carrier_mix_pivot.py` (read-only, `connect_ro`) — ship weeks as columns,
> one count row + one cost row per carrier·service lane. Rules SSOT = [`RESHIP_REPORT_RULES.md`](RESHIP_REPORT_RULES.md)
> **D35**; read it first. **NOT an Apps Script tab** (the `.gs` project cannot reach `shipping.db`, where
> the routing-tag service token and the carrier-invoice cost both live). 🔴 FedEx 2Day is ALWAYS its own
> row, never merged into Ground-HD; OnTrac/LaserShip are ONE carrier via `canon.normalize_carrier`.
> Counts and costs freeze on **two independent clocks**. ⚠️ **UNOWNED — no scheduled owner yet** (D35).
> 🔴 **D41** — the tab paint is GATED per column: a cohort is paintable only once its week has closed
> (Wed) **and** its Tuesday leg is actually in `fulfillments`. A weekday check alone is not enough —
> the `2026-08-24` column published the Monday-only **2,500** (true 2,545) from a *Wednesday* run,
> because the ingest, not the week, was still open. Every column now carries `Counts basis` +
> `Counts as_of`. Sibling guard on `TnT2` (`PivotAnalytics.gs`): the ceiling assert that catches its
> `2,227`-against-`2,226`. 🔴 The two tabs count DIFFERENT populations (orders vs `fulfillments`
> rows) — never use one as the other's ceiling.
> 🔴 **D44** — the COST half now carries `Cost basis` + `Cost as_of` as their OWN rows (the two clocks
> freeze independently; one date cannot describe both). `Cost basis` names the **store** —
> `shipments@local (carrier invoices)` — because "local or cloud?" is the question a published dollar
> could not answer when the CLOUD `shipments` table was deduped on 2026-09-06 (22,693 rows / $301,596).
> It could not have been affected: `connect_reporting` **raises** on `shipments` (only `delivery_status`
> is cloud-cleared), and local is 98,432 rows / 98,432 distinct trackings with zero `/tmp` copies.
> 🔴 Preview a repaint with **`--dry-run-sheet`** (cell-by-cell diff, read-only scope, writes nothing)
> before ever running `--write-sheet`. 🔴 **Never repaint with fewer `--weeks` than the tab already
> shows** — the tab is cleared and rewritten WHOLE, so a narrower window DELETES published columns.
> ⚠️ The tab is currently **unpaintable**: `CM_ASSERT_FROZEN_COUNTS` refuses `_SHIP_2026-08-10` (frozen
> 1534/458/175 vs a recompute 4 boxes lower — the mutable-tag drift D35 predicts). Kurt's call.
>
> 🔴 **Weekly reship report (one tab per week)** = `ingest/slack_reship/weekly_task.py` →
> `sync.py --report --push` → `sheet_push.py`, owner: the `weekly-reship-report` routine, beat
> `slack-reship` (10d). Rules SSOT = [`RESHIP_REPORT_RULES.md`](RESHIP_REPORT_RULES.md) **D40**;
> read it first. 🔴 It reports the **last COMPLETE** Mon–Sun week, never the week in progress —
> reporting the current week put **denom 0** on the `2026-07-20` tab and **denom 2** on `2026-08-10`
> (true cohorts 2082 / 2365). 🔴 `assert_denom_publishable()` refuses the write on a zero or
> below-floor denominator and PROVES which zero it is with a control probe; never route around it
> with `--denom` or a different `--week`. 🔴 The `slack-reship` beat fires only when `push()`
> returned a URL. 🔴 Its inputs are `fulfillments` ONLY — not `shipments`, not `delivery_status`.
>
> 🔴 **`Vendor Matrix` tab** = the durable HISTORY of the weekly carrier×issue matrix —
> `AppyHour/ingest/slack_reship/matrix_history.py`, written by
> `sync.py --report --history-sheet` (owner: the `weekly-shipping-vendor-matrix` routine, beat
> `vendor-matrix`, 10d threshold). Same shape as `Carrier Mix`: ship weeks as columns, ledger
> (`_outputs/reports/vendor_matrix_ledger.json`) is the MEMORY, tab is a VIEW. Rules SSOT =
> [`RESHIP_REPORT_RULES.md`](RESHIP_REPORT_RULES.md) **D39**; read it first. 🔴 The routine's Slack
> DM **posts every week and must never be made exception-only** — it is a report, not a monitor
> (Kurt 2026-08-31). 🔴 Counts come from Slack, never `feedback.issue_type`; `denom == 0` refuses
> the whole write rather than publishing a 0-denominator rate.

## Task Routing

| Task | Read | Skip | Notes |
|------|------|------|-------|
| Weekly report | `reports/weekly.py`, `~/.knowledge/ops/Crossdock*` | parsers | `python -m reports.weekly` |
| Routing recommendation | `reports/recommend.py` → outputs `routing_config.json` | parsers | Consumed by GelPackCalculator |
| Ingest new invoice | `ingest.py`, `parsers/<carrier>` | reports | OnTrac CSV / UPS CSV / FedEx XLSX / Veho via sync_all_carriers |
| Veho-specific | `~/.knowledge/shipping_db_path.md`, sync_all_carriers patched 2026-05-06 | non-Veho | shipments.db = canonical (with Veho); Kori-shipping.db has no Veho |
| Cohort attribution | `cohort_attribution.py`, `~/.knowledge/cohort_attribution_rules.md` | rest | Veho=tender+Mon; FedEx/OT/UPS=pickup→Mon; acct -113/-911 by bill-to |
| Failure analysis | `kori_snapshots/kori_snapshot_orders` (added 2026-05-07) | rest | appyhour-shipping MCP data source |

## Critical

- **HQ Woburn decommissioned** Feb 2026 — pre-Feb HQ_IGNORE = real customers; post-Feb = anomaly. `is_internal != HQ_IGNORE`.
- **TNT calc HARD RULE** — final-mile pickup→delivery only; never carrier API `transit_time`. Veho: PP `pickup_date`, not `Tendered`.
- **FedEx contract 2389560254** earn-floor $390k = 7%; 160/day min currently 91/57% = BREACH RISK. No Express service.
- **2-day mandatory** all shipments. Tue = Dallas-only hub. Zone5+ from Dallas needs 2DayExpress.
- 🔴 **A tracking's cost is the SUM of its invoice lines — never one line.** UPS bills a tracking
  across a freight line plus accessorials and after-the-fact `Adjustments & Other Charges/...`
  corrections. A parser that emits ONE ROW PER LINE hands the `shipments` upsert
  (`ON CONFLICT(tracking) DO UPDATE SET cost=excluded.cost`) an arbitrary survivor — locally the
  LAST line, in the cloud the FIRST. **Burn 2026-09-07:** `1Z2H94940334864194` in
  `AHB_00356_UPS Shipping Breakdown_AHB_6-1-26.csv` is billed **$18.08** `Ground Residential /
  Outbound/Shipping API` + **$1.40** `Shipping Charge Corrections`. True charge **$19.48**; local
  stored **$1.40**, cloud stored $18.08 — a $1.40 UPS ground parcel that nobody questioned. 20
  trackings, **$490.76** understated on the published UPS cost row.
  Rules for ANY new or edited invoice reader:
  * Aggregate by tracking BEFORE returning. `_parse_ups_billing_data` (headerless v2.1) has done
    this since it was written and is the reference shape; `_parse_ups_headed_csv` was brought into
    line 2026-09-07.
  * A **credit / negative adjustment SUBTRACTS** (real case: `1ZC411H40311605473` = 14.92 − 0.61 =
    **$14.31**). Never drop it, never `abs()` it.
  * Non-cost fields come from the **freight** line. Adjustment lines carry thin metadata — the
    address-correction line on `1ZC411H40318992015` has blank city/state/zip, zone `000`, and a
    pickup date four days after the shipment's.
  * `shipments` has no column recording how many lines were summed. Multi-line trackings are
    written to **stderr at parse time** so the ingest log carries the provenance; if that proves
    insufficient the smallest fix is `ALTER TABLE shipments ADD COLUMN charge_lines INTEGER`.
  * Do NOT sniff the dialect from the header alone. OnTrac 'Shipping Breakdown' CSVs also carry a
    `Tracking Number` column, so a header sniff routes 14 OnTrac files into the UPS reader and
    yields confident $0.00 costs. Select by the **filename token**, same authority as carrier.
  Guard: `tests/test_ups_headed_csv_line_summing.py` (every value pinned from a real invoice).
  Repair for the already-stored rows: `_outputs/scripts/ups_headed_cost_repair.py` (dry-run
  default). Re-ingest is idempotent — `store_shipments` upserts on `tracking`, never appends.
- 🔴 **Carrier comes from the FILENAME TOKEN, never the parser path.** An OnTrac parser run over a
  UPS file stamped `carrier='OnTrac'` on 61 `1Z...` trackings from
  `AHB_00350_UPS Shipping Breakdown_AHB_5-25-26.csv` — ~$829 of UPS cost published on the OnTrac
  row. `auto_import.rmfg_carrier()` fixed the code path; the stored rows are repaired by
  `_outputs/scripts/cleanup_invoice_headers.py --commit`, which as of 2026-09-07 has still not been
  run. A `1Z` prefix is unambiguously UPS — a tracking-prefix sanity check beside the filename-token
  rule would have caught this at write time.

## Overview
Analytics pipeline for subscription box shipping optimization. Ingests carrier invoices (OnTrac, UPS, FedEx), customer issue data (Gorgias), and tracking events (Parcel Panel) to generate routing recommendations, cost analysis, and performance reports.

## Architecture
- `parsers/` — Standardized invoice/data parsers (OnTrac CSV, UPS CSV, FedEx XLSX, Gorgias, Parcel Panel)
- `reports/` — Analysis modules (cost, transit, misrouting, weather normalization, zip-level)
- `data/` — Raw invoice/issue files (symlinked or copied from GelPackCalculator/Invoices)
- `output/` — Generated reports and routing config recommendations

## Relationship to GelPackCalculator
This project analyzes historical data and outputs routing config recommendations.
GelPackCalculator (`../GelPackCalculator/`) is the real-time execution app that applies those configs to live Shopify orders.

Flow: Invoices → ShippingReports → routing_config.json → GelPackCalculator imports as profile

## Build & Run
```
python -m reports.weekly    # Generate weekly report
python -m reports.recommend # Generate routing config recommendation
python ingest.py            # Parse new invoice files
```

## Dependencies
- Python 3.x (Anaconda: `/c/Users/Work/anaconda3/python.exe`)
- openpyxl (for FedEx XLSX parsing)
- requests (for Gorgias API, future)
- No paid services required (n8n alternative: native Python scheduling)

## Hub Definitions
- Dallas (TX) — Garland, TX 75042. Ships OnTrac, UPS, FedEx. Only hub on Tuesdays.
- Nashville (TN) — Nashville, TN 37210. Ships OnTrac, FedEx. Primary eastern hub.
- Anaheim (CA) — Anaheim, CA. Ships OnTrac, FedEx. West coast hub.
- Indianapolis (IN) — Indianapolis, IN 46204. FedEx overflow for rural eastern zips.
- Woburn (MA) — Company HQ, not a fulfillment hub. Ignore in analysis.

## Carrier Invoice Formats
- OnTrac: CSV, hub in Reference1 field, has First Scan/POD DateTime for transit calc
- UPS: CSV (Invoice_000000C411H40*.csv), hub in Reference No.2, no delivery date
- FedEx: XLSX (AHB_*_FedEx Shipping Breakdown*.XLSX), hub from Shipper City/State, has POD date
