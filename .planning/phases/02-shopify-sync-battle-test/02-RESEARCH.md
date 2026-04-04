# Phase 2: Shopify Sync Battle-Test - Research

**Researched:** 2026-04-04
**Domain:** Shopify GraphQL order edit API, sequential batch loop, checkpoint/rate-limiter wiring
**Confidence:** HIGH — all findings verified directly from codebase source files

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Replace ThreadPoolExecutor(max_workers=5) in cmd_sync with sequential per-order loop — rate limiter and checkpoint are not thread-safe
- **D-02:** Wire LeakyBucketLimiter.wait() before every Shopify GraphQL call in sync_order_to_shopify
- **D-03:** Wire DryRunGuard.assert_can_mutate() before orderEditBegin
- **D-04:** Wire CheckpointStore.save() after each order completes (success or failure)
- **D-05:** Wire PipelineState stage transitions: PASS1_COMPLETE after Pass 1, PASS2_COMPLETE after Pass 2
- **D-06:** Single cmd_sync function with a `pass_number` parameter that controls which SKU prefixes are active (1=PR-CJAM only, 2=everything else)
- **D-07:** Hard operator confirmation gate between passes backed by PipelineStage.PASS1_COMPLETE — Pass 2 cannot start until operator confirms Pass 1 is visible on live orders
- **D-08:** Remove PR-CJAM from SKIP_PREFIXES when pass_number=1; add it back for pass_number=2
- **D-09:** Keep existing pre-commit read (current_skus from fulfillable_quantity > 0) as primary idempotency mechanism
- **D-10:** Add per-order `committed` flag in PassProgress — written to checkpoint BEFORE orderEditCommit call, survives crash and prevents re-commit regardless of Shopify read-after-write consistency
- **D-11:** Before orderEditCommit: mark order as `commit_pending` in checkpoint
- **D-12:** If commit returns error: check if order is in `commit_pending` state, do a verification read to confirm whether edit applied, then mark as succeeded or failed accordingly
- **D-13:** On retry: skip orders already marked `commit_pending` with verified success
- **D-14:** PassProgress.failed holds errored order IDs with error messages
- **D-15:** Add `--retry-failed` flag to cmd_sync that loads checkpoint and re-runs only failed orders
- **D-16:** Full re-run also works (idempotency skips succeeded orders) but --retry-failed is faster

### Claude's Discretion
- Exact GraphQL query structure for verification reads
- How to surface the pass gate in web UI vs CLI
- Batch size for checkpoint saves (every order vs every N orders)
- Logging verbosity during live sync

### Deferred Ideas (OUT OF SCOPE)
- Shopify read-after-write consistency verification — needs live testing, not code change
- orderEditCommit idempotencyKey support check — needs Shopify schema inspection
- asyncio migration for faster batch processing — revisit if synchronous is too slow
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SYNC-01 | PR-CJAM pass and all-parents pass complete without duplicate line items | D-09 current_skus check + D-10 committed flag + D-11/D-12 verification read |
| SYNC-02 | Re-running on already-synced orders produces no changes | D-09 current_skus idempotency; existing duplicates list in sync_order_to_shopify |
| SYNC-03 | Failed orders recorded; operator can retry only those | D-14 PassProgress.failed + D-15 --retry-failed flag |
| SYNC-04 | Pass gate stops Pass 2 until operator confirms | D-07 PASS1_COMPLETE stage gate + CLI confirmation prompt |
| SYNC-05 | orderEditCommit error-but-applied orders detected and not re-committed | D-11 commit_pending + D-12 verification read + D-13 skip on retry |
</phase_requirements>

---

## Summary

Phase 2 wires the Phase 1 infrastructure (LeakyBucketLimiter, CheckpointStore, PipelineState, DryRunGuard) into the existing `cmd_sync` / `sync_order_to_shopify` functions. The primary structural change is replacing the ThreadPoolExecutor with a sequential per-order loop. Three supporting changes layer on top: pass_number parameter controlling which SKU prefixes are active, a `commit_pending` checkpoint flag protecting against the error-but-applied pitfall, and a `--retry-failed` CLI flag for partial-failure recovery.

The existing codebase already has the correct idempotency primitive — `current_skus` built from `fulfillable_quantity > 0` line items prevents re-adding already-present SKUs. Phase 2 preserves this and adds the checkpoint layer on top to survive process crashes. The verification read (D-12) is the only net-new Shopify API call pattern — everything else reuses existing GraphQL mutations.

**Primary recommendation:** Implement in three sequentially testable plans — (1) sequential loop + rate limiter + dry-run guard wiring, (2) two-pass sequencing with pass gate, (3) commit_pending + verification read + retry-failed.

---

## Standard Stack

No new libraries required. All dependencies are already present in the project.

| Component | Location | Purpose |
|-----------|----------|---------|
| `LeakyBucketLimiter` | `pipeline/rate_limiter.py` | Token-bucket throttle — call `.wait(cost)` before each GraphQL call |
| `shopify_retry` | `pipeline/rate_limiter.py` | Tenacity decorator for 429 retry with exponential backoff |
| `CheckpointStore` | `pipeline/checkpoint_store.py` | Atomic JSON checkpoint — `.save(state)` after each order |
| `PipelineState` / `PipelineStage` | `pipeline/pipeline_state.py` | Forward-only stage machine; `.advance(to)` at pass boundaries |
| `PassProgress` | `pipeline/pipeline_state.py` | Per-pass order tracking: `succeeded`, `failed`, `skipped` lists |
| `DryRunGuard` | `pipeline/dry_run_guard.py` | `.assert_can_mutate()` before `orderEditBegin` |

[VERIFIED: direct source file read]

---

## Architecture Patterns

### Pattern 1: Sequential Loop Replacing ThreadPoolExecutor

**Current code (lines 1602-1610):**
```python
with ThreadPoolExecutor(max_workers=workers) as pool:
    futures = {pool.submit(_do_sync, item): item[0]["name"] for item in order_items}
    for future in as_completed(futures):
        result = future.result()
```

**Replacement pattern:**
```python
# Source: matrix_commander.py cmd_sync + pipeline/rate_limiter.py
guard = DryRunGuard(dry_run=dry_run)
limiter = LeakyBucketLimiter(pts_per_sec=5.0)
store = CheckpointStore()
state = store.load() or PipelineState(pipeline_id=pipeline_id, dry_run=dry_run)

for order, m_skus in order_items:
    order_id = order["name"].replace("#", "")
    # Skip already-succeeded orders (idempotency across re-runs)
    progress = state.pass1 if pass_number == 1 else state.pass2
    if order_id in progress.succeeded:
        continue
    result = sync_order_to_shopify(
        base, headers, order, m_skus, variant_gids, mode,
        limiter=limiter, guard=guard, store=store, state=state,
        pass_number=pass_number,
    )
    # Update progress and save checkpoint after every order
    if result.status == "updated":
        progress = PassProgress(
            succeeded=[*progress.succeeded, order_id],
            failed=progress.failed,
            skipped=progress.skipped,
        )
    elif result.status == "error":
        progress = PassProgress(
            succeeded=progress.succeeded,
            failed=[*progress.failed, order_id],
            skipped=progress.skipped,
        )
    # Rebuild state immutably and save
    state = _update_pass_progress(state, pass_number, progress)
    store.save(state)
```

[VERIFIED: pipeline_state.py — PassProgress is a mutable dataclass with list fields; immutable rebuild pattern matches project coding-style rules]

**Note:** `PassProgress` uses mutable lists in its dataclass. The immutable rebuild pattern above (spread into new list) is required by CLAUDE.md coding-style rules (no mutation). Use `dataclasses.replace()` or construct new `PassProgress` instances.

### Pattern 2: pass_number Controls Active SKU Prefixes (D-06, D-08)

**Current SKIP_PREFIXES (line 313):**
```python
SKIP_PREFIXES = ("AHB-", "BL-", "PK-", "TR-", "EX-", "PR-CJAM", "CEX-E")
```

**Pass-aware filter in sync_order_to_shopify:**
```python
# Source: matrix_commander.py lines 1401-1407 + D-06/D-08
def _active_prefixes(pass_number: int) -> tuple[str, ...]:
    """Pass 1: PR-CJAM only. Pass 2: everything except PR-CJAM."""
    if pass_number == 1:
        return ("PR-CJAM",)
    # Pass 2: standard food/packaging prefixes; PR-CJAM already on orders
    return ("CH-", "MT-", "AC-", "PK-", "TR-")
```

The existing filter inside `sync_order_to_shopify` (lines 1402-1407) checks `if not any(sku.startswith(p) for p in ("CH-", "MT-", "AC-", "PK-", "TR-"))`. For pass 1, this filter must be replaced with a PR-CJAM-only check. The cleanest approach is passing the active prefix tuple as a parameter.

[VERIFIED: matrix_commander.py line 313 and lines 1401-1407 — direct read]

### Pattern 3: commit_pending Flag + Verification Read (D-10, D-11, D-12)

The `PassProgress` dataclass currently has three lists: `succeeded`, `failed`, `skipped`. D-10 requires a `commit_pending` tracking mechanism. Two options evaluated:

**Option A: Add `commit_pending: list[str]` field to PassProgress** — cleanest; survives crash because checkpoint is saved before `orderEditCommit`; naturally serializes via `to_dict()`/`from_dict()`.

**Option B: Separate dict `errors: dict[str, str]` on PassProgress** — stores error message alongside order ID for D-14.

**Recommendation (Claude's discretion):** Extend `PassProgress` with two new fields:
```python
@dataclass
class PassProgress:
    succeeded: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    commit_pending: list[str] = field(default_factory=list)   # NEW: D-10/D-11
    errors: dict[str, str] = field(default_factory=dict)      # NEW: D-14 (order_id -> error msg)
```

`to_dict()` and `from_dict()` must be updated to include these fields. Existing checkpoints without these fields will deserialize to empty lists/dicts via `.get()` with defaults — no migration required.

[VERIFIED: pipeline_state.py lines 27-49 — PassProgress structure and serialization methods read directly]

### Pattern 4: Verification Read GraphQL Query (D-12)

The verification read confirms whether an `orderEditCommit` that returned an error actually applied. It fetches the order's current line items and checks whether the target variants are present at expected quantities.

**Recommended query (Claude's discretion — verified against Shopify order edit docs):**
```graphql
# Source: [CITED: https://shopify.dev/docs/api/admin-graphql/latest/queries/order]
query VerifyOrderLineItems($id: ID!) {
    order(id: $id) {
        id
        lineItems(first: 250) {
            edges {
                node {
                    sku
                    fulfillableQuantity
                    quantity
                }
            }
        }
    }
}
```

**Usage pattern in sync_order_to_shopify:**
```python
# After commit returns error and order is in commit_pending:
def _verify_edit_applied(
    base: str, headers: dict, order_gid: str, expected_skus: list[str]
) -> bool:
    """Return True if all expected_skus appear on the order with qty > 0."""
    data = _shopify_graphql(base, headers, VERIFY_LINE_ITEMS_QUERY, {"id": order_gid})
    current = {
        edge["node"]["sku"]
        for edge in data["order"]["lineItems"]["edges"]
        if edge["node"]["fulfillableQuantity"] > 0
    }
    return all(sku in current for sku in expected_skus)
```

[CITED: https://shopify.dev/docs/api/admin-graphql/latest/queries/order — lineItems field with sku and fulfillableQuantity]

### Pattern 5: --retry-failed CLI Wiring (D-15)

**Current argparse for sync-shopify (lines 2503-2513):**
```python
p_sync = sub.add_parser("sync-shopify", ...)
p_sync.add_argument("xlsx", ...)
p_sync.add_argument("tag", ...)
p_sync.add_argument("--execute", action="store_true", ...)
p_sync.add_argument("--mode", choices=["smart", "conservative"], ...)
p_sync.add_argument("--workers", type=int, default=5, ...)  # to be removed
```

**New arguments to add:**
```python
p_sync.add_argument("--pass", type=int, choices=[1, 2], default=1,
                    dest="pass_number", help="Which pass to run (1=PR-CJAM, 2=all parents)")
p_sync.add_argument("--retry-failed", action="store_true",
                    help="Load checkpoint and retry only failed orders from last run")
```

**--workers argument:** Remove from argparse (sequential loop replaces thread pool per D-01). Keep the parameter in `cmd_sync` signature as dead code until the web UI integration is confirmed, then remove entirely.

**Dispatch at line 2543:**
```python
elif args.command == "sync-shopify":
    ok = cmd_sync(
        args.xlsx, args.tag,
        mode=args.mode,
        dry_run=not args.execute,
        pass_number=args.pass_number,
        retry_failed=args.retry_failed,
    )
```

[VERIFIED: matrix_commander.py lines 2502-2513 and 2542-2543 — direct read]

### Anti-Patterns to Avoid

- **Do not call `sync_order_to_shopify` in dry-run mode for the live path** — the current code (lines 1551-1554) calls `sync_order_to_shopify` in dry-run mode but then re-simulates locally. The refactored version should use `DryRunGuard.assert_can_mutate()` to block mutations inside `sync_order_to_shopify` rather than branching at the call site in `cmd_sync`.
- **Do not save checkpoint after every GraphQL call** — save once per completed order (after `orderEditCommit` succeeds or after verification read). Saving mid-order creates a partial state that is harder to recover from.
- **Do not re-use `calc_id` across retries** — `orderEditBegin` creates a new calculated order session each time. A stale `calc_id` from a previous crashed run is invalid. Always begin fresh.
- **Do not remove the existing `current_skus` duplicate check** — D-09 preserves it as the primary idempotency guard. The `commit_pending` flag is a second layer, not a replacement.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Atomic checkpoint write | Custom file write | `CheckpointStore.save()` | Already atomic via .tmp + os.replace() |
| 429 retry with backoff | Custom sleep loop | `shopify_retry` decorator | Handles Retry-After header, caps at 60s, 5 retries |
| Token bucket throttle | `time.sleep(N)` between orders | `LeakyBucketLimiter.wait(cost)` | Self-calibrates from response extensions |
| Dry-run gate | `if dry_run: return` sprinkled everywhere | `DryRunGuard.assert_can_mutate()` | Single enforcement point, raises clearly |
| Stage transitions | Manual stage string tracking | `PipelineState.advance(PipelineStage.X)` | Forward-only enforcement built in |

---

## Common Pitfalls

### Pitfall 1: orderEditCommit Error-But-Applied (SYNC-05)
**What goes wrong:** `orderEditCommit` returns a network error but the edit applied server-side. Retrying re-commits and adds duplicate line items.
**Why it happens:** HTTP response lost after server processed mutation (PITFALLS.md Pitfall 1).
**How to avoid:** Save `commit_pending` to checkpoint BEFORE calling commit. If commit errors and `commit_pending` is set, run verification read. If SKUs present → mark succeeded. If not → mark failed, safe to retry.
**Warning signs:** Orders with double item quantities; audit log shows two commits for same calc session.

### Pitfall 2: Pass 2 Starting Before Pass 1 Confirmed
**What goes wrong:** Pass 2 runs before operator verifies Pass 1 is visible on live orders (PITFALLS.md Pitfall 2).
**How to avoid:** Check `state.stage == PipelineStage.PASS1_COMPLETE` before starting Pass 2. Gate requires both the stage check AND explicit operator confirmation (`input("Confirm Pass 1 visible on live orders? [y/N]: ")`).

### Pitfall 3: Crash Recovery with Orphaned Edit Sessions (PITFALLS.md Pitfall 9)
**What goes wrong:** Crash between `orderEditBegin` and `orderEditCommit` leaves an open CalculatedOrder session. Next `orderEditBegin` on the same order may fail or nest.
**How to avoid:** Log `calc_id` to checkpoint immediately after `orderEditBegin`. On startup with an existing checkpoint, check if any order is in mid-edit state (has `calc_id` logged but no `commit_pending` or `succeeded`). If found, call `orderEditBegin` again (implicitly closes prior session) before proceeding.
**Note:** This is a moderate risk — Shopify sessions expire after inactivity. Prioritize `commit_pending` coverage first, orphaned session recovery in a follow-up if live testing reveals it as an issue.

### Pitfall 4: PassProgress Mutation Violating Immutability Rules
**What goes wrong:** Appending to `progress.succeeded` directly mutates the dataclass list. CLAUDE.md coding-style requires immutable patterns.
**How to avoid:** Always construct new `PassProgress` instances: `PassProgress(succeeded=[*old.succeeded, new_id], failed=old.failed, skipped=old.skipped, ...)`. Use `dataclasses.replace()` where applicable.

### Pitfall 5: --workers Argument Left in CLI Confusing Operators
**What goes wrong:** If `--workers` remains in argparse but does nothing (sequential loop), operators may pass it expecting parallelism and get wrong mental model.
**How to avoid:** Remove `--workers` from argparse when replacing thread pool. Log a deprecation warning if the old web app passes it via the `/api/sync` endpoint.

---

## Code Examples

### Rate Limiter Integration in sync_order_to_shopify
```python
# Source: pipeline/rate_limiter.py lines 52-71
# Call before each GraphQL mutation; cost=10 is a safe default for order edit mutations
limiter.wait(cost=10)
response = _shopify_graphql(base, headers, mutation, variables)
# Optionally sync actual cost:
limiter.record_response(response.get("extensions", {}).get("cost"))
```

### DryRunGuard Usage
```python
# Source: pipeline/dry_run_guard.py lines 37-44
guard = DryRunGuard(dry_run=dry_run)
# Inside sync_order_to_shopify, before orderEditBegin:
guard.assert_can_mutate()  # raises DryRunViolationError if dry_run=True
```

### CheckpointStore Save Pattern
```python
# Source: pipeline/checkpoint_store.py lines 48-56
store = CheckpointStore()  # defaults to .pipeline/checkpoint.json
store.save(state)           # atomic: writes .tmp then os.replace()
```

### PipelineState Stage Transition
```python
# Source: pipeline/pipeline_state.py lines 66-80
# After all Pass 1 orders complete:
state = state.advance(PipelineStage.PASS1_COMPLETE)
store.save(state)
# InvalidTransitionError raised automatically if transition is illegal
```

### PassProgress Immutable Update
```python
# Source: pipeline/pipeline_state.py lines 27-49 + coding-style.md
from dataclasses import replace as dc_replace

def _record_success(progress: PassProgress, order_id: str) -> PassProgress:
    return PassProgress(
        succeeded=[*progress.succeeded, order_id],
        failed=progress.failed,
        skipped=progress.skipped,
        commit_pending=[o for o in progress.commit_pending if o != order_id],
        errors={k: v for k, v in progress.errors.items() if k != order_id},
    )
```

---

## Runtime State Inventory

> Not a rename/refactor/migration phase — this section is omitted.

---

## Open Questions

1. **Checkpoint save frequency (Claude's discretion)**
   - What we know: Save after every order is safest; saves after every N orders reduce I/O
   - What's unclear: Whether `.pipeline/checkpoint.json` write frequency is perceptible at 2500 orders
   - Recommendation: Save after every order. At ~1KB per checkpoint and ~1 write/second (rate-limited), I/O is negligible. Simplicity beats micro-optimization here.

2. **Pass gate surface in web UI vs CLI (Claude's discretion)**
   - What we know: CLI gate is `input("Confirm? [y/N]: ")`. Web UI needs a different affordance.
   - What's unclear: Whether the web `/api/sync` endpoint needs a separate `confirm-pass1` endpoint or a state query
   - Recommendation: CLI for Phase 2. Web UI gate is Phase 6 scope — defer.

3. **Verification read false-negative risk**
   - What we know: Shopify has eventual consistency under load (PITFALLS.md Pitfall 2)
   - What's unclear: How quickly a committed edit becomes visible to a subsequent read in practice
   - Recommendation: Add a 2-second sleep before verification read when `commit_pending` is set. If still not visible, mark as `failed` and let operator retry — don't loop indefinitely.

---

## Environment Availability

> Step 2.6: SKIPPED — Phase 2 is code changes to an existing Python file. No new external tools, runtimes, or services beyond what Phase 1 already established (Shopify API credentials in environment, Python 3.10+ via Anaconda).

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (pyproject.toml) |
| Config file | `pyproject.toml` [tool.pytest.ini_options] |
| Quick run command | `pytest tests/ -x -q` |
| Full suite command | `pytest --cov` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SYNC-01 | No duplicate line items across two passes | unit | `pytest tests/test_sync.py::test_no_duplicates -x` | ❌ Wave 0 |
| SYNC-02 | Re-run produces no changes (idempotency) | unit | `pytest tests/test_sync.py::test_idempotent_rerun -x` | ❌ Wave 0 |
| SYNC-03 | Failed orders recorded; retry skips succeeded | unit | `pytest tests/test_sync.py::test_retry_failed -x` | ❌ Wave 0 |
| SYNC-04 | Pass gate blocks Pass 2 before PASS1_COMPLETE | unit | `pytest tests/test_sync.py::test_pass_gate -x` | ❌ Wave 0 |
| SYNC-05 | commit_pending + verification read prevents re-commit | unit | `pytest tests/test_sync.py::test_commit_pending_recovery -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/test_sync.py -x -q`
- **Per wave merge:** `pytest --cov`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/test_sync.py` — covers SYNC-01 through SYNC-05 (mock Shopify GraphQL responses)
- [ ] `tests/conftest.py` — shared fixtures: mock `_shopify_graphql`, sample order dicts, `PassProgress` factories

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | Shopify token via `_get_shopify_auth()` — existing, unchanged |
| V3 Session Management | no | Stateless CLI |
| V4 Access Control | no | Single-operator tool |
| V5 Input Validation | yes | Order IDs from Shopify API (trusted source); XLSX from operator |
| V6 Cryptography | no | No new crypto |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Stale checkpoint replay | Tampering | `pipeline_id` in PipelineState ties checkpoint to a specific run date |
| Shopify token in env | Information Disclosure | Already handled by `_get_shopify_auth()` — no change |
| Malformed XLSX injection | Tampering | `parse_matrix()` validates XLSX structure before sync — existing |

---

## Sources

### Primary (HIGH confidence — direct source file reads)
- `matrix_commander.py` lines 313, 1360-1642 — cmd_sync, sync_order_to_shopify, SKIP_PREFIXES, argparse
- `pipeline/rate_limiter.py` — full file — LeakyBucketLimiter, shopify_retry APIs
- `pipeline/checkpoint_store.py` — full file — CheckpointStore API
- `pipeline/pipeline_state.py` — full file — PipelineState, PipelineStage, PassProgress
- `pipeline/dry_run_guard.py` — full file — DryRunGuard API
- `.planning/phases/02-shopify-sync-battle-test/02-CONTEXT.md` — locked decisions D-01 through D-16
- `.planning/research/PITFALLS.md` — Pitfall 1 (commit error-but-applied), Pitfall 9 (orphaned sessions)

### Secondary (CITED — official docs)
- [Shopify order query — lineItems](https://shopify.dev/docs/api/admin-graphql/latest/queries/order) — verification read GraphQL structure

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `orderEditBegin` implicitly closes a prior open session on the same order | Common Pitfalls — Pitfall 3 | If it does not, orphaned sessions cause `orderEditBegin` to fail; needs live-data verification |
| A2 | 2-second sleep before verification read is sufficient for Shopify consistency | Open Questions #3 | If Shopify is slower, verification read returns false-negative and marks succeeded orders as failed |

**Both A1 and A2 are low-risk for Phase 2** — the commit_pending + verification read pattern is defensive regardless of session behavior. Live testing will surface real timing.

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all pipeline modules verified from source
- Architecture: HIGH — existing cmd_sync and sync_order_to_shopify read directly
- Pitfalls: HIGH — PITFALLS.md cross-referenced with actual code paths
- Verification read query: MEDIUM — cited from official Shopify docs, field names not executed against live schema

**Research date:** 2026-04-04
**Valid until:** 2026-05-04 (stable Shopify API; pipeline modules won't change between phases)
