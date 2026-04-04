# Phase 1: Pipeline Foundation - Context

**Gathered:** 2026-04-04 (assumptions mode)
**Status:** Ready for planning

<domain>
## Phase Boundary

Crash-safe pipeline state machine with proactive rate limiting is in place before any live data is touched. This phase builds safety nets (checkpoint/resume, rate limiter, state machine, dry-run enforcement) that all subsequent phases depend on.

</domain>

<decisions>
## Implementation Decisions

### Checkpoint Store
- **D-01:** Single JSON file at `.pipeline/checkpoint.json` holds pipeline stage, per-pass succeeded/failed order ID lists, and timestamps
- **D-02:** Per-order granularity — crash mid-batch resumes from last completed order, not from start of stage
- **D-03:** Web UI reads the same checkpoint file for state rendering — no separate in-memory state tracking
- **D-04:** JSON format (not SQLite) — consistent with existing `settings.json` and `idempotency_log.json` patterns

### Rate Limiter
- **D-05:** Synchronous leaky-bucket wrapper on existing `requests`-based ShopifyClient — NOT async/aiohttp
- **D-06:** Add `tenacity>=9.0` for retry-with-exponential-backoff on 429 responses
- **D-07:** Replace the three bare `time.sleep(0.1-0.2)` calls in matrix_commander.py with the rate limiter
- **D-08:** Target throughput: configurable pts/sec with default based on Shopify plan tier (needs live test to determine exact value)

### Pipeline State Machine
- **D-09:** Python dataclass (`PipelineState`) with forward-only enum: IDLE → INVENTORY_SYNCED → PASS1_COMPLETE → PASS2_COMPLETE → MATRIX_GENERATED → GIFTS_MERGED → FINALIZED
- **D-10:** Illegal backward transitions raise hard error, not warning
- **D-11:** Replaces the in-memory `STATE` dict in `matrix_commander_web/app.py`

### Dry-Run Mode
- **D-12:** Dry-run enforced at ShopifyClient layer — mutations cannot fire without explicit `--execute` flag
- **D-13:** Preserve existing dry-run logic in `cmd_sync` (already works); formalize it as first-class mode
- **D-14:** Web UI defaults to dry-run=True (already does); Phase 1 ensures this can't be accidentally bypassed

### Claude's Discretion
- Exact JSON schema for checkpoint file
- Leaky-bucket implementation details (token tracking vs time-window)
- PipelineState dataclass field names and serialization format
- How to surface dry-run preview in web UI

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Rate limiting & API patterns
- `.planning/research/STACK.md` — tenacity recommendation, synchronous vs async decision, Shopify rate limit analysis
- `.planning/research/ARCHITECTURE.md` — Pipeline patterns, checkpoint design, concurrency model

### Existing implementation
- `matrix_commander.py` lines 1153, 1185, 1559 — Current sleep-based rate limiting to replace
- `matrix_commander.py` lines 1318, 1370-1398 — Existing dry-run logic in cmd_sync
- `matrix_commander_web/app.py` lines 63-75 — Current STATE dict to replace with PipelineState
- `matrix_commander_web/app.py` line 392 — Web dry-run parameter handling

### Research findings
- `.planning/research/PITFALLS.md` — orderEditCommit error-but-applied bug (relevant to checkpoint design)
- `.planning/research/SUMMARY.md` — Build order recommendation (checkpoint first)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `cmd_sync` dry-run preview loop — already functional, needs to be extracted into ShopifyClient layer
- `_simulate_sync()` in web app — existing dry-run simulation for web path
- `ShopifyClient` pattern in matrix_commander.py — centralized API calls, good place to inject rate limiter

### Established Patterns
- Settings loaded from JSON files (`inventory_reorder_settings.json`) — checkpoint file follows same pattern
- CLI flags use argparse with `--execute` for destructive operations — dry-run is the default
- Web endpoints accept `dry_run` parameter in request body

### Integration Points
- ShopifyClient — rate limiter wraps all Shopify API calls
- `cmd_full()` pipeline — state machine replaces the sequential validate→check→swap flow
- Web `STATE` dict — checkpoint file replaces this as single source of truth
- All CLI commands that call Shopify — must go through rate-limited client

</code_context>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches. Research recommends plain Python (dataclasses + functions) without external state machine libraries.

</specifics>

<deferred>
## Deferred Ideas

- asyncio + aiohttp semaphore for concurrent Shopify calls — revisit in Phase 2 if synchronous is too slow
- Shopify plan tier rate limit test (`Shopify-GraphQL-Cost-Debug: 1` header) — needed before Phase 2 live testing

None — analysis stayed within phase scope

</deferred>

---

*Phase: 01-pipeline-foundation*
*Context gathered: 2026-04-04*
