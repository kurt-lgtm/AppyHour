# Requirements: Matrix Commander

**Defined:** 2026-04-04
**Core Value:** Produce a correct, ready-to-email RMFG production sheet in under 15 minutes, including gift orders

## v1 Requirements

### Pipeline Infrastructure (PIPE)

- [ ] **PIPE-01**: Checkpoint/resume — JSON-based crash recovery so failure mid-batch doesn't restart from order 0
- [ ] **PIPE-02**: Rate limiter — leaky-bucket throttle for Shopify GraphQL with exponential backoff on 429s (replace sleep-based)
- [ ] **PIPE-03**: Pipeline state machine — step-by-step orchestrator with pass/fail tracking per stage
- [ ] **PIPE-04**: Dry-run mode — preview what sync-shopify would do without touching Shopify (already partial in web)

### Order Sync (SYNC)

- [ ] **SYNC-01**: sync-shopify works against live Shopify orders (battle-test existing implementation)
- [ ] **SYNC-02**: Two-pass flow verified live — PR-CJAM first, verification gate, then all others
- [ ] **SYNC-03**: Idempotent edits verified live — safe to re-run without creating duplicate line items
- [ ] **SYNC-04**: Partial failure handling — track which orders failed, retry just those (via checkpoint)
- [ ] **SYNC-05**: Handle orderEditCommit error-but-applied bug — verify state before retry

### Matrix Generation (MATRIX)

- [ ] **MATRIX-01**: generate command works against live Shopify data (battle-test existing implementation)
- [ ] **MATRIX-02**: Gift order handling verified live — detect, assign children at matrix level, merge into final sheet
- [ ] **MATRIX-03**: finalize produces correct RMFG-ready XLSX (tab rename, ProductionDay, zips, sort, auto-name)
- [ ] **MATRIX-04**: MFG name validation catches all unmapped SKUs before sending

### Inventory (INV)

- [ ] **INV-01**: Inventory sync — push calculated inventory to Shopify paid + $0 variants
- [ ] **INV-02**: Shortage detection verified live — cross-check demand vs inventory, flag shortages
- [ ] **INV-03**: Swap resolution verified live — interactive shortage swaps with substitution families
- [ ] **INV-04**: Dietary restriction swap exclusion — NNRS/CORS/NCRS orders excluded from auto-swaps

### End-to-End (E2E)

- [ ] **E2E-01**: Full Saturday flow works end-to-end: inventory → sync (pass 1) → verify → sync (pass 2) → generate → finalize → email-ready
- [ ] **E2E-02**: Web UI supports the full flow (not just CLI)
- [ ] **E2E-03**: All commands produce correct output against live data (not just unit tests)

### Integration (INTG)

- [ ] **INTG-01**: Webhook endpoint — React tool POST triggers Matrix Commander post-processing
- [ ] **INTG-02**: Code documented as spec for React dev absorption (typed inputs/outputs, pure functions)

## v2 Requirements

### Automation

- **AUTO-01**: Charge detection — poll Shopify orders by tag every 5 min, detect batch completion
- **AUTO-02**: Pre-flight Friday automation — freeze inventory, pull Recharge upcoming, shortage simulation
- **AUTO-03**: Pre-position Friday night — apply routing tags, zip overrides, sync inventory
- **AUTO-04**: Operator notification — phone alert when orders ready

### Operator Experience

- **OPS-01**: Operator dashboard — single-screen view for any trained person
- **OPS-02**: Tuesday cycle automation — same pipeline, smaller batch
- **OPS-03**: Swap at Recharge bundle_selections level for persistent swaps

## Out of Scope

| Feature | Reason |
|---------|--------|
| React tool allocation logic | Owned by separate developer |
| Full Option 1 (React absorbs everything) | Future — after Matrix Commander proves the logic |
| Mobile app / notifications | Desktop-only workflow |
| Multi-user / auth | Single operator tool |
| Staging environment | No Shopify staging available — test against live |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| PIPE-01 | TBD | Pending |
| PIPE-02 | TBD | Pending |
| PIPE-03 | TBD | Pending |
| PIPE-04 | TBD | Pending |
| SYNC-01 | TBD | Pending |
| SYNC-02 | TBD | Pending |
| SYNC-03 | TBD | Pending |
| SYNC-04 | TBD | Pending |
| SYNC-05 | TBD | Pending |
| MATRIX-01 | TBD | Pending |
| MATRIX-02 | TBD | Pending |
| MATRIX-03 | TBD | Pending |
| MATRIX-04 | TBD | Pending |
| INV-01 | TBD | Pending |
| INV-02 | TBD | Pending |
| INV-03 | TBD | Pending |
| INV-04 | TBD | Pending |
| E2E-01 | TBD | Pending |
| E2E-02 | TBD | Pending |
| E2E-03 | TBD | Pending |
| INTG-01 | TBD | Pending |
| INTG-02 | TBD | Pending |

**Coverage:**
- v1 requirements: 22 total
- Mapped to phases: 0
- Unmapped: 22 ⚠️

---
*Requirements defined: 2026-04-04*
*Last updated: 2026-04-04 after initial definition*
