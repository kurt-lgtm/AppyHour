# E1 + E7 — Cohort Tag Dropdown + box_sku_contains Substring Filter

**Goal:**
- **E1:** Make BIX/XMOM/ALP and other non-`_SHIP_*` cohort tags selectable in swap UI dropdown.
- **E7:** Allow restricting swaps to orders where any line item SKU contains a given substring (e.g. `TR-` to limit PK-FCUST swap to tray orders).

**Effort:** ~5 hr combined (both touch same files; bundling avoids double-edit).
**Risk:** 🟡 medium — tag dropdown can grow large; substring filter is post-hoc and safe.
**Files touched:** 3 (`shopify_swap.py`, `app.py`, `static/app.js`, `templates/index.html` — 4)
**Delivery:** single commit on `main`.

## Approach

**E1:** Extend `/api/swap/ship-tags` to optionally include cohort tags. Add tag-type selector in UI: Ship Date (default) / Cohort / All. Cohort = anything not starting with `_` and matching a curated whitelist + auto-discovered short-name tags.

**E7:** Add `box_sku_contains: list[str] | None = None` param to `find_swap_targets`. When provided, also require the order has at least one line item with a SKU containing any substring. Mirrors MCP tool's filter ([order_edit.py:269](../../../AppyHourMCP/tools/order_edit.py:269)). UI input below ship-tag dropdown.

## Tasks

### T1. Backend — `/api/swap/ship-tags` accepts `?type=`

`app.py:4461` route. Accept query param `type` ∈ `{ship, cohort, all}` (default `ship`).

```python
@app.route("/api/swap/ship-tags")
def swap_ship_tags():
    tag_type = (request.args.get("type") or "ship").lower()
    # ... fetch loop ...
    for t in (o.get("tags") or "").split(","):
        t = t.strip()
        if not t:
            continue
        if tag_type == "ship" and t.startswith("_SHIP_"):
            tags.add(t)
        elif tag_type == "cohort":
            # Cohort tags: short uppercase tokens, no underscore prefix, no spaces, no _SHIP_/RMFG_ date suffixes
            if (
                not t.startswith("_")
                and not t.startswith("!")
                and " " not in t
                and not re.match(r"^RMFG_\d", t)
                and t.isupper()
                and 2 <= len(t) <= 20
            ):
                tags.add(t)
        elif tag_type == "all":
            if not t.startswith("_") and not t.startswith("!"):
                tags.add(t)
    # ...
```

Sort cohort tags alphabetically (not reverse). Return `{tags: [...], type: tag_type}`.

### T2. Backend — `find_swap_targets` accepts `box_sku_contains`

`shopify_swap.py:64`. Add param after `bundle_only`:

```python
def find_swap_targets(
    store_url, token, ship_tag, old_sku,
    progress_callback=None,
    bundle_only: bool = True,
    box_sku_contains: list[str] | None = None,
) -> list[dict]:
```

After tag check (line ~101), pre-filter the order: if `box_sku_contains`, require any line item SKU to contain any substring.

```python
if box_sku_contains:
    order_skus = [(li.get("sku") or "").strip() for li in order_line_items]
    if not any(any(frag in sku for frag in box_sku_contains) for sku in order_skus):
        continue
```

Update docstring.

### T3. Backend — 4 routes accept and pass `box_sku_contains`

Same pattern as E2's `bundle_only`:

- `app.py:4336` (single preview), `app.py:4384` (single execute), `app.py:4513` (multi preview), `app.py:4591` (multi execute)
- Add `box_sku_contains = data.get("box_sku_contains") or None`
- Pass `box_sku_contains=box_sku_contains` to every `find_swap_targets()` call

### T4. Frontend — HTML

In `templates/index.html`, swap-pairs-panel area:

**T4a.** Add tag-type selector ABOVE the existing ship-tag `<select>`:

```html
<div style="display:flex;gap:4px;margin-bottom:6px;font-size:11px">
  <label><input type="radio" name="swap-tag-type" value="ship" checked onchange="loadSwapShipTags()"> Ship Date</label>
  <label><input type="radio" name="swap-tag-type" value="cohort" onchange="loadSwapShipTags()"> Cohort</label>
  <label><input type="radio" name="swap-tag-type" value="all" onchange="loadSwapShipTags()"> All</label>
</div>
```

**T4b.** Add box-SKU-contains input next to the include-paid checkbox panel:

```html
<div style="padding:8px;border-top:1px solid var(--border,#333)">
  <label style="font-size:11px;color:#aaa">Box SKU contains (comma-sep, optional):</label>
  <input type="text" id="swap-box-sku-contains" class="settings-input"
         placeholder="e.g. TR-, AHB-MCUST" style="width:100%;font-size:12px;margin-top:4px">
</div>
```

### T5. Frontend — JS

**T5a.** `loadSwapShipTags()` — read selected radio, append `?type=...` to fetch URL.

```js
async function loadSwapShipTags() {
    const tagType = document.querySelector('input[name="swap-tag-type"]:checked')?.value || 'ship';
    const sel = document.getElementById('swap-ship-tag');
    sel.innerHTML = '<option>Loading...</option>';
    const resp = await fetch(`/api/swap/ship-tags?type=${tagType}`);
    const data = await resp.json();
    sel.innerHTML = '<option value="">— Select tag —</option>' +
        (data.tags || []).map(t => `<option value="${t}">${t}</option>`).join('');
}
```

**T5b.** `previewSwaps()` and `executeSwaps()` — read box-SKU input, send as array.

```js
const boxSkuRaw = document.getElementById('swap-box-sku-contains')?.value.trim() || '';
const boxSkuContains = boxSkuRaw ? boxSkuRaw.split(',').map(s => s.trim()).filter(Boolean) : null;
// ...add to JSON body: box_sku_contains: boxSkuContains
```

## Verification

1. `python app.py --browser` → Swaps panel
2. **E1 ship**: dropdown populates `_SHIP_*` (existing behavior unchanged)
3. **E1 cohort**: switch radio → dropdown shows `BIX, XMOM, ALP, MAY, ...` (alphabetical)
4. **E1 all**: shows union excluding `_*`/`!*`
5. **E7**: select `_SHIP_2026-05-04`, swap PK-FCUST→PK-BITESGUIDE, box-SKU-contains=`TR-` → preview shows ~7 (matches session pattern)
6. Empty box-SKU input → no filter applied (count unchanged)
7. Backend regression: omit `type` param → defaults to `ship`, unchanged

## Negative constraints

- ❌ Do NOT add cohort tag whitelist hardcode — discover via heuristic (uppercase, short, no `_`/`!` prefix). Whitelist would rot.
- ❌ Do NOT make box_sku_contains case-insensitive — SKUs are uppercase by convention; case-sensitive matches MCP tool semantics
- ❌ Do NOT auto-trigger preview on tag-type change — keep user in control (only repopulates dropdown)
- ❌ Do NOT couple to E2's bundle_only logic — orthogonal filters
- ❌ Do NOT expose `type=all` as default — too noisy with junk tags

## Out of scope

- E3 count limiter, E4 failure classification, E5 batch queue, E6 wildcard SKU search

## Commit plan

Single atomic commit:
```
feat(swap): cohort tag dropdown + box_sku_contains substring filter

- /api/swap/ship-tags accepts ?type=ship|cohort|all
- find_swap_targets accepts box_sku_contains list
- 4 routes pass box_sku_contains through to helper
- UI: tag-type radio selector + box-SKU-contains text input
- Defaults preserve current behavior (type=ship, no filter)

Closes E1 + E7 from .claude/plans/2026-05-02-swap-ui-extension-scope.md
```

## UNVERIFIED to re-check at impl time

- `UNVERIFIED:` confirm `loadSwapShipTags` exists in app.js (Scout report mentioned it ~line 2415)
- `UNVERIFIED:` cohort tag heuristic on real data — may need to drop `isupper()` if mixed-case cohorts exist. Smoke test reveals.
- `UNVERIFIED:` `var(--border)` CSS var actually defined in styles.css (used in T4 borders); fall back to `#333` literal otherwise — already handled with `var(--border,#333)`.
