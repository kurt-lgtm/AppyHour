# Architecture Patterns: Multi-Step Fulfillment Pipeline

**Domain:** Fulfillment pipeline orchestration (Python CLI + Flask web UI)
**Researched:** 2026-04-04
**Confidence:** HIGH (patterns verified against official Shopify docs + established Python practices)

---

## Recommended Architecture

### Pipeline as Explicit State Machine

Use a **linear state machine with checkpoint persistence** — not a generic task queue, not an event bus, not an async DAG. The fulfillment pipeline has a fixed, known sequence of 6 steps. Complexity should come from failure recovery, not from the orchestration model.

```
IDLE
  → INVENTORY_SYNCED       (paid variant + $0 variant quantities set in Shopify)
  → PASS1_COMPLETE         (PR-CJAM items on all orders)
  → PASS2_COMPLETE         (all other parents on all orders)
  → MATRIX_GENERATED       (RMFG matrix built from live Shopify data)
  → GIFTS_MERGED           (gift orders handled at matrix level)
  → FINALIZED              (XLSX ready for RMFG email)
```

Each state transition is only allowed in forward order. Resuming after a crash means reading the checkpoint file and re-entering at the last completed state.

---

## Component Boundaries

| Component | Responsibility | Inputs | Outputs | Talks To |
|-----------|---------------|--------|---------|----------|
| `PipelineRunner` | Orchestrates state transitions, reads/writes checkpoint | checkpoint file, user triggers | state updates, progress events | all step components |
| `InventorySync` | Pushes inventory counts to Shopify paid+$0 variants | inventory CSV / settings | SyncResult per variant | Shopify GraphQL |
| `OrderSync` (Pass 1 + Pass 2) | Applies line item edits to Shopify orders | XLSX matrix, pass enum | BatchSyncResult (successes + failures) | Shopify GraphQL |
| `MatrixGenerator` | Fetches live Shopify line items → builds RMFG matrix | Shopify order data | in-memory matrix dict | Shopify GraphQL |
| `GiftHandler` | Detects gift orders, assigns children at matrix level | matrix dict | merged matrix dict | none (pure function) |
| `Finalizer` | Tab rename, ProductionDay column, sort, zip, auto-name | merged matrix dict | XLSX file on disk | openpyxl |
| `CheckpointStore` | Read/write pipeline state to JSON on disk | state enum + progress counters | checkpoint JSON | filesystem |
| `WebUI` (Flask) | Exposes pipeline state + controls to the operator | HTTP requests from browser | JSON state + SSE progress stream | PipelineRunner |

The `PipelineRunner` is the only component that knows the full sequence. Every other component is a pure function or a thin API wrapper — it does one thing, takes typed inputs, returns typed results. This is the property that makes each piece absorbable by the React developer.

---

## Data Flow

```
inventory CSV / settings JSON
        │
        ▼
  InventorySync ──────────────────────────────► Shopify (variant quantity mutations)
                                                       │
  XLSX matrix (from React tool) ◄────────────────────◄┘
        │
        ▼
  OrderSync Pass 1 (PR-CJAM only) ────────────► Shopify (orderEdit: beginEdit → addVariant → commitEdit)
        │
        ▼  [React tool must run between Pass 1 and Pass 2 — operator confirms]
        │
  OrderSync Pass 2 (all other parents) ────────► Shopify (same pattern)
        │
        ▼
  MatrixGenerator ◄────────────────────────────  Shopify (fetch live line items)
        │
        ▼
  GiftHandler (pure function, no API calls)
        │
        ▼
  Finalizer (pure function, openpyxl)
        │
        ▼
  XLSX file on disk ──────────────────────────► operator emails to RMFG
```

State is never held in memory between steps. After each step, `CheckpointStore` writes a JSON file:

```json
{
  "pipeline_id": "2026-04-05-saturday",
  "state": "PASS1_COMPLETE",
  "pass1": { "succeeded": 2487, "failed": 13, "failed_order_ids": [10001, 10045, ...] },
  "pass2": null,
  "matrix_path": null,
  "final_xlsx_path": null,
  "started_at": "2026-04-05T08:14:22Z",
  "updated_at": "2026-04-05T08:19:44Z"
}
```

---

## Patterns to Follow

### Pattern 1: Idempotent Order Edits

**What:** Every Shopify order edit is safe to re-run because the code checks existing line items before issuing mutations.

**Why needed:** The pipeline can fail after 2400 of 2500 orders succeed. On resume, it must not double-apply edits to the 2400 already-done orders.

**How:** Before calling `beginEdit`, check whether the target variant already exists on the order at the expected quantity. If yes, skip. The check uses `fulfillableQuantity` on existing line items.

```python
@dataclass(frozen=True)
class OrderEditDecision:
    order_id: str
    already_applied: bool   # True → skip, no API call
    variants_to_add: tuple[VariantEdit, ...]

def plan_order_edit(order: ShopifyOrder, matrix_row: MatrixRow) -> OrderEditDecision:
    """Pure function. Compares live order state vs target matrix row.
    Returns skip decision if already applied — no side effects."""
    existing = {li.variant_gid for li in order.line_items if li.qty > 0}
    target = {v.gid for v in matrix_row.variants}
    if target.issubset(existing):
        return OrderEditDecision(order.id, already_applied=True, variants_to_add=())
    return OrderEditDecision(order.id, already_applied=False, variants_to_add=matrix_row.variants)
```

Shopify also supports `X-Shopify-Idempotency-Key` on GraphQL mutations (officially supported as of 2026-01 for inventory and refund mutations; use `orderInput.clientMutationId` for order edit deduplication). Use both: application-level check AND Shopify-level idempotency key.

**Confidence:** HIGH — verified against [Shopify idempotency docs](https://shopify.dev/docs/api/usage/implementing-idempotency)

### Pattern 2: Batch with Explicit Partial-Failure Tracking

**What:** Process orders in chunks of 50. After each chunk, write succeeded/failed IDs to the checkpoint. Never throw away the partial result.

**Why needed:** 2500 orders × 3 API calls each = ~7500 GraphQL requests per pass. Any network hiccup causes partial failure. The operator needs to know exactly which orders failed, not just a count.

```python
@dataclass(frozen=True)
class BatchSyncResult:
    pass_name: str                     # "pass1" or "pass2"
    succeeded: tuple[str, ...]         # order IDs
    failed: tuple[FailedOrder, ...]    # order ID + error message
    skipped: tuple[str, ...]           # already-applied, no API call needed

@dataclass(frozen=True)
class FailedOrder:
    order_id: str
    error: str
    graphql_errors: tuple[str, ...]
```

On resume, `OrderSync` reads the checkpoint's `failed_order_ids` list and processes only those orders. It never reprocesses succeeded orders.

### Pattern 3: Async for Shopify API Calls, Sync for Everything Else

**What:** Use `asyncio` + `aiohttp` for the Shopify GraphQL call loop. Keep everything else (XLSX processing, gift handling, finalization) synchronous.

**Why:** Shopify enforces a per-store rate limit (currently 1000 cost units/second bucket, GraphQL cost ~10/mutation). With 2500 orders × 3 mutations = 7500 requests, async concurrency with a semaphore-based rate limiter is necessary to finish in under 15 minutes. Threading works but asyncio uses less memory and makes the rate limiter easier to reason about.

**Rate limiter pattern:**

```python
async def sync_orders_async(
    decisions: list[OrderEditDecision],
    semaphore_size: int = 5,           # 5 concurrent requests → ~50 cost/s, well inside limit
) -> BatchSyncResult:
    sem = asyncio.Semaphore(semaphore_size)
    async with aiohttp.ClientSession() as session:
        tasks = [_edit_one_order(session, sem, d) for d in decisions if not d.already_applied]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    return _aggregate_results(decisions, results)
```

The semaphore is the rate limiter. Start at 5 concurrent; tune up if Shopify returns no 429s.

**Confidence:** MEDIUM — asyncio superiority for I/O-bound batch verified; specific semaphore value requires live tuning against actual Shopify store throttle response.

### Pattern 4: CheckpointStore as Single Source of Truth

**What:** All pipeline state lives in one JSON file on disk. Nothing lives in memory between steps. The Flask web UI reads this file to render current state — it does not hold state itself.

**Why:** The pipeline must survive a crash, a pywebview restart, or a browser refresh. If state is only in memory, a restart loses all progress.

```
.pipeline/
  checkpoint.json         ← current run state
  checkpoint.2026-04-05.bak  ← auto-backup before each step
  logs/
    pass1_failures.json   ← full error detail for failed orders
    pass2_failures.json
```

The web UI polls `GET /api/pipeline/state` every 2 seconds during active runs — no websockets, no SSE required for a 1-operator tool.

### Pattern 5: Steps as Pure Functions with Typed Boundaries

**What:** Every pipeline step is a standalone pure function (or async coroutine) with no hidden dependencies. All inputs passed explicitly. All outputs returned explicitly.

**Why this enables absorption:** The React developer needs to absorb each step as a spec. If each step is a pure function with a typed signature, they can: (1) read it and understand it without tracing global state, (2) reimplement it in TypeScript with the same contract, (3) test it in isolation.

```python
# WRONG: uses module globals, hidden file paths, shared state
def sync_orders():
    orders = _global_matrix.orders  # hidden dependency
    ...

# CORRECT: all inputs explicit, result returned
def sync_orders(
    matrix: ProductionMatrix,
    shopify_credentials: ShopifyCredentials,
    checkpoint: PipelineCheckpoint,
    pass_name: Literal["pass1", "pass2"],
) -> BatchSyncResult:
    ...
```

Each function's docstring should include: what it does, what the React dev will need to replicate, and which Shopify API calls it makes.

---

## Anti-Patterns to Avoid

### Anti-Pattern 1: Catch-All Exception Swallowing

**What:** `except Exception: pass` or `except Exception: continue` in the order sync loop.

**Why bad:** An order fails silently. The checkpoint marks it as succeeded. It never gets retried. The RMFG sheet is wrong. This is the most common failure mode in the existing codebase (26+ instances in CONCERNS.md).

**Instead:** Catch specific exceptions, log the full error with order ID, add to `failed` list in `BatchSyncResult`. Never suppress.

### Anti-Pattern 2: XLSX as Mutable Shared State

**What:** Passing an `openpyxl.Workbook` object through pipeline steps and mutating it in place.

**Why bad:** If step 4 (gift merge) fails halfway, the workbook is in an unknown state. No recovery without re-running from the start.

**Instead:** Each step returns a new immutable data structure (`ProductionMatrix` dataclass). The `Finalizer` at the end is the only thing that writes XLSX to disk.

### Anti-Pattern 3: Synchronous API Calls in Flask Routes

**What:** Calling Shopify GraphQL directly inside a Flask route handler.

**Why bad:** The 15-minute Saturday run blocks the HTTP worker. pywebview's embedded browser appears hung. No progress feedback possible.

**Instead:** Flask routes trigger async pipeline steps via a background thread (or subprocess). Routes return immediately with `{"status": "running"}`. Frontend polls for progress.

### Anti-Pattern 4: Two-Pass Logic Encoded in Ordering Only

**What:** "Pass 1 = rows 1-N, Pass 2 = rows N+1-end" — relying on XLSX row ordering to determine which pass an order belongs to.

**Why bad:** Any re-sort of the XLSX breaks the two-pass boundary. The existing codebase already has fragile string-parsing SKU logic that breaks on renames.

**Instead:** Pass membership is determined by SKU prefix (`PR-CJAM-` = pass 1; everything else = pass 2), encoded as a predicate function, not by position.

---

## Build Order (Phase Dependencies)

The correct build sequence follows data flow dependencies, not feature desirability:

```
Phase 1: CheckpointStore + PipelineRunner skeleton
  └─ Required by: everything. Without checkpoint, no resume.

Phase 2: OrderSync (the two-pass Shopify edit loop)
  └─ Requires: checkpoint (Phase 1)
  └─ Required by: MatrixGenerator (needs items on orders before fetching)
  └─ This is the highest-risk component — test first against live data.

Phase 3: InventorySync
  └─ Requires: checkpoint (Phase 1)
  └─ Independent of OrderSync; can build in parallel but run before it in pipeline.

Phase 4: MatrixGenerator
  └─ Requires: OrderSync complete (Phase 2) so live items are on orders
  └─ Replaces current RMFG portal download.

Phase 5: GiftHandler
  └─ Requires: MatrixGenerator output (Phase 4)
  └─ Pure function — easiest to test.

Phase 6: Finalizer
  └─ Requires: GiftHandler output (Phase 5)
  └─ Mostly existing logic from matrix_commander.py.

Phase 7: WebUI wiring
  └─ Requires: all steps complete
  └─ Flask routes expose checkpoint state; frontend polls.
```

**Critical path:** Phase 1 → Phase 2 → Phase 4 → Phase 5 → Phase 6. InventorySync (Phase 3) and WebUI (Phase 7) are off the critical path.

---

## Making Code Easy to Absorb

The React developer will need to absorb each component. Design for that from day one:

1. **One file per component.** `inventory_sync.py`, `order_sync.py`, `matrix_generator.py`, `gift_handler.py`, `finalizer.py`, `checkpoint_store.py`. No 14k-line monoliths.

2. **Typed boundaries.** Every public function uses dataclasses or TypedDicts for inputs and outputs. No raw dicts, no positional argument soup.

3. **Spec comments on each public function.** A comment block above each function that says: "React equivalent: POST /api/pipeline/sync-orders, same idempotency contract, same BatchSyncResult shape."

4. **No hidden singletons.** No module-level `_client = ShopifyClient()`. Pass credentials as arguments so the React dev can see exactly what auth context each call needs.

5. **Separate business logic from I/O.** `plan_order_edit()` is a pure function (testable without Shopify). `execute_order_edit()` is the API call. The React dev reimplements both separately.

---

## Scalability Notes

This tool is intentionally scoped to 400-2500 orders per cycle. No database is needed. The in-memory + XLSX + checkpoint-JSON approach is appropriate. The React absorption is the scale path, not SQLite or a message queue.

| Concern | At 400 orders | At 2500 orders | Beyond scope |
|---------|--------------|----------------|--------------|
| Shopify API time | ~2 min | ~12 min | React absorption |
| Memory | Negligible | ~50MB XLSX | React absorption |
| Checkpoint file | Trivial | Trivial | React absorption |
| Gift handling | Pure function | Pure function | React absorption |

---

## Sources

- [Shopify: Implementing idempotency](https://shopify.dev/docs/api/usage/implementing-idempotency) — HIGH confidence
- [Shopify: Idempotent requests](https://shopify.dev/docs/api/usage/idempotent-requests) — HIGH confidence
- [Shopify: Building Resilient GraphQL APIs Using Idempotency](https://shopify.engineering/building-resilient-graphql-apis-using-idempotency) — HIGH confidence
- [Prefect: Importance of Idempotent Data Pipelines](https://www.prefect.io/blog/the-importance-of-idempotent-data-pipelines-for-resilience) — MEDIUM confidence
- [PyPI: python-statemachine](https://pypi.org/project/python-statemachine/) — HIGH confidence (library exists, active)
- asyncio for I/O-bound API batch calls — HIGH confidence (established Python practice)

---

*Research date: 2026-04-04*
