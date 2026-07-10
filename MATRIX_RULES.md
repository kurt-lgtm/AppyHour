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
12. **Allocate sets ON_HAND, and paid demand is counted by the DISTINCT PAID VARIANT** (Kurt 2026-07-03, three-bug fix). Each cheese SKU is TWO products sharing the SKU: a $0 in-box variant (the item we cap) and a priced add-on product. (a) Write **`on_hand` = HAVE − paid**, NEVER `available` — setting available makes Shopify back-compute `on_hand = available + committed` so the paid subtraction is lost; setting on_hand lets Shopify derive `available = on_hand − committed` and self-maintain as curation commits. (b) Count paid **by the priced variant's id** (`_variant_catalog_prices` catalog price > 0), NEVER by line-item `price` — paid/curation lines routinely show $0 on the line, so price-based counting is inverted (CH-GAOP read paid=1 instead of 76). (c) Use the **`_SHIP_<mon>` ship tag** for the cohort, not the RMFG cut tag. Equivalent check: $0 `available` should equal `HAVE − all-committed-both-products-in-tag`. Verify with an UNCACHED read (`_shopify_graphql_matrix`); the cached path returns stale pre-push values.
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
