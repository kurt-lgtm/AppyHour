# Phase 2: Shopify Sync Battle-Test - Context

**Gathered:** 2026-04-04 (assumptions mode)
**Status:** Ready for planning

<domain>
## Phase Boundary

Two-pass Shopify order edit loop works correctly against live orders — idempotent, rate-safe, and partial-failure tolerant. This phase wires Phase 1 infrastructure into the existing sync code and proves it against real data.

</domain>

<decisions>
## Implementation Decisions

### Phase 1 Integration
- **D-01:** Replace ThreadPoolExecutor(max_workers=5) in cmd_sync with sequential per-order loop — rate limiter and checkpoint are not thread-safe
- **D-02:** Wire LeakyBucketLimiter.wait() before every Shopify GraphQL call in sync_order_to_shopify
- **D-03:** Wire DryRunGuard.assert_can_mutate() before orderEditBegin
- **D-04:** Wire CheckpointStore.save() after each order completes (success or failure)
- **D-05:** Wire PipelineState stage transitions: PASS1_COMPLETE after Pass 1, PASS2_COMPLETE after Pass 2

### Two-Pass Sequencing
- **D-06:** Single cmd_sync function with a `pass_number` parameter that controls which SKU prefixes are active (1=PR-CJAM only, 2=everything else)
- **D-07:** Hard operator confirmation gate between passes backed by PipelineStage.PASS1_COMPLETE — Pass 2 cannot start until operator confirms Pass 1 is visible on live orders
- **D-08:** Remove PR-CJAM from SKIP_PREFIXES when pass_number=1; add it back for pass_number=2

### Idempotency
- **D-09:** Keep existing pre-commit read (current_skus from fulfillable_quantity > 0) as primary idempotency mechanism
- **D-10:** Add per-order `committed` flag in PassProgress — written to checkpoint BEFORE orderEditCommit call, survives crash and prevents re-commit regardless of Shopify read-after-write consistency

### Error-But-Applied (SYNC-05)
- **D-11:** Before orderEditCommit: mark order as `commit_pending` in checkpoint
- **D-12:** If commit returns error: check if order is in `commit_pending` state, do a verification read to confirm whether edit applied, then mark as succeeded or failed accordingly
- **D-13:** On retry: skip orders already marked `commit_pending` with verified success

### Partial Failure Recovery
- **D-14:** PassProgress.failed holds errored order IDs with error messages
- **D-15:** Add `--retry-failed` flag to cmd_sync that loads checkpoint and re-runs only failed orders
- **D-16:** Full re-run also works (idempotency skips succeeded orders) but --retry-failed is faster

### Claude's Discretion
- Exact GraphQL query structure for verification reads
- How to surface the pass gate in web UI vs CLI
- Batch size for checkpoint saves (every order vs every N orders)
- Logging verbosity during live sync

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Existing sync implementation
- `matrix_commander.py` lines 1488-1642 — cmd_sync function (ThreadPoolExecutor path to replace)
- `matrix_commander.py` lines 1360-1486 — sync_order_to_shopify function (core edit logic)
- `matrix_commander.py` lines 1390-1407 — current_skus duplicate detection
- `matrix_commander.py` line 313 — SKIP_PREFIXES (PR-CJAM currently skipped)
- `matrix_commander.py` lines 1478-1480 — orderEditCommit error handling

### Phase 1 infrastructure
- `pipeline/rate_limiter.py` — LeakyBucketLimiter, shopify_retry
- `pipeline/checkpoint_store.py` — CheckpointStore with atomic save
- `pipeline/pipeline_state.py` — PipelineState, PipelineStage enum, PassProgress
- `pipeline/dry_run_guard.py` — DryRunGuard

### Research findings
- `.planning/research/PITFALLS.md` — Pitfall 1 (orderEditCommit error-but-applied), Pitfall 2 (two-pass gate)
- `.planning/research/STACK.md` — Shopify rate limit analysis, synchronous approach

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `sync_order_to_shopify()` — core order edit logic, mostly intact, needs rate limiter + checkpoint wiring
- `current_skus` duplicate detection — working idempotency check, just needs commit_pending flag addition
- `_simulate_sync()` in web app — dry-run preview, DryRunGuard replaces this pattern

### Established Patterns
- `SyncResult` dataclass at line 1361 — status/skus_added/skus_skipped/duplicates/errors
- Order fetching via REST API with tag filter
- Variant GID lookup via `_lookup_variant_gids()`

### Integration Points
- cmd_sync → LeakyBucketLimiter (before each GraphQL call)
- cmd_sync → CheckpointStore (after each order)
- cmd_sync → PipelineState (stage transitions at pass boundaries)
- cmd_sync → DryRunGuard (before orderEditBegin)
- Web /api/sync endpoint → same infrastructure

</code_context>

<specifics>
## Specific Ideas

- PR-CJAM is currently in SKIP_PREFIXES — needs to be dynamically included/excluded based on pass number
- The pass gate should be a simple "Confirm Pass 1 applied? [y/N]" in CLI, and a button in web UI

</specifics>

<deferred>
## Deferred Ideas

- Shopify read-after-write consistency verification — needs live testing, not code change
- orderEditCommit idempotencyKey support check — needs Shopify schema inspection
- asyncio migration for faster batch processing — revisit if synchronous is too slow

None — analysis stayed within phase scope

</deferred>

---

*Phase: 02-shopify-sync-battle-test*
*Context gathered: 2026-04-04*
