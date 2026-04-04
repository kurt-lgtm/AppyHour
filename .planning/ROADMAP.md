# Roadmap: Cut Order Consolidation (v1.1)

## Overview

Three phases that fix demand accuracy, deliver a polished v2 Cut Order XLSX, and unify duplicated logic into a shared module. Phase 8 delivers the immediate value (correct numbers + polished XLSX), Phase 9 removes hardcoded dates and adds auto-discovery, and Phase 10 eliminates the triple code duplication. After Phase 8, the operator has a production-ready XLSX. After Phase 10, all demand logic lives in one place.

## Phases

**Phase Numbering:**
- Continues from v1.0 milestone (ended at Phase 7)
- Integer phases (8, 9, 10): Planned milestone work
- Decimal phases (8.1, 8.2): Urgent insertions if needed

- [ ] **Phase 8: XLSX v2 + Demand Fixes** — Polished 3-tab workbook with correct demand, urgency grouping, raw materials, and assignment tables
- [ ] **Phase 9: Parameterized Dates + Auto-Discovery** — Remove hardcoded dates, auto-discover new SKUs, fix first-order projection
- [ ] **Phase 10: Shared Demand Module** — Extract shared resolution logic, retire duplicated code paths

## Phase Details

### Phase 8: XLSX v2 + Demand Fixes
**Goal**: Operator receives a polished, correct Cut Order XLSX every Wednesday — urgency-grouped, demand broken out by source, raw materials visible, assignments editable
**Depends on**: Nothing (first phase of milestone)
**Requirements**: DEM-01, CUT-01, CUT-02, CUT-03, CUT-04, CUT-05, CUT-06, RAW-01, RAW-02, RAW-03, RAW-04
**Success Criteria** (what must be TRUE):
  1. v2 XLSX generates without errors from live Recharge + Shopify data
  2. Demand numbers match the Recharge queued charges CSV (no MONTHLY exclusion gap)
  3. SKUs are grouped by urgency (Shortage/Tight/Healthy) with conditional formatting
  4. Raw Materials tab shows cheese wheels with potential slices and bulk accompaniments with potential packets
  5. Assignments tab has PR-CJAM, CEX-EC tables with editable cheese cells that feed SUMIF into Cut Order tab
  6. MONTHLY box slot tables show WK1/WK2 counts separately, split by charge month when changeover occurs
**Plans**: TBD

### Phase 9: Parameterized Dates + Auto-Discovery
**Goal**: Operator can generate cut orders for any week without editing code; new SKUs are automatically surfaced
**Depends on**: Phase 8
**Requirements**: DEM-02, DEM-03, DEM-04
**Success Criteria** (what must be TRUE):
  1. Running the XLSX builder with `--week 2026-04-18` generates for that week's date range (no code edits)
  2. SKUs appearing on Shopify orders in the last 90 days that aren't in the inventory CSV are flagged in a "New SKUs" section
  3. First-order projection calculates days-to-Friday correctly regardless of which day the script runs
  4. Ship tags are derived from the week parameter, not hardcoded
**Plans**: TBD

### Phase 10: Shared Demand Module
**Goal**: All demand resolution logic lives in one importable module — XLSX builder, web app, and cut_order_generator all share the same code
**Depends on**: Phase 8
**Requirements**: CON-01, CON-02, CON-03, CON-04, CON-05
**Success Criteria** (what must be TRUE):
  1. A shared module (e.g. `appyhour/demand.py`) exports resolve_demand, resolve_pr_cjam, resolve_cex_ec, normalize_sku, is_pickable, resolve_curation
  2. build_cut_order_xlsx_v2.py imports from the shared module (no inline resolution logic)
  3. fulfillment_web/app.py imports from the shared module (existing tests still pass)
  4. cut_order_generator.py either imports from shared module or is retired with a pointer to the v2 builder
  5. MONTHLY_BOX_SLOTS defined in one place, imported by all consumers
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 8 → 9 → 10 (Phase 9 and 10 can run in parallel after 8)

| Phase | Plans Complete | Status | Completed |
|-------|---------------|--------|-----------|
| 8. XLSX v2 + Demand Fixes | 0/? | In Progress | - |
| 9. Parameterized Dates + Auto-Discovery | 0/? | Not started | - |
| 10. Shared Demand Module | 0/? | Not started | - |
