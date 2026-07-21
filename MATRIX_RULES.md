# Matrix Tool — Constraints (single source of truth)

> 🔴 **PRE-CHANGE GATE:** read this BEFORE touching `matrix_commander.py`, `matrix_commander_web/`,
> `ShipRouting/scripts/gen_rmfg_export.py`, or `AppyHourMCP` matrix tools. Change rules HERE first —
> same commit as the code. Gotchas/negatives-first: each rule below encodes a failure that shipped.

> 🧭 **NORTH STAR:** the matrix RMFG picks from matches paid demand EXACTLY — zero wrong physical boxes.
> Every check in this doc exists because an error here becomes a mis-packed box at a customer's door
> (the CEX-EC box-size proxy over-cut ~6× before the line-item rule).

**What it is:** the canonical RMFG production-matrix export pipeline —
`matrix_commander.py generate` (Shopify → Access_LIVE matrix xlsx) → `gen_rmfg_export.py` wrapper
(generate → col-L curation → autofit → QC) → `qc_audit` gate. Validation twin:
`appyhour_validate_production_matrix` MCP tool. Inputs: live Shopify orders by RMFG/_SHIP_ tag.
Output: the xlsx Tommy/RMFG picks from — errors here become wrong physical boxes.

## Rules (negatives-first)

0. **Column D "Total" = sum of ALL product columns on the row (`=SUM(O:EK)` semantics)** — NOT a
   CH/MT/AC-only food count (Kurt 2026-07-10: wk0713 vF shipped with food-count totals; TR/PK
   quantities were missing and Kurt hand-fixed row 17 with `=SUM(O17:EK17)`). If metadata columns
   ever shift the product-column start, the total must still cover exactly the product columns.

1. **CEX-EC is a 3-level parent chain — resolve down it, never sideways** (Kurt 2026-07-02):
   `CEX-EC` (bare parent line) → `CEX-EC-{suffix}` (curation child) → applied `CH-` SKU (the cheese
   actually picked). All three legitimately COEXIST on one order — bare + suffix together is VALID,
   not a duplicate to clean (vault SKU Quirks). NEVER resolve curation from tag substrings: `cur in
   tag` made `CEXEC.3.10.NMS` match both NMS and MS → phantom expected-cheese flags (removed
   2026-07-02, `check_cexec_cheese_counts`). Curation comes ONLY from the `CEX-EC-{suffix}` level;
   the QC check walks the chain: bare with NO suffix line = unresolved → FLAG (never silently skip);
   suffix with the expected `CH-` absent from assignments = missing cheese → FLAG. The `cex_ec`
   settings map (suffix → CH-) is the level-2→3 edge for demand/QC, not an order fix
   (`feedback_cexec_resolution_rules`).
2. **CEX-EC counts off the actual CEX-EC line item, NEVER box size** — box-size proxy over-cut ~6×.
   CEX-EC = paid add-on on ANY box size. Trays (`AHB-*CUST-TRAY`) must never carry CEX-EC (no
   curation → no valid suffix); a tray+CEX-EC is an upstream Recharge-collection defect, not a
   per-order fix.
3. **Shopify REST `orders.json` has NO `tag` filter** — it silently ignores the param and returns
   ALL open unfulfilled orders (`_fetch_orders_by_tag` shipped this way; worst inside `allocate`,
   which then computed paid demand store-wide). Filter tags client-side (EXACT comma-split match,
   never substring — `RMFG_2026070` must not match `RMFG_20260706`) or use the GraphQL tag query.
   ✅ FIXED 2026-07-02 in all three instances: `matrix_commander._fetch_orders_by_tag`,
   `AppyHourMCP/tools/matrix_qc._fetch_orders_by_tag` (also forces `tags` into `fields`), and
   `InventoryReorder/inventory_reorder.py` customer-lifecycle fetch (was double-counting the whole
   store via a two-tag loop). Any NEW REST fetch must never pass a `tag` param.
4. **Always filter line items to fulfillable** — GraphQL `fulfillableQuantity` / REST
   `fulfillable_quantity > 0`; removed/refunded items otherwise get picked.
5. **QC must GATE, not report** — `qc_audit` exit 1 propagates through the wrapper. Never deliver an
   xlsx from a FAILed run. ⚠️ Known gap: the xlsx is written to Downloads BEFORE QC, so a failed run
   leaves a legit-looking file — treat any xlsx without a PASS log line as untrusted.
6. **Unattended runs use `generate` only** — `full`/`swap` paths contain `input()` prompts and will
   hang a scheduled run.
7. **All SKU prefixes are pickable** (PK/MR carry 0 DistVol) — any "non-pickable PK/TR" list in this
   codebase is stale doc-rot, not a rule.
8. **Read-only against Shopify by default** — writes (`sync-shopify`, `allocate --commit`) are
   dry-run-default and stay that way. Nothing in this pipeline touches `shipping.db`.
9. **Web UI (`matrix_commander_web`) must route through the CLI pipeline functions** — its `/api/sync`
   shipped both broken (dataclass `.get()`) AND bypassing the limiter/guard/checkpoints. Never
   re-implement sync in the web layer. ⚠️ Endpoint still broken as of 2026-07-02.
10. **Column-name → SKU resolution is layered; a name resolving must NEVER pass MFG onboarding**
    (2026-07-03): `parse_matrix` resolves `constants.NAME_TO_SKU` → `mfg_translations.csv` reverse →
    live Shopify product titles + raw SKUs (lazy, only when local layers leave gaps; offline → `??-`
    fallback as before). The Shopify layer exists because validation showed `??-` garbage for names
    known to Shopify — but a Shopify-resolved SKU is still NOT onboarded at RMFG; the
    `check_mfg_onboarding` FAIL must stay loud for it. Never treat "name resolved" as "RMFG can pick
    it". Same resolver feeds `apply_swaps_to_xlsx` column lookup — do NOT let the two drift back to
    `NAME_TO_SKU`-only (swaps silently skipped columns before). `mfg_translations.csv` is now
    git-tracked — refresh it from translator.robbinsmfginc.com via commit, never an untracked drop.
12. **Allocate sets AVAILABLE = max(0, HAVE − paid(week) − $0-in-box(week)); demand scope is the SHIP WEEK ONLY** (Kurt 2026-07-21, superseding the 2026-07-03 on_hand form). **Failure that forced the change:** the on_hand form (`on_hand = HAVE − paid`, letting Shopify derive available) assumed `committed` ≈ this week's curation demand — but committed spans **ALL open unfulfilled orders across every ship week** (CH-QOTA 2026-07-21: HAVE 229 pushed as on_hand, committed 328 from multiple cohorts → available **−99**; the SKU read unavailable while the week was fully covered). A week-scoped HAVE must never be netted against Shopify's week-agnostic committed. (a) Write **`available`** directly = max(0, HAVE − NEED) where NEED = ALL fulfillable units of the `_SHIP_<mon>` cohort (paid variant + $0 in-box). Shopify back-computes on_hand = available + committed; that inflated on_hand is accepted fiction — `available` is the number the portal/customization reads, and it's the one that must be right. Never revert to on_hand-derived available. (b) Count paid **by the priced variant's id** (`_variant_catalog_prices` catalog price > 0), NEVER by line-item `price` — paid/curation lines routinely show $0 on the line, so price-based counting is inverted (CH-GAOP read paid=1 instead of 76). (c) Use the **`_SHIP_<mon>` ship tag** for the cohort, not the RMFG cut tag. Verify post-push: $0 variant's `available` at RMFG == max(0, HAVE − NEED), with an UNCACHED read (`_shopify_graphql_matrix`); the cached path returns stale pre-push values.
13. **One HAVE sheet feeds BOTH the cross-check and the Shopify On Hand push** (2026-07-03): the
    web Inventory tab's loaded inventory drives `/api/allocate` → `compute_allocation` /
    `apply_allocation` (extracted from `cmd_allocate`; web routes through them per rule 9 — never
    re-implement the AVAIL$0 math in the web layer). Gotchas: **a tag matching 0 orders is
    REFUSED** (paid=0 → push would set Available to raw HAVE and let parents over-allocate into
    paid units — the exact failure allocate exists to prevent); commit requires a fresh preview and
    invalidates it after push (re-preview after swaps); PK-/MR- structural SKUs stay uncapped;
    Available push remains manual-confirm — never auto-commit (inventorySetQuantities is
    effectively last-writer-wins even with changeFromQuantity read-then-write).
    **REGRESSION (2026-07-07):** `apply_allocation` read the `changeFromQuantity` (current on_hand,
    the optimistic-lock arg) via the CACHED `_shopify_graphql` — so on any re-push of a SKU whose
    on_hand was just changed, the stale cached value no longer matched the persisted quantity and
    Shopify returned `changeFromQuantity argument no longer matches the persisted quantity` (hit live
    on CH-MONT, failed twice, only succeeded via an uncached read). Rule 12 already mandates the
    uncached path — the optimistic-lock read MUST use `_shopify_graphql_matrix`, never the cached
    `_shopify_graphql`. Do not revert this read to the cached path.

14. **Col L (Tags) is QC-GATED for routing + ice at validation time** (`check_routing_and_ice`, 2026-07-03).
    The export is the LAST artifact RMFG reads; on 2026-07-03 untagged CS drift-ins, 1522 missing-ice
    orders, 176 residential-forcing `!FedEx Home Delivery` pins and 378 leaky single-hub fences ALL
    reached the live cohort and were caught only by manual near-deadline checking. The check imports
    the SHARED gate `ShipRouting/lib/qc_gate.py` (the same module apply.py runs pre-write — one gate,
    two chokepoints): 5-form tag grammar (ROUTING_RULES §12; `lib/canon` is the ONLY parser — never
    hand-roll a tag regex), untagged, HD pins, leaky fences, ice policy (`MAX_ICE_NONTRAY` env),
    Indy pin count (info-only here — the CAP is enforced apply-side where box data lives). A missing/
    unimportable qc_gate = the check FAILS loud, never skips. Trays = any TR-/TRAY SKU in assignments.

15. **Col L must PRESERVE `Gift_Redemption` and `Reship - <reason>` tags alongside route+gel**
    (Kurt 2026-07-10). The wk0713 export's col-L filter kept ONLY routing+gel tags (correct fix for
    the 10,469-stray-tag FORMAT trip), but it also stripped gift/reship identity — RMFG couldn't
    tell which rows were gift redemptions or reships, and warm-history reships that should have been
    obvious extra-ice candidates were invisible on the sheet. The allowed col-L set = 5-form routing
    grammar + gel tags + `Gift_Redemption` + `Reship - <reason>` (and nested `Shipping::<reason>::`),
    NOTHING else. A col L with any other stray tag OR missing gift/reship identity = regression.
    ✅ Implemented 2026-07-10: `qc_gate.is_identity_tag`/`identity_tags_of` is the ONE definition
    (Gift_Redemption underscore OR space form, `Reship*`, `Shipping::*`); `gen_rmfg_sheet` +
    `ice_distvol_workflow.write_export` preserve them in col L; `qc_audit` FORMAT exempts them from
    the stray check. Tests: `ShipRouting/tests/test_identity_tags.py`.

16. **Reships — `Arrived Warm` history above all — get the 3×48 upgrade whenever slack allows**
    (Kurt 2026-07-10). wk0713: several Arrived-Warm reships rode at standard max ice while the 3×48
    pass targeted only forecast-heat orders — a reship IS a prior cold-chain failure; re-icing it at
    the same level it failed at re-runs the experiment. `ice_distvol_workflow` target selection must
    include every non-tray, non-air reship with DistVol slack ≥0.5 regardless of forecast margin.
    ✅ Implemented 2026-07-10 (`select_targets`: `qc_gate.is_reship_tag` reships bypass the
    max-config + under-iced gates; tray/air/slack gates still apply; the dry-run report counts
    included reships). Tests: `ShipRouting/tests/test_identity_tags.py`.

15b. **Export product headers use the RMFG TRANSLATOR's exact name form** (Kurt 2026-07-10). The vF
    emitted `Walnut, Honey & Extra Virgin Olive Oil Crackers` (Shopify title, comma) but RMFG's
    translator maps `Walnut Honey & …` (no comma) — 545 units would have failed their import mapping.
    The translator export (`meal-type-export-appy-hour-*.csv`, latest in Downloads) is the authority
    for header spelling; also mind its trailing-dot pairs (`Prairie Breeze.`=CH-PRBZ≠CH-BRZ,
    `…Sharp Cheddar.`=CH-V5CH≠CH-ACAC) — a period is a different product.
    ✅ Implemented 2026-07-17 (wk0720 walnut-header pair): (a) `merge_gift_xlsx` now merges by
    HEADER (column union), normalizing both sheets to the no-comma walnut form first
    (`_normalize_rule15b_header`) — comma vs no-comma no longer splits AC-FCWALN demand across
    two columns; (b) `constants.NAME_TO_SKU` maps BOTH walnut forms → AC-FCWALN so the
    rule-15b-correct submitted sheet passes `check_sku_mappings` (was false-failing weekly).
    Do NOT strip punctuation generally — the trailing-dot pairs above are DIFFERENT products;
    only the walnut comma is normalized. Residual: `mfg_translations.csv` AC-FCWALN row still
    carries the comma form, so generated sheets still EMIT the comma header (resolver + merge
    normalize it downstream). Tests: `tests/test_gift_merge_walnut.py`.

18. **Notes column ships EMPTY** (Kurt 2026-07-10): the vF export's Notes column must be blank on
    the submitted sheet — whatever the generator or order data puts there, clear it before submit.

19. **Every pickable prefix that can appear on an order needs a product column** (wk0720,
    RMFG_20260717): `generate_matrix_xlsx`'s `food_pkg_prefixes` filter (matrix_commander.py) is the
    ONLY thing that decides which SKUs become product columns. It shipped as
    `("CH-","MT-","AC-","PK-","TR-")` and silently omitted `MR-` — 38 orders had `MR-JRNL`
    ("Cheese Journal") and it was MISSING from the generated sheet until caught by hand and injected
    post-hoc (`_outputs/artifacts/wk0720_rmfg_combine.py`). Rule 7 already declares all prefixes
    pickable, but that doc line was never mirrored into the column filter — a doc-only "pickable"
    claim does NOT create a column. Any pickable prefix a real order can carry (`CH-/MT-/AC-/PK-/TR-/MR-`)
    MUST be in `food_pkg_prefixes` AND in `_active_prefixes(2)` / `sync_order_to_shopify`'s
    `active_prefixes` default (the sync path that adds the $0 in-box variant), or the SKU is invisible
    to both the sheet and Shopify. When a new pickable prefix is onboarded, add it to ALL THREE tuples
    in the same change and add a regression test asserting its column appears
    (`tests/test_generate_matrix.py::test_mr_jrnl_gets_column`). MR- carries 0 DistVol so it is never
    inventory-capped (already handled at `matrix_commander.py:3004`, `("PK-","MR-")`) and its qty rides
    into the col-D Total per rule 0.

17. **One ice-target list per cohort, one canonical invocation** (Kurt 2026-07-10). Two agents ran
    `ice_distvol_workflow.py` the same evening with different tag args (`RMFG_20260710` vs
    `_SHIP_2026-07-13`) and got 293 vs 725 targets — the tag silently changes the cohort fetch AND
    the effective-TNT/weather basis, and each run wrote its own `ice_overcap_override_*.json`. Rules:
    (a) the workflow reads its tag + ship date from `ShipRouting/cohort.json` — passing a tag by hand
    is for ad-hoc analysis only, never for a list that reaches a sheet; (b) exactly ONE override file
    per cohort (`ice_overcap_override_<ship-date>.json`) — a second computation DIFFS against it and
    surfaces the delta, never forks a parallel file; (c) the TNT-credit question (size ice to
    effective transit vs to forecast heat regardless of TNT) is a DOCUMENTED POLICY toggle, not an
    accident of arguments — record the chosen policy in the file's header and on the Summary tab.
    ✅ Enforced 2026-07-10 (with the rule-15/16 pass): tag arg optional — default reads
    `cohort.json` (hand tag ≠ cohort tag prints an AD-HOC warning); override file ship-date-keyed
    via `override_path()` (gen_rmfg_sheet reads the same path); re-runs DIFF against the existing
    file before overwriting; file is now a dict with a `policy`/`tag`/`ship_date` header
    (`read_override()` still accepts legacy bare lists). Summary-tab policy note stays manual.

Linked from `AppyHour/CLAUDE.md`. Audit that produced this doc:
`_outputs/reports/2026-07-02-matrix-tool-audit.md`.
