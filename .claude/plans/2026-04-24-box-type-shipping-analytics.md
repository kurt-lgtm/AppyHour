# Box-Type Shipping Analytics

**Date:** 2026-04-24
**Owner:** Kurt
**Mode:** STANDARD (3-10 files, cross-module: Kori + shipping_invoice_db + ShippingReports)
**Status:** DRAFT — awaiting user approval

---

## Goal

Enable per-box-size shipping analysis (cost, transit, on-time rates) across 38k historical shipments and new ingest. Surface in Kori UI. Generate executive PDF report for boss (Dan).

## Locked Decisions (from conversation)

1. **Classification function** (`classify_box(line_items) -> str`):
   - `TRAY_LARGE` if ≥7 distinct TR- SKUs
   - `TRAY` if 1–6 distinct TR- SKUs
   - `REGULAR_LARGE` if any `AHB-L*` SKU
   - `REGULAR_MEDIUM` if any `AHB-M*` or `AHB-CMED` SKU
   - `SPECIALTY` if any `AHB-X*` SKU
   - `UNKNOWN` otherwise
   - **Distinct** count (not qty). Only count `fulfillable_quantity > 0`.
   - `AHB-*TRAY*` box SKU without TR- line items → falls through to REGULAR_* (per user).

2. **Storage:** Add `box_type TEXT` column to Kori's existing `shipments` table (`shipping_invoice_db.py`). Indexed.

3. **Backfill source:** Kori's `fulfillments` table (tracking ↔ order_id already populated). Pull line items via existing `ShopifyClient`.

4. **Kori is SQLite-native already.** No JSON→DB migration needed.

## Non-Goals

- Not building a second Kori. Reuse existing shipping tab.
- Not re-parsing FedEx/OnTrac/UPS invoice formats. Classification runs on Shopify line items, joined to shipments by tracking.
- Not changing swap logic, error detection, or demand pipeline.
- Not adding events/issues tables — redundant with Kori's live API calls.

## Architecture

```
Shopify order.line_items ─┐
                          ├→ classify_box() ─→ box_type
fulfillments.tracking ────┘                       │
                                                  ▼
                                     shipments.box_type (indexed)
                                                  │
                         ┌────────────────────────┼────────────────────────┐
                         ▼                        ▼                        ▼
                 Kori Briefing filter    SQL reports (ad-hoc)    PDF export for boss
```

## Phases

### Phase 1: Pure classifier + unit tests (0.5 day)

Create `GelPackCalculator/appyhour/box_classify.py`:

```python
def classify_box(line_items: list[dict]) -> str: ...
```

Input: list of dicts with `sku` + `fulfillable_quantity` keys (Shopify REST/GraphQL shape).

Tests (`tests/test_box_classify.py`):
- Empty → UNKNOWN
- Only `AHB-MED` → REGULAR_MEDIUM
- `AHB-LCUST-MONG` → REGULAR_LARGE
- 3× TR- SKUs → TRAY
- 7× TR- SKUs → TRAY_LARGE
- 8× TR- SKUs → TRAY_LARGE
- `AHB-MCUR-TRAY` + no TR- items → REGULAR_MEDIUM (per user decision)
- `AHB-MCUR-TRAY` + 5× TR- items → TRAY
- `AHB-XMDT` → SPECIALTY
- Mixed: `AHB-LGE` + 8× TR- → TRAY_LARGE (TR- wins)
- `fulfillable_quantity=0` items ignored

**Gate:** Tests pass, pyright clean, ruff clean.

### Phase 2: Schema migration + DB helpers (0.5 day)

Edit `GelPackCalculator/shipping_invoice_db.py`:
- Add `box_type TEXT` to `shipments` CREATE TABLE
- Add migration: `ALTER TABLE shipments ADD COLUMN box_type TEXT` (guarded — only if column missing)
- Add `CREATE INDEX idx_box_type ON shipments(box_type)`
- New helper: `set_box_type(conn, tracking: str, carrier: str, box_type: str)`
- New helper: `stats_by_box_type(conn, since: str | None = None) -> list[dict]` — count, avg cost, avg transit, on-time %, by carrier × box_type
- Extend existing `store_shipments` to accept optional `box_type` parameter

Tests (`tests/test_shipping_invoice_db_box_type.py`):
- Migration idempotent (run twice, no error)
- `set_box_type` upserts
- `stats_by_box_type` returns expected buckets from seeded rows

**Gate:** Schema migration run against a copy of live DB (backup first); column present; no data loss.

### Phase 3: Backfill script (1 day)

Create `GelPackCalculator/backfill_box_type.py`:
- Read Kori `shipping.db` → get distinct (order_id, tracking) pairs from `fulfillments` where `box_type IS NULL`
- Resume-safe: skip already-classified
- Batch Shopify GraphQL pulls, 50 orders/call
- Rate limit: respect 429, retry with backoff
- For each order: run `classify_box(line_items)`, update `shipments.box_type`
- Optional new table `order_classifications (order_id, box_type, skus_json, classified_at)` to cache so reclassification is free
- Progress bar to stdout + periodic commit every 500 rows
- Estimated: ~38k shipments, ~760 API calls, 1-2hr wall clock

Tests:
- Dry-run mode prints stats without writing
- Single-order test against known order

**Gate:** Run against 100-order sample, inspect manually. User approves full run.

### Phase 4: Ongoing classification hook (0.25 day)

Edit Kori's existing Sync All path (`gel_pack_webview.py` — `sync_all` / `rebuild_shipments_from_invoices`):
- When new fulfillments are resolved, call `classify_box` on line items and persist `box_type` alongside shipment.
- No extra Shopify calls — line items already fetched in error-scan path.

**Gate:** Sync All on next invoice batch → new rows have `box_type` populated.

### Phase 5: Kori UI — Briefing filter dropdown (0.5 day)

Edit `GelPackCalculator/kori/web_ui/shipping.js`:
- Add dropdown in briefing header: `All | Regular Med | Regular Lge | Tray | Tray Lge | Specialty | Unknown`
- Wire to `getCleanShipments()` — filter array by selected `box_type` before rollups
- Default: All
- Persist selection in localStorage

Edit `shipping.html` + `shipping.css`:
- Dropdown styled to match existing tab chrome

**Gate:** User test — filter on "Tray" shows only tray-box shipments in all briefing charts.

### Phase 6: PDF executive report (1 day)

New Kori JS API method in `gel_pack_webview.py`:
- `export_box_size_report(date_range: str) -> {"path": str}`
- Generates PDF via `fpdf2` (already bundled)

PDF contents:
- **Page 1 — Summary**: Title, date range, total shipments, breakdown by box_type
- **Page 2 — Cost comparison**: Table + bar chart, cost/shipment by box_type × carrier
- **Page 3 — Transit & on-time**: Avg transit days, on-time % by box_type × carrier
- **Page 4 — Weekly trend**: Last 12 weeks, cost and volume per box_type
- **Page 5 — Top problem zones**: Zip-level failure rate per box_type

Save to `~/.knowledge/reports/box-size-YYYY-MM-DD.pdf`. Auto-open.

New button in Kori Briefing tab: "Export Report for Dan".

**Gate:** Generate sample report, inspect visually, confirm data accuracy against SQL ground truth.

### Phase 7: Documentation (0.25 day)

- Update `~/.knowledge/ops/Shipping Data Pipeline.md` with box_type column + classifier location
- Add `~/.knowledge/domain/Box Type Classification.md` — rules, examples, edge cases
- Add memory: `feedback_box_type_classification.md` — distinct TR- count, not qty

## File Changes

**New:**
- `GelPackCalculator/appyhour/box_classify.py`
- `GelPackCalculator/backfill_box_type.py`
- `tests/test_box_classify.py`
- `tests/test_shipping_invoice_db_box_type.py`
- `~/.knowledge/domain/Box Type Classification.md`

**Modified:**
- `GelPackCalculator/shipping_invoice_db.py` (schema + 2 helpers)
- `GelPackCalculator/kori/gel_pack_webview.py` (Sync All hook + PDF export method)
- `GelPackCalculator/kori/web_ui/shipping.js` (filter dropdown + wire to getCleanShipments)
- `GelPackCalculator/kori/web_ui/shipping.html` (dropdown markup + export button)
- `GelPackCalculator/kori/web_ui/shipping.css` (dropdown styling)
- `~/.knowledge/ops/Shipping Data Pipeline.md` (box_type section)

## Risks

| # | Risk | Mitigation | Severity |
|---|------|-----------|----------|
| 1 | Shopify rate limit during 38k backfill | Batch 50/call, 429 retry w/ backoff, resume-safe | 🟡 medium |
| 2 | Orders with multiple fulfillments (multi-package) classify at order-level, shipment-level inherits same box_type | Accept noise in v1; flag for v2 if reports show distortion | 🟡 medium |
| 3 | Old orders (pre-TRAY product) missing line items in Shopify archive | Leave as `UNKNOWN`; exclude from reports or show as own bucket | 🟢 low |
| 4 | `AHB-MCUR-TRAY` with 0 TR- items classified as REGULAR_MEDIUM is actually a tray | User confirmed acceptable; flag count in report footer so boss sees if it grows | 🟢 low |
| 5 | Migration breaks Kori's existing shipping.db | Back up DB before ALTER; test on copy first | 🔴 high mitigation: 🟢 low residual |
| 6 | PDF layout breaks with edge case data (all-UNKNOWN date range) | Handle gracefully: "Insufficient classified data — run backfill" | 🟢 low |

## Testing Strategy

- **Unit:** `classify_box()` — 11+ cases covering all branches + edge cases
- **Unit:** DB helpers — migration idempotency, stats correctness
- **Integration:** Backfill against 100-order sample, compare to manual classification
- **UAT:** User filters Kori briefing by each bucket, confirms charts update correctly
- **Data accuracy:** PDF numbers cross-checked against raw SQL

## Delivery Strategy

- Feature branch: `feat/box-type-shipping-analytics`
- Atomic commits per phase
- Merge to main after Phase 6 UAT passes
- No PR review needed (solo project); direct merge

## Negative Constraints (Do NOT)

- Do NOT add separate `events` or `issues` tables — redundant with live API paths
- Do NOT migrate ShippingReports/output/shipments.db into Kori — they serve different purposes
- Do NOT change `classify_box()` rules without user approval — locked contract
- Do NOT include Recharge subscription pulls in backfill — Shopify line items are sufficient
- Do NOT rebuild Kori's Briefing from scratch — add filter to existing code
- Do NOT touch swap logic, cut order, or error detection scanners
- Do NOT refactor `shipping_invoice_db.py` beyond additive schema changes

## UNVERIFIED claims (confirm during implementation)

- `UNVERIFIED`: Kori's `fulfillments` table has `tracking` + `order_id` populated for all 38k shipments. If coverage <90%, backfill yield is lower than advertised — may need parallel Shopify tracking-number search fallback.
- `UNVERIFIED`: `fpdf2` is actually imported in Kori's runtime (claimed in `GelPackCalculator/CLAUDE.md` tech stack but not confirmed with import check).
- `UNVERIFIED`: Kori's Sync All path actually fetches line items during error-scan. If not, Phase 4 becomes "add line items to that fetch" — extra scope.

## Estimated Effort

| Phase | Hours |
|-------|-------|
| 1. Classifier + tests | 4 |
| 2. Schema + DB helpers | 4 |
| 3. Backfill script | 6 |
| 4. Ongoing hook | 2 |
| 5. Kori filter UI | 4 |
| 6. PDF report | 8 |
| 7. Docs | 2 |
| **Total** | **30 hrs** (~4 working days) |

Minimum viable boss-ready: Phases 1-3 + 6 (~22 hrs, 3 days). Filter UI (Phase 5) can slip if PDF is the priority deliverable.

## Sequencing Options

🟢 **Full plan** — all 7 phases in order (4 days)
🟡 **Boss-priority** — Phases 1, 2, 3, 6, 7 (skip Kori UI for now, 3 days, PDF ready fastest)
🔵 **SQL-only MVP** — Phases 1, 2, 3 only; user writes ad-hoc SQL until ready for UI/PDF (1.5 days)

## Approval Required

- [ ] Classifier rules correct (see Locked Decisions #1)
- [ ] Backfill approach (Kori fulfillments table as join source) acceptable
- [ ] Sequencing option chosen (Full / Boss-priority / SQL-only)
- [ ] OK to proceed to Phase 4 IMPLEMENT after approval
