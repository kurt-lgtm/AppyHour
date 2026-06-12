# E2 — rc_bundle_only Toggle (Paid Item Swap)

**Goal:** Enable UI to swap paid line items, not just `_rc_bundle` items. Today the UI cannot swap paid items because [shopify_swap.py:112](shopify_swap.py:112) hardcodes `if "_rc_bundle" not in prop_names: continue`.

**Effort:** 2-3 hr.
**Risk:** 🔴 high — paid swaps modify customer-paid line items. Must be opt-in + confirmed.
**Files touched:** 4 (`shopify_swap.py`, `app.py`, `static/app.js`, `templates/index.html`)
**Delivery:** commit on current branch (no PR — single-operator tool).

## Approach

Add `bundle_only: bool = True` param to `find_swap_targets`. Default preserves current safe behavior. UI exposes checkbox "Include paid items (no exceptions)" — unchecked default. When checked: red warning + confirmation modal before execute.

## Tasks

### T1. Backend — `shopify_swap.py:64`

Add `bundle_only=True` keyword param to `find_swap_targets`:

```python
def find_swap_targets(
    store_url: str,
    token: str,
    ship_tag: str,
    old_sku: str,
    progress_callback=None,
    bundle_only: bool = True,
) -> list[dict]:
```

At line 112, gate the existing check:

```python
if bundle_only and "_rc_bundle" not in prop_names:
    continue
```

Update docstring (line 71-74) to mention the param.

### T2. Backend — `app.py` 4 routes

Update each caller to accept `bundle_only` from request JSON (default `True`):

- `app.py:4361` (single-pair preview) — line: `targets = find_swap_targets(store, token, ship_date, old_sku, bundle_only=bundle_only)`
- `app.py:4401` (single-pair execute) — same pattern, also pass `bundle_only` through to execute path
- `app.py:4546` (multi-pair preview) — accept top-level `bundle_only` from request, apply to every pair
- `app.py:4622` (multi-pair execute) — same

Read `bundle_only = request.json.get('bundle_only', True)` at top of each route.

### T3. Frontend — `templates/index.html:1064`

Add checkbox in swap panel (above SKU pair inputs):

```html
<label class="swap-paid-toggle">
  <input type="checkbox" id="swap-include-paid">
  Include paid items (no exceptions)
  <span class="warn-inline" id="swap-paid-warning" style="display:none">
    ⚠ Will modify customer-paid line items
  </span>
</label>
```

Show warning text only when checked (CSS or JS toggle).

### T4. Frontend — `static/app.js`

**4a.** In `previewSwaps()` (~line 2517) and `executeSwaps()` (~line 2575): include `bundle_only: !document.getElementById('swap-include-paid').checked` in POST body. (Note: UI checkbox = "include paid" = inverse of `bundle_only`.)

**4b.** In `executeSwaps()` before fetch: if `swap-include-paid` is checked AND preview count > 0, show confirm modal:
> "About to modify N paid customer line items in M orders. This is irreversible. Type SWAP to confirm:"
> [text input + Confirm button]

Reuse existing modal pattern if any; else inline `confirm()` w/ count.

**4c.** Toggle `#swap-paid-warning` visibility on checkbox change.

### T5. CSS — `static/styles.css`

Add minimal styles for `.swap-paid-toggle` + `.warn-inline` (red text, italic). Match existing badge color vars.

## Verification

After each task:
- `python app.py --browser` → http://127.0.0.1:5187 → Swaps panel
- T1+T2: smoke test bundle_only=true (current behavior unchanged) — preview MT-CAPO→MT-COPPA on `_SHIP_2026-05-04` should match prior counts (164 bundle)
- T3-T5: toggle checkbox on, preview same swap → expect higher count incl. paid (~166)
- Modal triggers when checkbox on + Execute clicked
- Default (checkbox off) preview matches old behavior exactly

**Regression risk:** Any caller that doesn't pass `bundle_only` keyword still gets `True` default → safe.

## Negative constraints (Do NOT)

- ❌ Do NOT change default behavior (`bundle_only=True`)
- ❌ Do NOT add `bundle_only` to matrix-upload route (`app.py:4684`) — separate workflow, scope creep
- ❌ Do NOT add to recharge-sync route (`app.py:4841`) — Recharge bundles ≠ Shopify line items
- ❌ Do NOT skip the confirm modal — paid swap = irreversible customer impact
- ❌ Do NOT couple this to E1/E7 (cohort tags, box_sku filter) — ship E2 standalone first

## Out of scope

- Cohort tag dropdown (E1)
- box_sku_contains filter (E7)
- Failure classification (E4)
- Count limiter (E3)

## Commit plan

Single atomic commit:
```
feat(swap): add bundle_only toggle to enable paid-item swaps

- find_swap_targets accepts bundle_only=True default
- 4 routes accept bundle_only from request JSON
- UI checkbox "Include paid items" with red warning
- Confirmation modal required when enabled + count > 0
- Default behavior unchanged
```

## UNVERIFIED claims to check during implementation

- `UNVERIFIED:` exact line numbers for `previewSwaps()` and `executeSwaps()` in app.js (estimated ~2517, ~2575 from explore agent — re-grep before edit)
- `UNVERIFIED:` whether app.py routes use Flask's `request.get_json()` or `request.json` — match existing pattern in same file
- `UNVERIFIED:` whether existing modal/confirm pattern exists in app.js — search before adding new
