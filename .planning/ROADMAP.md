# Roadmap: Matrix Commander

## Overview

Seven phases that prove the Saturday fulfillment workflow chunk by chunk against live data. Each phase delivers an independently useful piece: safety nets first (Phase 1), then the highest-risk sync loop (Phase 2), then inventory and shortage detection (Phase 3), then the swap engine with Recharge pre-fetch (Phase 4), then matrix generation and finalization (Phase 5), then end-to-end flow with web UI (Phase 6), and finally the React webhook integration plus handoff documentation (Phase 7). After Phase 6, the Saturday flow runs in under 15 minutes without touching Matrixify or the RMFG portal.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Pipeline Foundation** - CheckpointStore + rate limiter — safety nets before touching live data
- [ ] **Phase 2: Shopify Sync Battle-Test** - Two-pass order edit loop proven against live orders
- [ ] **Phase 3: Inventory + Shortage Detection** - Inventory sync to Shopify and shortage flagging
- [ ] **Phase 4: Recharge Pre-Fetch + Swap Engine** - Bundle selection download, swap logic, and apply to Shopify
- [ ] **Phase 5: Matrix Generation + Gift Merge + Finalize** - RMFG matrix from live data, gift orders merged, XLSX ready to email
- [ ] **Phase 6: End-to-End Flow + Web UI** - Full Saturday flow works; web UI covers every step
- [ ] **Phase 7: React Integration + Handoff Docs** - Webhook endpoint and spec documentation for React developer

## Phase Details

### Phase 1: Pipeline Foundation
**Goal**: Crash-safe pipeline state machine with proactive rate limiting is in place before any live data is touched
**Depends on**: Nothing (first phase)
**Requirements**: PIPE-01, PIPE-02, PIPE-03, PIPE-04
**Success Criteria** (what must be TRUE):
  1. Operator can kill the process mid-run and resume from the last completed order without restarting from order 0
  2. The rate limiter holds Shopify GraphQL throughput at or below target pts/sec and backs off cleanly on 429s
  3. Pipeline state machine transitions forward-only through named stages; illegal transitions are rejected with a clear error
  4. Dry-run mode prints what sync-shopify would do without issuing any Shopify mutations
**Plans**: 3 plans

Plans:
- [ ] 01-01-PLAN.md — CheckpointStore + PipelineState (PIPE-01, PIPE-03): crash-safe JSON checkpoint and forward-only state machine
- [ ] 01-02-PLAN.md — Rate Limiter (PIPE-02): leaky-bucket throttle + tenacity 429 retry, replaces bare sleep calls
- [ ] 01-03-PLAN.md — Dry-Run Enforcement + Web Integration (PIPE-04): DryRunGuard at mutation layer, STATE dict replaced by CheckpointStore

### Phase 2: Shopify Sync Battle-Test
**Goal**: Two-pass Shopify order edit loop works correctly against live orders — idempotent, rate-safe, and partial-failure tolerant
**Depends on**: Phase 1
**Requirements**: SYNC-01, SYNC-02, SYNC-03, SYNC-04, SYNC-05
**Success Criteria** (what must be TRUE):
  1. sync-shopify completes a live PR-CJAM pass and all-parents pass without duplicate line items on any order
  2. Re-running sync-shopify on already-synced orders produces no changes (idempotency verified on live data)
  3. Orders that fail mid-batch are recorded; operator can retry just those orders without touching succeeded ones
  4. The pass gate stops Pass 2 until operator confirms Pass 1 is visible on live orders
  5. orderEditCommit error-but-applied orders are detected and not re-committed
**Plans**: TBD

### Phase 3: Inventory + Shortage Detection
**Goal**: Calculated inventory is pushed to Shopify paid and $0 variants, and shortages are flagged before sync begins
**Depends on**: Phase 1
**Requirements**: INV-01, INV-02
**Success Criteria** (what must be TRUE):
  1. Operator can push inventory counts to Shopify paid and $0 variants from a single command; Shopify variant quantities match the input after the run
  2. Shortage detection cross-checks demand against inventory and surfaces a list of shorted SKUs before any order edits begin
  3. Stale inventory snapshot (older than 18 hours) blocks the pipeline unless operator explicitly confirms
**Plans**: TBD

### Phase 4: Recharge Pre-Fetch + Swap Engine
**Goal**: Recharge bundle selections are downloaded before swaps so customer-chosen items are never swapped; shortage swaps are decided and applied back to Shopify
**Depends on**: Phase 2, Phase 3
**Requirements**: INV-03, INV-04, INV-05, INV-06
**Success Criteria** (what must be TRUE):
  1. Recharge bundle selections are pre-fetched and available before swap decisions are made; customer-chosen items are never swapped
  2. NNRS/CORS/NCRS orders are excluded from all auto-swaps
  3. Shortage swaps are applied to Shopify via order edit API (remove old SKU, add replacement $0 variant); swap audit trail lists every decision
  4. Only _rc_bundle items are eligible for auto-swap; paid/chosen items are untouched
**Plans**: TBD

### Phase 5: Matrix Generation + Gift Merge + Finalize
**Goal**: Correct RMFG production XLSX is generated from live Shopify data with gift orders merged in — ready to email without manual edits
**Depends on**: Phase 2, Phase 4
**Requirements**: MATRIX-01, MATRIX-02, MATRIX-03, MATRIX-04
**Success Criteria** (what must be TRUE):
  1. generate command produces a production matrix from live Shopify line items that matches what the RMFG portal would show (no manual download needed)
  2. Gift orders are detected, children assigned at matrix level, and merged into the final sheet with correct quantities
  3. finalize produces an XLSX with tab named "Worksheet", ProductionDay column present, correct sort order, and ISO-week auto-name
  4. MFG name validation blocks finalize and lists all unmapped SKUs before any XLSX is written
**Plans**: TBD

### Phase 6: End-to-End Flow + Web UI
**Goal**: Full Saturday workflow runs end-to-end from React sheets through email-ready RMFG XLSX, and every step is accessible from the web UI
**Depends on**: Phase 5
**Requirements**: E2E-01, E2E-02, E2E-03
**Success Criteria** (what must be TRUE):
  1. Operator runs the full Saturday sequence (inventory sync → Pass 1 sync → Pass 2 sync → generate → gift merge → finalize) and receives an email-ready RMFG XLSX in under 15 minutes
  2. Web UI exposes every pipeline step, shows per-order sync progress, and surfaces the Pass 1 confirmation gate without requiring CLI
  3. All commands produce verified correct output on live data (not just unit tests passing)
**Plans**: TBD
**UI hint**: yes

### Phase 7: React Integration + Handoff Docs
**Goal**: React tool can trigger Matrix Commander post-processing via webhook, and all logic is documented clearly enough for the React developer to absorb it
**Depends on**: Phase 6
**Requirements**: INTG-01, INTG-02
**Success Criteria** (what must be TRUE):
  1. React tool POST to the webhook endpoint triggers the Matrix Commander post-processing pipeline without manual operator intervention
  2. Every pipeline component has typed inputs/outputs and a spec comment describing the future TypeScript/React equivalent
  3. React developer can read the codebase and implement each component in TypeScript without needing to ask about intent or data shapes
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Pipeline Foundation | 0/3 | Not started | - |
| 2. Shopify Sync Battle-Test | 0/? | Not started | - |
| 3. Inventory + Shortage Detection | 0/? | Not started | - |
| 4. Recharge Pre-Fetch + Swap Engine | 0/? | Not started | - |
| 5. Matrix Generation + Gift Merge + Finalize | 0/? | Not started | - |
| 6. End-to-End Flow + Web UI | 0/? | Not started | - |
| 7. React Integration + Handoff Docs | 0/? | Not started | - |
