# Project Research Summary

**Project:** Matrix Commander — Fulfillment Automation Pipeline
**Domain:** Shopify subscription box batch order editing + RMFG production sheet generation
**Researched:** 2026-04-04
**Confidence:** HIGH

## Executive Summary

Matrix Commander replaces two manual bottlenecks in the Saturday fulfillment cycle: Matrixify (Shopify order editing) and the RMFG Translator portal (production sheet download). The pipeline processes 400–2500 subscription box orders in a fixed 6-step sequence: validate → swap → inventory sync → Pass 1 Shopify sync (PR-CJAM only) → Pass 2 Shopify sync (all other parents) → generate → finalize. The recommended implementation is a plain Python linear state machine with JSON checkpoint persistence — no Celery, no Prefect, no async broker. The async concern is limited to the Shopify API call loop itself, where `asyncio` + semaphore-based rate limiting is the correct pattern for finishing 7500 GraphQL mutations within the Saturday morning window.

The single highest-risk component is the two-pass Shopify order edit loop. `orderEditCommit` has a confirmed community-reported behavior where it applies changes server-side even when the HTTP response fails — naive retry logic creates duplicate line items on orders. Combined with Shopify's cost-based GraphQL throttle (1000 pts/60s bucket), the sync loop requires both application-level idempotency checking (pre-commit line item comparison) and proactive rate throttling (target 40 pts/sec, not reactive backoff). These two properties must be built in from the start, not retrofitted.

The architecture is explicitly designed for future absorption by the React developer. Every pipeline step is a pure function with typed inputs/outputs, one file per component, no hidden singletons, and spec comments pointing to the future TypeScript/React equivalent. The Saturday flow is the north star: MVP = inventory CSV to email-ready RMFG XLSX in one session without touching Matrixify or the RMFG portal manually.

---

## Key Findings

### Recommended Stack

The existing `requests`-based `ShopifyClient` is retained — the official `shopify-api-python` SDK adds OAuth session management overhead not needed for private app tokens. Add `tenacity>=9.0.0` to replace hand-rolled retry loops, and `xlsxwriter>=3.2.0` for write-only XLSX generation (3x faster than openpyxl for write path). Keep `openpyxl` for reading incoming weekly production query files. Upgrade Shopify API version from `2024-10` to `2025-10`. The `bulkOperationRunMutation` API is explicitly not applicable — it does not support the `orderEditBegin`/`commitEdit` three-step workflow.

**Core technologies:**
- `requests` + custom `ShopifyClient`: Shopify GraphQL mutations — proven, no SDK overhead
- `tenacity>=9.0`: Retry with exponential backoff + jitter — replaces 26+ hand-rolled loops
- `xlsxwriter>=3.2.0`: Write-only XLSX generation — 3x faster than openpyxl for write path
- `openpyxl>=3.1.5`: Read-only for incoming XLSX imports — required, keep separate from write path
- `pydantic v2`: Validate API response shapes and pipeline state objects
- `flask>=3.1.2`: Webhook endpoint + web UI — already in stack
- Plain Python dataclasses + JSON files: Checkpoint persistence — no broker needed

**Do not add:** pandas, aiohttp (for non-API paths), Prefect, Celery, Airflow, Redis, `shopify-api-python` SDK.

### Expected Features

**Must have (table stakes — pipeline cannot function without these):**
- Idempotent Shopify order sync via `beginEdit → addVariant → commitEdit` with pre-commit line item check
- Two-pass sequencing with explicit operator confirmation gate between Pass 1 and Pass 2
- Rate-limit-aware API client (proactive 40 pts/sec throttle, not just reactive 429 handling)
- Gift order detection and matrix-level merge (Shopify blocks `orderEditBegin` on gift orders)
- RMFG production sheet generation with correct tab name (`Worksheet`), `ProductionDay` column, sort
- MFG name validation (blocks `finalize` on unmapped SKUs)
- Automated swap resolution respecting dietary exclusion orders (NNRS/CORS/NCRS never swapped)
- Inventory sync: calculated quantities → Shopify paid variant + $0 remainder variant

**Should have (high value, not strictly blocking):**
- Pre-sync dry-run mode (simulate full sync against live data before committing 2500 mutations)
- Shortage detection with auto-surfaced swap suggestions ranked by inventory availability
- Webhook trigger from React tool (removes manual Saturday handoff step)
- Sync progress dashboard (real-time per-order status via Flask polling endpoint)
- End-to-end timing log per pipeline stage

**Defer to v2+:**
- Charge detection / Saturday 3am polling
- Pre-flight Friday night automation
- Tuesday cycle automation (reuse Saturday modules when ready)
- Full React absorption / single-screen operator dashboard
- Recharge bundle sync for swaps (Shopify-level swaps sufficient for production sheet)

### Architecture Approach

Use a **linear state machine with checkpoint persistence**. Seven discrete states (`IDLE → INVENTORY_SYNCED → PASS1_COMPLETE → PASS2_COMPLETE → MATRIX_GENERATED → GIFTS_MERGED → FINALIZED`) with forward-only transitions. All state persists in a single `.pipeline/checkpoint.json` file — nothing lives in memory between steps. The Flask web UI reads the checkpoint file; it holds no state itself. Every pipeline step is a pure function with explicit typed inputs and outputs, no module-level singletons, and a spec comment describing the future React/TypeScript equivalent.

**Major components:**
1. `CheckpointStore` — reads/writes pipeline state JSON; single source of truth for all state
2. `PipelineRunner` — orchestrates state transitions; the only component that knows the full sequence
3. `InventorySync` — pushes inventory counts to Shopify paid+$0 variants
4. `OrderSync` (Pass 1 + Pass 2) — async Shopify order edit loop with semaphore rate limiter; partial-failure tracking
5. `MatrixGenerator` — fetches live Shopify line items after sync; builds RMFG matrix in memory
6. `GiftHandler` — pure function; merges gift order allocations into matrix (no API calls)
7. `Finalizer` — pure function; writes XLSX with correct tab name, ProductionDay, sort, auto-name
8. `WebUI` (Flask) — exposes checkpoint state + controls; frontend polls `/api/pipeline/state` every 2s

### Critical Pitfalls

1. **orderEditCommit duplicate on retry** — Server applies the edit before returning HTTP error. Prevention: check target variant already present at expected quantity before every `beginEdit`; use per-order `committed: true` flag; never re-commit. This is the highest-severity risk in the codebase.

2. **Two-pass gate race condition** — Pass 2 triggered before Shopify has fully propagated Pass 1 commits. Prevention: after all Pass 1 commits, read back a sample of orders to verify PR-CJAM variant is present; require explicit operator confirmation before Pass 2; do not auto-chain.

3. **GraphQL bucket exhaustion under load** — 2500 orders × ~40 pts/mutation = aggressive throttle hit. Prevention: proactive 40 pts/sec throttle (read `X-GraphQL-Cost-Include-Fields` headers); process in chunks of 20–30; one `beginEdit` session per order with multiple `addVariant` calls before a single `commitEdit`.

4. **Gift orders silently drop items from matrix** — `orderEditBegin` fails on locked orders; catch-and-continue loses the allocation. Prevention: maintain explicit `gift_orders` side-list during sync; `MatrixGenerator` must merge this list before building RMFG matrix.

5. **Stale inventory snapshot feeds wrong allocation** — Inventory JSON has no timestamp; Friday's snapshot used on Saturday. Prevention: timestamp every snapshot write; refuse to proceed if snapshot is older than 18 hours without operator confirmation; live Shopify variant read to validate before sync.

---

## Implications for Roadmap

Based on combined research, the pipeline has clear dependency ordering. Build in data-flow sequence, not feature-desirability order.

### Phase 1: Foundation — CheckpointStore + PipelineRunner Skeleton
**Rationale:** Everything depends on checkpoint persistence. Without it, no crash-resume and no progress visibility. Build first, test thoroughly.
**Delivers:** JSON checkpoint R/W, state machine transitions, crash-resume skeleton, basic Flask `/api/pipeline/state` polling endpoint.
**Addresses:** Crash-resume requirement; basis for all other components.
**Avoids:** Anti-pattern of mutable in-memory state (Pitfall 4: gift order silent drop; Pitfall 9: orphaned edit sessions).
**Research flag:** Standard patterns — skip phase research.

### Phase 2: OrderSync — Two-Pass Shopify Edit Loop
**Rationale:** Highest-risk component; must be proven against live data before anything downstream is built. It is also on the critical path for every subsequent phase.
**Delivers:** Idempotent batch order edit with proactive rate limiting, partial-failure tracking (`BatchSyncResult`), per-order committed flag, Pass 1/Pass 2 distinction by SKU predicate (not row position).
**Uses:** `tenacity` for retry; `asyncio` + semaphore for concurrency; `requests` + `ShopifyClient` for mutations.
**Implements:** `OrderSync` component; `OrderEditDecision` pure planner function.
**Avoids:** Pitfall 1 (duplicate commits), Pitfall 2 (pass gate race), Pitfall 3 (bucket exhaustion), Pitfall 6 (CalculatedOrder active line item filtering).
**Research flag:** Needs live API testing — exact GraphQL cost per mutation and actual semaphore ceiling require validation against the production store. Flag for `/gsd-research-phase` during planning.

### Phase 3: InventorySync
**Rationale:** Off the critical path (can build in parallel with Phase 2), but must run before OrderSync in the pipeline. Simpler than OrderSync — fewer mutations, no three-step session.
**Delivers:** Paid variant + $0 remainder variant quantity writes to Shopify; inventory freshness timestamp check; staleness gate in `validate` command.
**Avoids:** Pitfall 5 (stale inventory snapshot).
**Research flag:** Standard patterns — skip phase research.

### Phase 4: MatrixGenerator
**Rationale:** Depends on OrderSync being complete (live items must be on orders before fetching). Reads Shopify state; does not write.
**Delivers:** In-memory `ProductionMatrix` built from live Shopify line items after both passes; replaces RMFG portal download.
**Implements:** `MatrixGenerator` component.
**Research flag:** Standard patterns — skip phase research.

### Phase 5: GiftHandler + Finalizer
**Rationale:** Pure functions with no external API calls. GiftHandler merges the `gift_orders` side-list from OrderSync into the matrix. Finalizer writes the XLSX.
**Delivers:** Correct item counts including gift orders; RMFG-compatible XLSX with tab named `Worksheet`, `ProductionDay` column, correct sort, ISO-week auto-name.
**Avoids:** Pitfall 4 (gift order silent drop), Pitfall 7 (wrong tab name breaks portal), Pitfall 11 (missing ProductionDay), Pitfall 12 (month-boundary auto-name bug).
**Research flag:** Standard patterns — skip phase research.

### Phase 6: Swap Resolution + Shortage Detection
**Rationale:** Builds on validated OrderSync; shortage detection informs swap resolution which updates the XLSX before sync. Dietary exclusion enforcement (NNRS/CORS/NCRS) must be correct before this goes live.
**Delivers:** Pre-sync shortage report with ranked substitutes; automated swap application with dietary exclusion guard; audit trail (`SwapDecision` dataclass); `_rc_bundle` property guard with default-to-exclusion behavior.
**Avoids:** Pitfall 13 (`_rc_bundle` guard failure).
**Research flag:** Standard patterns for swap logic — skip phase research. Dietary exclusion rules are well-documented in codebase.

### Phase 7: Web UI + Operator Experience
**Rationale:** Build last, after all pipeline steps are proven. The Flask polling endpoint and progress display are valuable but not blocking.
**Delivers:** Sync progress dashboard (per-order status), dry-run mode toggle, Pass 1 confirmation gate in UI, end-to-end timing log, webhook endpoint from React tool.
**Research flag:** Standard patterns — skip phase research.

### Phase Ordering Rationale

- CheckpointStore first because every other component depends on it for crash-resume.
- OrderSync second because it is the highest-risk component and the critical path dependency for MatrixGenerator.
- InventorySync third because it is simpler and independent, but must precede OrderSync in runtime.
- MatrixGenerator fourth because it requires live Shopify data post-OrderSync.
- GiftHandler + Finalizer fifth because they are pure functions that depend on MatrixGenerator output.
- Swap Resolution sixth because it modifies the matrix before sync and requires proven OrderSync.
- Web UI last because operator can use CLI logs until pipeline is proven end-to-end.

This order also matches the architecture research's explicit build dependency graph (ARCHITECTURE.md Phase 1–7).

### Research Flags

Needs deeper research during planning:
- **Phase 2 (OrderSync):** Exact GraphQL cost per `orderEdit` mutation sequence against this store's plan tier. Optimal semaphore concurrency ceiling. Whether `asyncio`+`aiohttp` or synchronous chunked requests performs better under Shopify's specific throttle behavior. Requires one live test run with `Shopify-GraphQL-Cost-Debug: 1` header before committing to implementation approach.

Standard patterns (skip `/gsd-research-phase`):
- **Phase 1** (CheckpointStore): JSON file R/W + dataclass state machine — established Python pattern.
- **Phase 3** (InventorySync): Standard Shopify variant quantity mutation — well-documented.
- **Phase 4** (MatrixGenerator): Shopify order line item fetch + dict aggregation — straightforward.
- **Phase 5** (GiftHandler + Finalizer): Pure functions; openpyxl write patterns documented.
- **Phase 6** (Swap Resolution): Business logic documented in CONCERNS.md + existing codebase.
- **Phase 7** (Web UI): Flask polling endpoint — established pattern.

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Shopify GraphQL API docs verified; xlsxwriter benchmark from 2025 source; existing stack items confirmed working |
| Features | HIGH | Table stakes derived from PROJECT.md validated requirements and confirmed Shopify API constraints (gift order lock is documented) |
| Architecture | HIGH | State machine + checkpoint pattern verified against Prefect/official docs; pure function boundary design is established Python practice |
| Pitfalls | HIGH for API behavior; MEDIUM for openpyxl specifics | orderEditCommit silent-apply confirmed in Shopify community forum; openpyxl column style behavior needs live validation |

**Overall confidence:** HIGH

### Gaps to Address

- **Exact GraphQL cost per orderEdit mutation sequence:** Listed as 10 pts/mutation in STACK.md but the actual cost depends on fields requested in the response. Must measure with `Shopify-GraphQL-Cost-Debug: 1` header on first live run before tuning semaphore size or throttle target. Treat Phase 2 planning estimates as provisional until measured.
- **asyncio vs synchronous chunked approach for OrderSync:** ARCHITECTURE.md recommends `asyncio`+`aiohttp` (Pattern 3, MEDIUM confidence); STACK.md recommends synchronous `requests` with proactive sleep throttle. These conflict. Resolve during Phase 2 planning by testing both against a small live batch (50 orders) and measuring actual throughput vs. 429 rate.
- **Shopify plan tier:** Rate limit strategy differs significantly by plan (Standard 50 pts/sec vs Plus 500 pts/sec). Verify actual plan tier before finalizing throttle parameters.
- **`orderEditBegin` behavior on already-open sessions:** PITFALLS.md notes this may "implicitly close the previous session" — verify this is true before relying on it as a crash-recovery mechanism (Pitfall 9).

---

## Sources

### Primary (HIGH confidence)
- Shopify GraphQL Admin API — Bulk Operations: https://shopify.dev/docs/api/usage/bulk-operations
- Shopify API Rate Limits: https://shopify.dev/docs/api/usage/limits
- orderEditBegin mutation: https://shopify.dev/docs/api/admin-graphql/latest/mutations/ordereditbegin
- orderEditCommit mutation: https://shopify.dev/docs/api/admin-graphql/latest/mutations/ordereditcommit
- Shopify: Implementing idempotency: https://shopify.dev/docs/api/usage/implementing-idempotency
- Edit existing orders (official): https://shopify.dev/docs/apps/build/orders-fulfillment/order-management-apps/edit-orders
- openpyxl Performance Docs: https://openpyxl.readthedocs.io/en/stable/performance.html
- openpyxl styles (official): https://openpyxl.readthedocs.io/en/stable/styles.html
- `.planning/PROJECT.md` — Requirements (Validated/Active/Out of Scope)
- `.planning/codebase/CONCERNS.md` — Existing bugs and bare exception handlers

### Secondary (MEDIUM confidence)
- openpyxl vs xlsxwriter 200k row benchmark (2025): https://mass-software-solutions.medium.com/optimizing-excel-report-generation-from-openpyxl-to-xlsmerge-processing-52-columns-200k-rows-5b5a03ecbcd4
- Webhook Idempotency Implementation Guide: https://hookdeck.com/webhooks/guides/implement-webhook-idempotency
- Prefect: Importance of Idempotent Data Pipelines: https://www.prefect.io/blog/the-importance-of-idempotent-data-pipelines-for-resilience
- asyncio for I/O-bound API batch calls — established Python practice

### Tertiary (MEDIUM-LOW confidence)
- orderEditCommit silent apply (community): https://community.shopify.dev/t/potential-order-edit-api-bug-commit-returns-error-but-still-applies-changes/32225 — behavior confirmed in forum but not in official docs; treat as real risk
- Shopify automated order fulfillment blog (marketing content) — LOW confidence, general patterns only

---
*Research completed: 2026-04-04*
*Ready for roadmap: yes*
