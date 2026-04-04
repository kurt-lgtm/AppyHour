# Requirements: Cut Order Consolidation (v1.1)

**Defined:** 2026-04-04
**Core Value:** Single source of truth for demand calculation and cut order generation — accurate numbers, no duplicated logic, polished operator XLSX.

## v1 Requirements

### Demand Accuracy (DEM)

- [x] **DEM-01**: All pickable items from Recharge charges counted directly, including MONTHLY boxes (no exclusion)
- [ ] **DEM-02**: Auto-discover CH-/MT-/AC- SKUs from Shopify orders in last 90 days not in inventory settings
- [ ] **DEM-03**: First-order projection works correctly for any day of the week
- [ ] **DEM-04**: Date ranges (WK1/WK2 boundaries, ship tags) parameterized via CLI args or settings, not hardcoded

### Cut Order XLSX (CUT)

- [ ] **CUT-01**: v2 XLSX with urgency-grouped rows (Shortage / Tight / Healthy)
- [ ] **CUT-02**: Demand breakdown columns showing RC direct, Shopify addon, +CJAM, +CEXEC separately
- [ ] **CUT-03**: Raw Materials tab showing cheese wheels (potential slices) and bulk accompaniments (potential packets)
- [ ] **CUT-04**: Assignments tab with PR-CJAM, CEX-EC, splits, and MONTHLY box slot tables
- [ ] **CUT-05**: WK1 and WK2 separated in MONTHLY box slot tables, with month changeover support
- [ ] **CUT-06**: Audit trail — data source counts (RC charges, SH orders, generation timestamp) in subtitle

### Logic Consolidation (CON)

- [ ] **CON-01**: Extract shared demand resolution module (resolve_demand, resolve_pr_cjam, resolve_cex_ec)
- [ ] **CON-02**: XLSX builder imports shared module instead of duplicating resolution logic
- [ ] **CON-03**: cut_order_generator.py imports shared module or is retired
- [ ] **CON-04**: Monthly box slot definitions in one place (not duplicated across files)
- [ ] **CON-05**: normalize_sku, is_pickable, resolve_curation shared across all consumers

### Raw Materials (RAW)

- [ ] **RAW-01**: Cheese wheel inventory extracted from inventory CSV via extract_bulk_weights()
- [ ] **RAW-02**: Bulk accompaniment inventory extracted from CSV + bulk_conversions settings
- [ ] **RAW-03**: Wheel potential shown per-SKU in cut order (Wheel Pot. column)
- [ ] **RAW-04**: Status indicators on raw materials (HIGH / MED / LOW / EMPTY)

## v2 Requirements

### Automation

- **AUTO-01**: One-click generation from fulfillment web app (no standalone script needed)
- **AUTO-02**: Google Drive upload integrated into web app UI
- **AUTO-03**: Cut order tab in fulfillment web app uses v2 layout with interactive assignment editing

## Out of Scope

| Feature | Reason |
|---------|--------|
| Matrix Commander pipeline | Separate milestone (v1.0) — different workflow (Saturday fulfillment) |
| React tool integration | External developer, separate timeline |
| Charge detection / Saturday automation | Future milestone (autopilot plan) |
| Web app cut order pane redesign | v2 requirement — after XLSX proven correct |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| DEM-01 | Phase 8 | Complete |
| CUT-01 | Phase 8 | In Progress |
| CUT-02 | Phase 8 | In Progress |
| CUT-03 | Phase 8 | In Progress |
| CUT-04 | Phase 8 | In Progress |
| CUT-05 | Phase 8 | In Progress |
| CUT-06 | Phase 8 | In Progress |
| RAW-01 | Phase 8 | In Progress |
| RAW-02 | Phase 8 | In Progress |
| RAW-03 | Phase 8 | In Progress |
| RAW-04 | Phase 8 | In Progress |
| DEM-02 | Phase 9 | Pending |
| DEM-03 | Phase 9 | Pending |
| DEM-04 | Phase 9 | Pending |
| CON-01 | Phase 10 | Pending |
| CON-02 | Phase 10 | Pending |
| CON-03 | Phase 10 | Pending |
| CON-04 | Phase 10 | Pending |
| CON-05 | Phase 10 | Pending |

**Coverage:**
- v1 requirements: 19 total
- Mapped to phases: 19
- Unmapped: 0

---
*Requirements defined: 2026-04-04*
*Last updated: 2026-04-04 after milestone v1.1 definition*
