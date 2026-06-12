# E3 + E6 — Count Limiter + Wildcard SKU Search

**Goals:**
- **E3:** Limit swap to first N orders (oldest first) of M matched. Closes "70 of 122" gap.
- **E6:** Accept wildcard SKU patterns (`*-HHIGH`, `TR-*`, `*BIX*`) — expand to concrete SKUs server-side, then proceed normally.

**Effort:** ~5 hr combined.
**Risk:** 🟡 medium — count limit is straightforward; wildcard introduces a SKU expansion step that can blow up if pattern is too loose.
**Files touched:** 4 (`shopify_swap.py`, `app.py`, `static/app.js`, `templates/index.html`)
**Delivery:** single commit on `main`.

## Approach

**E3:** Client-side slice. Preview returns full target list (existing); add a "Limit to first N" input next to Execute button. Pre-execute, slice `swapPreviewPairs` per pair to first N (sorted by order# ascending). Pass sliced list as a new `targets_override` param so backend skips `find_swap_targets` and uses the explicit list. Server-side validation: each `targets_override` entry has `order_gid`, `order_name`, `qty`.

**E6:** Add `find_skus_matching(store, token, pattern)` to `shopify_swap.py`. Pattern syntax:
- `*-HHIGH` → suffix match
- `TR-*` → prefix match
- `*BIX*` → substring match
- No `*` → exact (unchanged path)

Implementation: paginate `productVariants(first: 250, after: cursor)` filtered by Shopify's text search where possible (`query: "sku:HHIGH"` substring), then post-filter via Python regex/glob to enforce strict pattern. Return list of concrete SKUs.

UI: when OLD_SKU contains `*`, show "Resolve" button next to pair. Click → calls `/api/swap/resolve-sku?pattern=*-HHIGH` → returns SKU list + count. User confirms (e.g. "7 SKUs match: PR-CJAM-HHIGH (121), CEX-EC-HHIGH (113), ...") → those SKUs get added as separate pairs to swap-pairs-list, original wildcard removed.

## Tasks

### T1. Backend — `find_skus_matching` helper

`shopify_swap.py`. New function:

```python
def find_skus_matching(store_url: str, token: str, pattern: str) -> list[str]:
    """Resolve wildcard SKU pattern to concrete SKU list.

    Patterns: '*-HHIGH', 'TR-*', '*BIX*', or exact (no `*`).
    Returns: list of unique SKUs that match.
    """
    import fnmatch
    if "*" not in pattern:
        return [pattern]

    # Extract searchable substring (longest non-* token) for Shopify text query
    parts = [p for p in pattern.split("*") if p]
    search_term = max(parts, key=len) if parts else ""

    matches = set()
    cursor = None
    while True:
        after = f', after: "{cursor}"' if cursor else ""
        q_filter = f'sku:{search_term}' if search_term else ''
        query = (
            '{ productVariants(first: 250'
            + (f', query: "{q_filter}"' if q_filter else '')
            + after + ') { '
            'pageInfo { hasNextPage endCursor } '
            'edges { node { sku } } } }'
        )
        data = _gql(store_url, token, query)
        pv = data["productVariants"]
        for edge in pv["edges"]:
            sku = (edge["node"].get("sku") or "").strip()
            if sku and fnmatch.fnmatchcase(sku, pattern):
                matches.add(sku)
        if not pv["pageInfo"]["hasNextPage"]:
            break
        cursor = pv["pageInfo"]["endCursor"]

    return sorted(matches)
```

Edge cases:
- empty pattern → return []
- pattern with no `*` → return as-is (caller bypasses resolve)
- max 1000 results safety cap

### T2. Backend — `/api/swap/resolve-sku` route

```python
@app.route("/api/swap/resolve-sku", methods=["POST"])
def swap_resolve_sku():
    """Expand wildcard SKU pattern to concrete SKUs.

    Body: {pattern: '*-HHIGH', ship_tag, bundle_only, box_sku_contains}
    Returns: {pattern, skus: [{sku, count}], total}
    Per-SKU count = orders matching that SKU under the same filters.
    """
    from shopify_swap import find_skus_matching, find_swap_targets
    data = request.get_json(force=True)
    pattern = (data.get("pattern") or "").strip()
    ship_tag = data.get("ship_tag", "")
    bundle_only = data.get("bundle_only", True)
    box_sku_contains = data.get("box_sku_contains") or None

    if "*" not in pattern:
        return jsonify({"pattern": pattern, "skus": [{"sku": pattern, "count": 0}], "total": 0, "exact": True})

    s = _s()
    store = s.get("shopify_store_url", "")
    token = s.get("shopify_access_token", "")

    skus = find_skus_matching(store, token, pattern)
    if not skus:
        return jsonify({"pattern": pattern, "skus": [], "total": 0})

    # Optional: count orders per SKU (only if ship_tag provided — else skip to keep cheap)
    sku_counts = []
    if ship_tag:
        for sku in skus:
            targets = find_swap_targets(store, token, ship_tag, sku, bundle_only=bundle_only, box_sku_contains=box_sku_contains)
            sku_counts.append({"sku": sku, "count": len(targets)})
    else:
        sku_counts = [{"sku": s, "count": None} for s in skus]

    return jsonify({"pattern": pattern, "skus": sku_counts, "total": sum(s.get("count") or 0 for s in sku_counts)})
```

### T3. Backend — `targets_override` param for execute path

`app.py:swap_multi_execute` (~line 4609). Accept `targets_override` per-pair:

```python
# In _worker, per pair:
override = pair.get("targets_override")  # list of {order_gid, order_name, qty} or None
if override:
    targets = override
else:
    targets = find_swap_targets(store, token, ship_tag, old_sku, bundle_only=bundle_only, box_sku_contains=box_sku_contains)
```

No new top-level field needed; per-pair override is more flexible.

### T4. Frontend — count limit UI

`templates/index.html`. Add "Limit to first N" input next to Execute button:

```html
<div style="display:flex;align-items:center;gap:6px;font-size:11px;margin-right:8px">
  <label style="color:#aaa">Limit:</label>
  <input type="number" id="swap-limit" min="0" placeholder="0=all" style="width:70px;font-size:12px">
  <span id="swap-limit-hint" style="color:#888"></span>
</div>
```

Show `swap-limit-hint` as `"of N"` when preview has N matches.

### T5. Frontend — wildcard resolve button + JS

In swap-pairs-list rendering (search `addSwapPair`/`renderSwapPairs`), if `old_sku.includes('*')`:
- show ⚠ wildcard badge + "Resolve" button per row
- on click: POST to `/api/swap/resolve-sku` with current ship_tag + filters
- show result list, user confirms → replace wildcard pair with N concrete pairs (each old=concrete sku, new=same new_sku)

```js
async function resolveWildcardPair(idx) {
    const pair = swapPairs[idx];
    if (!pair.old_sku.includes('*')) return;
    const shipTag = document.getElementById('swap-ship-tag').value;
    const bundleOnly = !(document.getElementById('swap-include-paid')?.checked);
    const boxSkuRaw = document.getElementById('swap-box-sku-contains')?.value.trim() || '';
    const boxSkuContains = boxSkuRaw ? boxSkuRaw.split(',').map(s=>s.trim()).filter(Boolean) : null;

    setMascot('loading', `Resolving ${pair.old_sku}...`);
    const resp = await fetch('/api/swap/resolve-sku', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({pattern: pair.old_sku, ship_tag: shipTag, bundle_only: bundleOnly, box_sku_contains: boxSkuContains}),
    });
    const data = await resp.json();
    if (!data.skus.length) { setMascot('alert', 'No SKUs match'); return; }
    const summary = data.skus.map(s => `${s.sku}${s.count !== null ? ` (${s.count})` : ''}`).join('\n');
    if (!confirm(`Pattern ${pair.old_sku} matches ${data.skus.length} SKUs:\n\n${summary}\n\nReplace wildcard pair with ${data.skus.length} concrete pairs?`)) return;
    // Remove wildcard, append concrete pairs
    swapPairs.splice(idx, 1);
    data.skus.forEach(s => swapPairs.push({old_sku: s.sku, new_sku: pair.new_sku}));
    renderSwapPairs();
    setMascot('happy', `Expanded to ${data.skus.length} pairs`);
}
```

### T6. Frontend — apply count limit pre-execute

In `executeSwaps()` after getting `pairsWithGids`:

```js
const limitN = parseInt(document.getElementById('swap-limit')?.value || '0', 10);
if (limitN > 0) {
    // Group preview targets by old_sku → pair, sort by order#, take first N per pair
    const targetsByPair = {};
    swapPreviewData.forEach(t => {
        const key = t.old_sku;
        (targetsByPair[key] = targetsByPair[key] || []).push(t);
    });
    pairsWithGids.forEach(p => {
        const list = (targetsByPair[p.old_sku] || []);
        list.sort((a, b) => parseInt(a.order_name.replace('#','')) - parseInt(b.order_name.replace('#','')));
        p.targets_override = list.slice(0, limitN).map(t => ({
            order_gid: t.order_gid, order_name: t.order_name, qty: t.qty
        }));
    });
}
```

⚠ Need preview to return `order_gid` per target (currently doesn't). Update `swap_multi_preview` to include `order_gid` in `all_targets` items.

### T7. Backend — preview includes `order_gid`

`app.py:swap_multi_preview` `all_targets.append`:

```python
all_targets.append({
    "order_name": t["order_name"],
    "order_gid": t["order_gid"],   # NEW
    "old_sku": old_sku,
    "new_sku": new_sku,
    "qty": t["qty"],
})
```

## Verification

1. **E3:** preview MT-CAPO→MT-COPPA on `_SHIP_2026-05-04` → ~166 (existing). Set Limit=10 → execute → 10 swapped, oldest order#s.
2. **E3:** Limit=0 (default) → all 166 swapped (unchanged behavior).
3. **E6:** add pair `*-HHIGH` → CH-WMANG, click Resolve → modal shows ~7 SKUs (PR-CJAM-HHIGH, CEX-EC-HHIGH, etc.). Confirm → list expands to 7 pairs.
4. **E6:** pair `EXACT-SKU` (no `*`) → no Resolve button, normal flow.
5. Combined: wildcard `*-HHIGH` + Limit=70 → 7 pairs in preview, 70 first-by-order# orders execute (matches CH-ALP→CH-WMANG session pattern).

## Negative constraints

- ❌ Do NOT auto-resolve wildcards on Add Pair — let user click Resolve (avoids surprise scans)
- ❌ Do NOT count-limit on dry-run preview (preview shows full count → informs the limit)
- ❌ Do NOT cap N at preview count silently — let user enter higher N (matches all)
- ❌ Do NOT slice across pairs — each pair gets its own first-N (e.g. 70 PR-CJAM + 70 CEX-EC, not 70 total)
- ❌ Do NOT skip pattern validation — at least one non-`*` char required, else reject

## Out of scope

- E5 batch queue (5+ independent swaps) — separate phase
- Saved presets

## Commit plan

```
feat(swap): count limiter + wildcard SKU search

- shopify_swap.find_skus_matching() expands *-HHIGH/TR-*/*BIX* via productVariants paginate + fnmatch
- /api/swap/resolve-sku route returns matched SKUs + per-SKU order counts
- multi-execute accepts per-pair targets_override to skip find_swap_targets
- multi-preview includes order_gid per target for client-side slicing
- UI: Limit input + per-pair Resolve button when OLD_SKU contains *

Closes E3 + E6 from .claude/plans/2026-05-02-swap-ui-extension-scope.md
```

## UNVERIFIED

- `UNVERIFIED:` Shopify `productVariants(query: "sku:HHIGH")` substring behavior — may require leading wildcard or different syntax. Test with known SKU before trusting search_term shortcut. Worst case: drop search filter and paginate all variants (slow but correct).
- `UNVERIFIED:` `swapPairs` object shape — confirm `addSwapPair` stores `{old_sku, new_sku}` and `renderSwapPairs` exists with that name
- `UNVERIFIED:` `swap-limit-hint` integration into preview render path (`renderSwapPreview`)
