# E4 — Failure Classification + Retry Transients + Locked Backfill

**Goal:** Differentiate swap failures by type (🔒 locked / ⏱ transient / ❌ other), enable one-click retry of transients, optionally backfill locked from unselected pool.

**Effort:** ~3 hr.
**Risk:** 🟡 medium — touches result shape (UI parsing must match), retry path must not double-execute success rows.
**Files touched:** 3 (`shopify_swap.py`, `app.py`, `static/app.js`)
**Delivery:** single commit on `main`.

## Approach

**Backend:** `execute_bulk_swap` already returns `errors: list[str]`. Wrap each error string with order_name + classified bucket. Change return shape:

```python
{
  "total": N,
  "success": N,
  "failed": N,
  "locked": [{"order_name": "#134486", "error": "..."}, ...],
  "transient": [{"order_name": "#130270", "error": "..."}, ...],
  "other":     [{"order_name": "#XXXXX",  "error": "..."}, ...],
  "successful_orders": ["#127435", ...],   # for retry exclusion
  "errors": [...]                          # legacy field, keep for back-compat
}
```

Classification (substring match on error text):
- `"order cannot be edited"` or `"beginEdit failed"` w/ that message → **locked**
- `"502"`, `"503"`, `"504"`, `"Bad Gateway"`, `"timeout"`, `"ChunkedEncodingError"` → **transient**
- everything else → **other**

**Frontend:** new result panel sections `#swap-result-locked`, `#swap-result-transient`, `#swap-result-other` with counts + collapsible order lists. Buttons: "Retry transients" (re-run swap, same params, only on transient orders), "Backfill locked" (find next N eligible orders excluding all attempted).

**Retry transients:** new route `/api/swap/retry-transients` — accepts `{ship_tag, pairs, bundle_only, box_sku_contains, retry_orders: [...]}`. Skips `find_swap_targets`; iterates given order names, looks up GIDs, calls `execute_swap` directly per order.

**Backfill locked:** new route `/api/swap/backfill-locked` — accepts `{ship_tag, pairs, bundle_only, box_sku_contains, exclude_orders: [...], count: N}`. Re-runs `find_swap_targets`, filters out exclude list, takes first N, executes.

## Tasks

### T1. Backend — `shopify_swap.py:execute_swap` return order_name

`execute_swap` currently returns `{success, error}`. Add `order_name` so `execute_bulk_swap` can populate buckets without round-trip.

Change [shopify_swap.py:142](shopify_swap.py:142) — pass `order_name` param OR derive from existing target dict in `execute_bulk_swap` (already has `t["order_name"]`). Keep `execute_swap` signature stable; do classification in `execute_bulk_swap` loop instead.

### T2. Backend — `execute_bulk_swap` classify + bucket

In the iteration loop (currently `~line 280`), when `res["success"]` is False, classify error text and append to bucket:

```python
import re
LOCKED_RE = re.compile(r"cannot be edited|beginEdit failed.*cannot be edited", re.I)
TRANSIENT_RE = re.compile(r"\b50[234]\b|Bad Gateway|timeout|ChunkedEncoding|Connection reset", re.I)

# ...inside loop:
for t in targets:
    if cancel_flag and cancel_flag[0]: break
    res = execute_swap(...)
    if res["success"]:
        successful_orders.append(t["order_name"])
        success += 1
    else:
        err = str(res["error"] or "")
        item = {"order_name": t["order_name"], "error": err}
        if LOCKED_RE.search(err):
            locked.append(item)
        elif TRANSIENT_RE.search(err):
            transient.append(item)
        else:
            other.append(item)
        failed += 1
        errors.append(f"{t['order_name']}: {err}")  # legacy
```

Add `locked`, `transient`, `other`, `successful_orders` to return dict. Keep `errors` legacy.

### T3. Backend — new route `/api/swap/retry-transients`

After `swap_multi_execute` (~`app.py:4682`):

```python
@app.route("/api/swap/retry-transients", methods=["POST"])
def swap_retry_transients():
    """Re-execute given orders without re-querying targets. Used after transient 502s."""
    from shopify_swap import lookup_variant_gid, execute_swap, _gql
    # acquire lock + spawn worker thread (mirror multi-execute pattern)
    # input: {ship_tag, pairs:[{old_sku, new_sku, new_variant_gid}], retry_orders: ["#136545", ...]}
    # for each retry order: _gql to resolve order_gid, then for each pair, execute_swap(old_sku, new_variant_gid)
    # return same classified result shape
```

### T4. Backend — new route `/api/swap/backfill-locked`

Same file. Re-runs `find_swap_targets` with original filters, drops `exclude_orders`, slices to `count`, executes.

```python
@app.route("/api/swap/backfill-locked", methods=["POST"])
def swap_backfill_locked():
    # input: {ship_tag, pairs, bundle_only, box_sku_contains, exclude_orders: [...], count: N}
    # for each pair: targets = find_swap_targets(...); filter exclude; sort by order#; take N
    # execute_bulk_swap on filtered list
```

### T5. Frontend — result panel sections

In result render path (search `swap-result` / `pollSwapProgress` finalizer in app.js):
- 3 collapsible `<details>` blocks for locked/transient/other
- Each shows count + order list
- "Retry transients" button (visible only if `transient.length > 0`)
- "Backfill locked" button (visible only if `locked.length > 0` AND original execute had a count limit OR user enables "make-up shortage")

### T6. Frontend — wire retry + backfill

```js
async function retryTransients() {
    const orders = lastSwapResult.transient.map(x => x.order_name);
    const body = { ship_tag: lastShipTag, pairs: lastPairsWithGids, retry_orders: orders };
    // POST /api/swap/retry-transients
    // re-render result, merge buckets (move resolved transients to success)
}

async function backfillLocked() {
    const count = lastSwapResult.locked.length;
    const exclude = [...lastSwapResult.successful_orders, ...lastSwapResult.locked.map(x=>x.order_name)];
    const body = { ship_tag: lastShipTag, pairs: lastPairsWithGids, bundle_only, box_sku_contains, exclude_orders: exclude, count };
    // POST /api/swap/backfill-locked
}
```

Track `lastSwapResult`, `lastShipTag`, `lastPairsWithGids`, `lastBundleOnly`, `lastBoxSkuContains` in module-level state.

## Verification

1. Trigger an intentional 502 (kill network mid-swap, or use small known-locked order)
2. Run swap → result panel shows locked/transient/other buckets
3. Click "Retry transients" → transient bucket re-runs, success count rises
4. Click "Backfill locked" → finds replacement orders, executes
5. Old result shape callers (matrix-upload, recharge-sync) ignore new fields → no regression

## Negative constraints

- ❌ Do NOT auto-retry — user clicks button (avoid silent double-charges)
- ❌ Do NOT backfill paid items unless original swap had `bundle_only=false` (preserve user intent)
- ❌ Do NOT re-execute successful_orders on retry (idempotency safety)
- ❌ Do NOT block on backfill — if no eligible replacements found, surface 0 with message

## Out of scope

- E3 count limiter, E5 batch queue, E6 wildcard SKU search

## Commit plan

```
feat(swap): classify failures (locked/transient/other) + retry + backfill

- execute_bulk_swap returns locked/transient/other buckets + successful_orders
- New routes: /api/swap/retry-transients, /api/swap/backfill-locked
- UI: collapsible result sections per bucket, Retry/Backfill buttons
- Retry skips find_swap_targets, executes given order list directly
- Backfill re-queries with exclusions, takes first N

Closes E4 from .claude/plans/2026-05-02-swap-ui-extension-scope.md
```

## UNVERIFIED

- `UNVERIFIED:` exact line of result render in app.js — grep `pollSwapProgress` + `result` + `setMascot` finalizer
- `UNVERIFIED:` whether existing `_swap_progress["result"]` consumers expect strict shape — search for `data.result.` / `result.errors`
- `UNVERIFIED:` whether `lookup_variant_gid` is needed in retry route (caller already has GIDs from previous preview — pass through, skip lookup)
