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
12. **Allocate sets AVAILABLE = max(0, HAVE − paid(week) − $0-in-box(week)); demand scope is the SHIP WEEK ONLY** (Kurt 2026-07-21, superseding the 2026-07-03 on_hand form). **Failure that forced the change:** the on_hand form (`on_hand = HAVE − paid`, letting Shopify derive available) assumed `committed` ≈ this week's curation demand — but committed spans **ALL open unfulfilled orders across every ship week** (CH-QOTA 2026-07-21: HAVE 229 pushed as on_hand, committed 328 from multiple cohorts → available **−99**; the SKU read unavailable while the week was fully covered). A week-scoped HAVE must never be netted against Shopify's week-agnostic committed. (a) Write **`available`** directly = max(0, HAVE − NEED) where NEED = ALL fulfillable units of the `_SHIP_<mon>` cohort (paid variant + $0 in-box). Shopify back-computes on_hand = available + committed; that inflated on_hand is accepted fiction — `available` is the number the portal/customization reads, and it's the one that must be right. Never revert to on_hand-derived available. (b) Count paid **by the priced variant's id** (`_variant_catalog_prices` catalog price > 0), NEVER by line-item `price` — paid/curation lines routinely show $0 on the line, so price-based counting is inverted (CH-GAOP read paid=1 instead of 76). (c) Use the **`_SHIP_<mon>` ship-week tag** (= cohort `ship_week`), NOT an RMFG cut sub-tag. **ALL sub-cohorts of a ship week (RMFG_<Fri>, `_b`, `_c`, a Tuesday tag…) share the one `_SHIP_<Mon>` tag**, so scoping on it subtracts EVERY sub-cohort's demand; scoping on a single RMFG sub-tag counts one batch and **over-promises inventory** (available too high). `compute_allocation` WARNS loudly if the tag isn't `_SHIP_`-prefixed. Verify post-push: $0 variant's `available` at RMFG == max(0, HAVE − NEED), with an UNCACHED read (`_shopify_graphql_matrix`); the cached path returns stale pre-push values. [[ship-date-vs-ship-week]]: ship_week is the allocate scope, distinct from ship_date (departure day).
13. **One HAVE sheet feeds BOTH the cross-check and the Shopify On Hand push** (2026-07-03): the
    web Inventory tab's loaded inventory drives `/api/allocate` → `compute_allocation` /
    `apply_allocation` (extracted from `cmd_allocate`; web routes through them per rule 9 — never
    re-implement the AVAIL$0 math in the web layer). Gotchas: **a tag matching 0 orders is
    REFUSED** (paid=0 → push would set Available to raw HAVE and let parents over-allocate into
    paid units — the exact failure allocate exists to prevent); commit requires a fresh preview and
    invalidates it after push (re-preview after swaps); PK-/MR-/TR- structural SKUs stay uncapped;
    Available push remains manual-confirm — never auto-commit (inventorySetQuantities is
    effectively last-writer-wins even with changeFromQuantity read-then-write).
    **REGRESSION (2026-07-07):** `apply_allocation` read the `changeFromQuantity` (current on_hand,
    the optimistic-lock arg) via the CACHED `_shopify_graphql` — so on any re-push of a SKU whose
    on_hand was just changed, the stale cached value no longer matched the persisted quantity and
    Shopify returned `changeFromQuantity argument no longer matches the persisted quantity` (hit live
    on CH-MONT, failed twice, only succeeded via an uncached read). Rule 12 already mandates the
    uncached path — the optimistic-lock read MUST use `_shopify_graphql_matrix`, never the cached
    `_shopify_graphql`. Do not revert this read to the cached path.
    **REGRESSION (Kurt 2026-07-29): allocate was capping TRAYS.** The structural-skip literal in
    `compute_allocation` was `("PK-", "MR-")` only, so every `TR-` SKU with a $0 variant fell through
    and got `available = max(0, HAVE − week NEED)` pushed to Shopify — **84 TR- pushes** landed in
    `_outputs/logs/inventory_alloc_audit.jsonl` through 2026-07-28 04:44 (`TR-TRUFF`=104, `TR-AAB`=72,
    `TR-APRES`=52, `TR-ICTRY`=39 …). Trays are **made-to-order from bulk** — there is no meaningful
    per-tray HAVE row, so the cap is fiction and can drive a sellable tray's available to 0 (or
    over-promise it). `TR-` is now structural alongside `PK-`/`MR-`: allocate must NEVER push a TR-
    SKU's available. Do not "complete" the prefix list by adding TR- back to the capped set — TR- is
    pickable and gets matrix COLUMNS (rule 19a), but pickable ≠ inventory-capped; those two prefix
    lists are deliberately different. Test: `tests/test_allocate_structural.py`.

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
    (`tests/test_generate_matrix.py::test_mr_jrnl_gets_column`).
    **19-bis (2026-07-28):** there was a FOURTH copy — `check_mfg_onboarding` collected demand SKUs
    from its own `("CH-","MT-","AC-","PK-")` literal, omitting TR-/MR- that the column builder
    included. A tray or journal SKU with no MFG name was therefore INVISIBLE to the onboarding gate
    while still becoming a column: the rule-19a phantom-column failure, one prefix over. The column
    builder and the gate now share ONE constant, `matrix_commander.PICKABLE_PREFIXES`; duplicating it
    as a literal is the regression (`test_mfg_gate_and_column_builder_share_one_prefix_set` asserts the
    old 4-prefix literal appears nowhere in the module). MR- carries 0 DistVol so it is never
    inventory-capped (already handled at `matrix_commander.py:3004`, `("PK-","MR-")`) and its qty rides
    into the col-D Total per rule 0.

19a. 🔴 **NEVER fabricate an MFG name — an un-onboarded SKU is an IMMEDIATE REJECT, not a warning**
    (Kurt 2026-07-28, wk0728 TUE). **Failure mode:** `AC-QUIC` ("Quicos", 1 unit, order #165739) was
    live in Shopify but absent from `mfg_translations.csv`. `generate_matrix_xlsx` emitted
    `AHB (S_REG): {SKU_TO_NAME.get(sku, sku)}` — and with no local mapping either, that resolves to the
    **BARE SKU wrapped in the real header format**: a phantom column that looks legitimate on the sheet
    but names a product RMFG has never seen and cannot pick. THREE layers failed open at once:
    (a) the column builder invented the name and only printed a yellow warning; (b) `cmd_generate`'s
    `check_mfg_onboarding` correctly returned False, printed it, then `return True`d anyway and went on
    to "Ready to email to RMFG" (rule 5 — GATE, don't report); (c) `gen_rmfg_export.run_step` swallows
    child stdout unless the step exits non-zero, so the warning never reached the log. Nothing crashed;
    Kurt caught it by eye on the finished sheet. **Rules:** only `mfg_translations.csv` (the RMFG
    translator-portal export) may name a column — never Shopify titles, never `SKU_TO_NAME`, never the
    bare SKU. Missing name → `ValueError` at column-build time, BEFORE any xlsx is written (this also
    closes rule 5's known gap where a failed run left a legit-looking file in Downloads). Never
    hand-edit a header to get past it — onboard at https://translator.robbinsmfginc.com/ and re-export.
    A `mfg_translations.csv` refresh is NOT proof of coverage: the 07-27 refresh (283 rows) ran the day
    before and still lacked AC-QUIC.
    **Two invariants the tests lock (`test_generate_matrix.py`):** (a) the reject is identified BY TYPE
    — `MfgOnboardingError` (a `ValueError`) carrying `.skus`; callers must catch the type and read
    `.skus`, never match the message string (§13.5 degrades only the SHEET stages on this — a reword
    silently reverts to failing whole jobs). (b) A **parent line** (`PR-CJAM-*`, `CEX-EC-*`, anything in
    `SKIP_PREFIXES`) is never a pickable child: it is excluded from the column set, so it can neither
    trip this reject nor render as a column — a parent can never be the SOURCE that names a child's
    column (the wrong-source class, Kurt 2026-07-28 "you filled both in from CEX-EC not PR-CJAM").

20. **Gift redemption vFGR = REPLACE, never skip-as-duplicate** (Kurt 2026-07-24, wk0727 done by
    hand — this rule automates it). **Failure mode:** gift redemption orders are UNEDITABLE in
    Shopify, so the matrix rows generated from Shopify carry stale/too-few items for them; the old
    `merge_gift_xlsx` skipped any gift OrderID already in the matrix as a "duplicate" — which kept
    exactly the stale rows the weekly `*_vFGR.xlsx` (Access_LIVE format, Downloads) exists to fix.
    Semantics, in order:
    (a) **A-suffix twin fold FIRST** (Simple Bundles "Associated Order", e.g. `164878A`): when the
        vFGR lists parent AND twin as separate rows, SUM both rows' product cells onto the parent
        OrderID and DROP the A row — the twin is FULFILLED in Shopify, never ships separately, and
        must not appear on the sheet or count in reconciles. **Double-count guard:** a vFGR can
        arrive pre-combined (parent already carries the twin's items, NO A row — wk0727 shipped this
        way): then fold NOTHING; never sum twice. That falls out of there being no A row to fold, so
        it needs no flag. An A row whose parent is absent from the vFGR = loud error, never a
        standalone row.
        🔴 **CORRECTION (Kurt 2026-07-28) — `remove` is NOT a flag.** This rule and the code both
        read the vFGR's `remove` column as a "pre-combined" marker and logged `remove=1` rows as
        such. Invented semantics: **`remove` is a placeholder MFG NAME Kurt typed into the RMFG
        translator portal for a `BL-` (bulk) SKU**, chosen so the column is obviously disposable.
        `BL-` is in `SKIP_PREFIXES` — not fulfillable, never a pick line — so `remove=1` is a
        QUANTITY of a bulk SKU and says nothing about twin folding. The flag was print-only, so no
        merge was ever wrong; but a log line asserting a false fact is how the false fact spreads.
    (a3) **Gift metadata is never read as authority.** The routing app's matrix row owns OrderID,
        recipient/name/address/contact, Tags (including duplicate routing/ice/ability tokens),
        Notes, ProductionDay, Total, and every other fixed/run field. The vFGR supplies only MFG
        item quantities. A renamed or malformed gift meta header is inert.
    (a2) **Placeholder-named gift columns are DROPPED** (`remove` / `delete` / `ignore`, exact match,
        case-insensitive): the generating app is unreliable, so the merge does not depend on that
        column merely lacking the `AHB (` product shape — it is dropped BY NAME, before the shape
        check, and its qty never reaches the sheet or the col-D Total. Onboarding that `BL-` SKU with
        a real-looking MFG name must not be able to push an unfulfillable column onto the sheet RMFG
        picks from. Match is exact — a fuzzy "contains remove" would eat a real product eventually.
    (b) **ITEM-ONLY REPLACE by OrderID:** WIPE-then-REFILL only validated MFG product cells from the
        vFGR. Every non-product cell remains byte-for-cell from the routing app, including Total;
        downstream canonical generation owns any derived totals. Gift input can never override
        order/name/address/contact/Tags/Notes/ProductionDay/run fields.
    (c) **vFGR OrderID missing from the matrix = LOUD `GiftMergeError`** (e.g. a `_HOLD` order —
        wk0727 #165505), listing the OIDs — surface for Kurt's release/drop decision, NEVER
        silently include or exclude. Release = retag into the cohort + re-run; drop = the explicit
        `--gift-drop OID[,OID]` flag (generate/finalize/weekly_flow), which excludes the row and
        prints it. No silent-append of unknown orders.
    (d) **Items map only by registered MFG name** — header-based with rule-15b normalization, never
        positional. A known gift-only MFG column may be appended once. Unknown names (including
        literal `remove`) are silently omitted. Duplicate normalized headers on either input never
        create duplicate output columns; duplicate gift quantities are summed deterministically.
    Chokepoint: `merge_gift_xlsx` — cmd_generate, cmd_finalize, and weekly_flow stage 1 all route
    through it; never fork a second gift-merge path. Post-apply, `check_routing_and_ice` /
    gen_rmfg self-QC still gate that every gift row got a routing tag.
    Tests: `tests/test_gift_replace.py` (+ updated `tests/test_gift_merge_walnut.py`).

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

21. **"We never make up MFG names" (Kurt 2026-08-02 — ABSOLUTE). A translation NAME outside the
    authoritative meal-type export is a HARD REJECT — an invented
    header must never become a vF column** (wk0803, 2026-07-31). **Failure mode:** `mfg_translations.csv`
    maps SKU → name but nothing checked the NAME was real; a hand-added row took a label off a
    screenshot ("Cheese Slice, Frumage L'Ottavio" vs RMFG's actual "Frumage LOttavio") and the
    invented header reached a SENT vF on 234 count rows — un-pickable on RMFG's floor, caught only
    by Kurt's review. **Rule:** `validate_mfg_names()` runs at matrix generate; any translation name
    not in `mfg_names_authoritative.csv` (a committed snapshot of the meal-type export) raises
    `MfgOnboardingError` (BY TYPE, §13.5 semantics — sheet blocked, routing unaffected). MFG names
    are sourced ONLY from the meal-type export, never from sheet labels, screenshots, or memory.
    Refresh the snapshot by replacing the file with a fresh export; snapshot absent = loud
    validation-skipped warning (not a hard stop on machines without it).
    ✅ Enforced 2026-07-31: `matrix_commander.validate_mfg_names` + `tests/test_mfg_name_validation.py`.

22. **Zips and tracking numbers are TEXT everywhere — leading-zero loss recurred FOUR times**
    (07-03 matrix, 07-17 second writer, 07-29 Kurt caught it in a report, 07-31 vFGR + FedEx
    tracking in auto_import). **Failure mode:** an int cast anywhere in a pipeline silently turns
    `07627` into `7627` (undeliverable NE zips) and strips tracking leading zeros (repair-matching
    breaks); each writer re-fixed it locally instead of the rule existing. **Rule:** every writer
    and reader treats zip/tracking as strings (`zfill(5)` at ingest); every xlsx export sets
    `number_format='@'` on order#/zip/tracking columns; `check_zip_leading_zeroes` runs on BOTH the
    matrix and gift (vFGR) paths. Engine-side twin: ROUTING_RULES "ZIP INTEGRITY"; consolidated
    ledger in memory `zip-integrity-family`.
    **ZIP+4 addendum (wk0817, 2026-08-14): RMFG also REJECTS zips longer than 5 digits.** 13
    gift/hand-merged rows shipped `83340-7024`-style and the sent sheet bounced. The finalize
    zip fix-pass now ALWAYS strips to zip5 (`split("-")[0]`, zfill(5), format `@`) — silent strip,
    not a hard fail (Kurt 2026-08-14 "not hard fail, just strip"). Any path that appends rows to a
    vF outside `matrix_commander` (hand-adds, sweeps) must normalize the same way at write time.

23. **The AUTHORITY FILE ITSELF is validated — adding to MFG names must not pollute the export**
    (Kurt 2026-08-09: *"we also have to take care to separate mfg names from other shit as to not
    pollute our export when we add to mfg names."*). Rule 21 guards translations **against**
    `mfg_names_authoritative.csv`. **Nothing guarded the csv.** That is the authority-registry
    meta-rule in a second place: *a guard that reads the source it is guarding validates nothing
    about that source* — a polluted row does not fail any check, it **becomes** the authority and
    every downstream name check inherits it.
    **Failure modes this closes (all silent):**
    (a) a name pasted from a screenshot / Shopify title / meal-type PDF instead of the export —
    the wk0803 `Frumage L'Ottavio` class, where a **curly apostrophe** is invisible in a csv and
    reaches a sent vF as an un-pickable header;
    (b) an extra column appended "just for reference" (notes, classification, count, source_file)
    — the loader reads `col[1]`, so a shifted row silently changes what a name IS;
    (c) the same SKU added twice with two spellings — the loader builds a dict, so the **last row
    wins** and the authority becomes row-order dependent;
    (d) pollution's mirror image — a **blind overwrite** with a fresh export that quietly DROPPED
    items.
    **Rule:** the authority file's schema is **name-columns only, exactly 2 columns
    (`SKU,name`), no header row**; every name matches the export grammar `AHB (S_REG): <name>`;
    SKU and name are both unique; SKU prefixes come from the product-rules taxonomy (a new prefix
    is an onboarding decision, never a validator default); no smart punctuation (curly quotes /
    en-dash / NBSP = pasted, not exported). Anything else belongs in a **separate file**.
    Additions still come **only** from a fresh meal-type export (rule 21) — but are reviewed as an
    **ADD/DROP/RENAME delta**, never a blind file swap. **A rename is not automatically safe: it
    changes a vF header RMFG may already be picking from.**
    ✅ Machine-checked 2026-08-09: `scripts/utilities/validate_mfg_authority.py` (validates the
    file; `--diff-against <fresh_export.csv>` prints the delta and refuses to promote an export
    that is itself polluted). Verified against the live 285-row file: 2 columns throughout, all
    285 names on the `AHB (S_REG):` grammar, zero duplicate SKUs or names.
    🔴 **OPEN — both name checks currently fail OPEN.** `matrix_commander.py:401-404` and
    `scripts/utilities/validate_vf_sheet.py:85` both print a warning and **skip validation** when
    the authority is missing/empty. Rule 21 blessed that ("not a hard stop on machines without
    it"), but for a **submission-bound** run it is the silent-degrade class: an empty authority is
    *more* dangerous than a missing one, because "0 names checked" is indistinguishable from "all
    names valid". Hooking rule 23 into the generate path and closing the fail-open both land in
    `matrix_commander.py`, which is another session's claimed surface — routed, not silently
    edited.

24. **ITEM-MATRIX editing on a BUILT vF is a validated, ledgered TOOL — never Excel**
    (`AppyHour/scripts/vf_items.py`; the item-side sibling of `ShipRouting/scripts/vf_tags.py`,
    which owns col L). Rules 19a/21/23 guard names at GENERATE time; **nothing guarded the edits
    Kurt makes to the sheet afterwards**, and every burn below happened on that unguarded surface.
    The submitted vF is THE AUTHORITY (RMFG picks from the sheet, not Shopify), so a post-generate
    hand-edit lands on the floor with no gate in front of it.
    **Failure modes this closes — each one shipped:**
    (a) 🔴 **Invented MFG name** (2026-08-04): an agent derived *"Farmstead Smoked Cumin Gouda"*
        from a **Shopify product TITLE** while `mfg_names_authoritative.csv` said *"Farmstead Cumin
        Gouda"* — immediately after Kurt asked to check the names. Same class as the wk0803
        `Frumage L'Ottavio` header that reached a SENT vF on 234 rows. **Rule:** every header this
        tool writes — added, renamed, or produced by a find-and-replace — is the **verbatim** name
        from `mfg_names_authoritative.csv`. Never a Shopify title, never `SKU_TO_NAME`, never the
        bare SKU, never a "close enough" spelling. A rename cannot smuggle in a non-authoritative
        name: the RESULT is re-validated, not just the input. Unknown SKU/name → **refuse and print
        MISSING**, never guess. Reuses rule 21's `validate_mfg_names` — never reimplemented.
    (a2) **Rule 23's fail-open is closed HERE.** `matrix_commander.py:401` and
        `validate_vf_sheet.py:85` warn-and-skip when the authority is missing/empty — for a
        submission-bound EDIT that is the silent-degrade class ("0 names checked" is
        indistinguishable from "all names valid"). `vf_items` **fails closed**: an empty/missing
        authority aborts. `--allow-missing-authority` exists for a machine without the snapshot and
        is recorded in the ledger on every entry it touches.
    (b) 🔴 **Malformed header shape** (wk0713): `Walnut, Honey & Extra Virgin Olive Oil Crackers`
        (Shopify's comma form) vs the translator's `Walnut Honey & …` — **545 units** would have
        failed RMFG's import mapping. **Rule:** shape is checked BEFORE membership so the message
        names the defect: `AHB (S_REG): ` prefix, no comma, no tab/newline, no doubled or edge
        whitespace, no smart punctuation (curly quotes / en-dash / NBSP = pasted, not exported —
        rule 23a). Rule 15b's trailing-dot pairs (`Prairie Breeze.` ≠ `Prairie Breeze`) are
        DIFFERENT products — the tool never normalizes punctuation to make a header pass, it
        refuses.
    (c) 🔴 **A swap must never put the same item twice in one order.** wk0810's real ask —
        `CH-TETI → CH-ETX or CH-WWHO or CH-CARO, avoid duplicates` — is the shape of every swath
        swap. A duplicated line item is a Matrixify import failure and a customer-visible
        double-cheese box (the whole `matrixify-dupe-split` / `matrixify-import-dupe-check`
        skill fallout). **Rule:** before writing, the tool checks the TARGET column on every
        targeted row; any row already carrying that item **refuses the whole write and names the
        rows/orders**. With multiple `--to` targets it assigns the least-used target that is FREE
        on that row (deterministic, spreads demand); a row where **no** target is free is named
        and refuses — it is never silently skipped and never doubled up. Duplicate = two different
        columns resolving to the same SKU on one row, not merely a repeated header.
    (d) 🔴 **Never overwrite a sent/dated file** ([[never-delete-prior-output-files]]) — `--write`
        emits `<stem>_r2.xlsx`, `_r3.xlsx`, and refuses a collision. The input is opened
        `data_only=False` so formulas round-trip as formulas.
    (e) 🔴 **Audit the artifact that LEFT, not the fix you queued** (wk0713, 545 units). Every
        write is followed by a **semantic preservation verify** — sheet names, row count, per-row
        `OrderID`, and every `(row, header)` cell compared against the source; only the cells the
        plan declared may differ. Structural ops are verified the same way by HEADER NAME, so an
        add/drop/rename cannot silently shift a neighbouring column's values. Any discrepancy →
        **the output file is deleted** and the run fails. Nothing is trusted because it was queued.
    (f) **Col-D `Total` is RECOMPUTED, never carried** (rule 0) on any row whose product cells
        changed; `Tags` (col L), `ProductionDay`, and every meta cell are byte-preserved. An item
        edit touches items — the mirror of vf_tags §7.4 (a routing edit touches routing only).
    (g) **Gift rows go through `merge_gift_xlsx`, never a second path** (rule 20): ONE vFGR per
        sub-cohort, REPLACE-by-OrderID (wipe-then-refill, not additive), quantities exactly as the
        vFGR states, `GiftMergeError` on an OID missing from the matrix. `vf_items gift` is a
        dry-run-default, revisioned, ledgered wrapper around that chokepoint — it copies the source
        first and never lets the merge write next to a sent file.
    (h) **Selection may not be inferred.** Curation is NOT on the sheet (the box SKU never lands
        there), so `--curation` does not exist — pass an order list. Deriving a curation from a
        row would be the fabrication class one level up.
    (i) **DRY-RUN BY DEFAULT; a REFUSED op writes NOTHING — no file, no ledger line.** Every
        applied change is recorded exactly once in `_outputs/cache/vf_item_edits_<stem>.jsonl`
        with before/after/reason; `revert` reads only that ledger and never guesses a prior value
        (a structural op stores the dropped column's cells so its revert is exact).
    `validate` is a first-class read-only command (mirrors vf_tags §7.9): it audits every product
    header against the authority, the header shape, duplicate columns, per-row duplicate SKUs, and
    `Total` drift. **Not enforced here:** routing/ice tags (vf_tags + `check_routing_and_ice` own
    col L), MFG *onboarding* at the translator portal, and whether a SKU has a $0 in-box variant.
    Tests: `tests/test_vf_items.py`.

25. **EVERY order carries EXACTLY ONE tasting guide, and WHICH one is a POLICY TABLE — never an
    `if`** (Kurt 2026-08-09, standing rule; verbatim: *"all orders must have a tasting guide. if its
    got a TR- tray sku in it, it gets pkbitesguide. if its got regular shit, its PK-TCUST"*).

    🔴 **The failure first.** A row with **no** guide column set ships a box with **no tasting
    guide** — the customer gets cheese and no idea what it is, and RMFG has nothing to pick because
    the sheet never asked for one. A row with the **wrong** guide is worse than none: RMFG picks a
    guide that does not describe the box (a tray order handed the Custom Box guide names products
    that are not in it). A row with **two** guides is a double insert and a printed-stock overrun.
    None of these fail loudly anywhere today — a missing guide column looks exactly like a blank
    cell. On the submitted `08-10-26_vF` this was **29 orders with no guide and 1 mismatched out of
    2,253** (measured 2026-08-09, read-only).

    (a) **The selection table (fully resolved — Kurt answered both open cases 2026-08-09).** Ordered
        predicates, first match wins; the table is DATA (`vf_items.GUIDE_POLICY`), so a future change
        is an edit to a table or a `--guide-policy` json, never a rewritten branch:

        | # | when | guide | status |
        |---|------|-------|--------|
        | 1 | order carries any `TR-` SKU (tray) | `PK-BITESGUIDE` | active |
        | 2 | otherwise | `PK-TCUST` | active |
        | — | `PK-FCUST` "Tasting Guide - The First AppyHour" | never selected | 🔴 **RETIRED** |
        | — | `PK-TMDT` "Tasting Guide - Mediterranean Escape" | never selected by default | 🟡 **PARKED** |

        **A MIXED order (tray AND regular items) is a TRAY order** — bites guide ONLY, never both
        (Kurt 2026-08-09: *"TR- tray gets bitesguide"*). Tray presence decides; item mix does not.
    (b) 🔴 **RETIRED ≠ DELETED, PARKED ≠ GONE** — the [[max-ice-directive-is-seasonal]] / Veho /
        Indy-cap shape. `PK-FCUST` is retired: the table never selects it, but the validator still
        RECOGNIZES it and reports "retired guide present" so an existing sheet carrying it is
        *identified and corrected*, never silently accepted and never silently stripped. `PK-TMDT`
        is a parked backup (Kurt: *"the TMDT is a weird backup we may use again"*): never selected
        by default, reported as **INFO** (not an error) when a sheet carries it — that means someone
        used it deliberately — and re-enabled **by config** (`--guide-policy`), not by a code edit.
        Deleting either entry from the table would erase the only record of *why* they are inert.
    (c) 🔴 **This is a SHEET rule. Shopify line-item state is NOT authoritative for it and a
        mismatch is EXPECTED, not a defect** (Kurt 2026-08-09: *"its really not the biggest thing
        in the world if that is not consistent with the shopify write"*). RMFG picks from the
        submitted vF, so the sheet is where the guide must be correct. **Never** build a
        sheet-vs-Shopify guide reconciliation, a divergence check, or a Shopify-side backfill for
        guides — that is the "helpfully close the gap" move this clause exists to stop. Same posture
        as **ice** (`VF_SHEET_RULES` §5b / `ROUTING_RULES` §18.6: tags are a SET, quantity is
        inexpressible, the SHEET wins) and **routing tags** (a historical record consumed days
        later). Three surfaces, ONE doctrine: *the sheet is the artifact; Shopify is not its mirror.*
    (d) 🔴 **A guide header is written ONLY from the MFG authority** — `vf_items` resolves
        `PK-BITESGUIDE`/`PK-TCUST` through `Authority.header_for_sku` and the write is re-gated by
        rule 21's `validate_mfg_names` (rule 24a). A guide SKU absent from
        `mfg_names_authoritative.csv` → **REFUSE and print MISSING**; never invent
        `AHB (S_REG): Tasting Guide - <something>`. That is the 2026-08-04 Cumin class applied to
        inserts.
    (e) **Guides are `PK-` inserts: DistVol 0.** Adding one **cannot** change box sizing, ice
        quantity, gel packs, or routing (`box_simulation.PREFIX_DEFAULTS` PK = 0). Remediation of a
        guide is therefore safe to run AFTER routing/ice have been applied — it moves no thermal or
        carrier decision. It DOES change col-D `Total` (rule 0: Total = ALL product columns), which
        is recomputed, and it DOES add a pickable line to RMFG's workload.
    (f) 🔴 **Reships are the high-incidence class** (Kurt 2026-08-09: *"its usually reships that need
        hand editing"*). CS creates reships outside the curation pipeline, so they arrive on the
        sheet missing or mis-guided far more often than regular rows. Reships are identified by TAG
        via the canonical `ShipRouting/lib/qc_gate.is_reship_tag` (both live formats: `Reship -
        <reason>` AND nested `Shipping::<reason>::<x>`) — **never a hand-rolled substring match**,
        which misses the nested form. `vf_items` fails loudly if that import is unavailable rather
        than degrading to a guess. Every guide count is reported **split reship vs regular**; a flat
        total hides the concentration.
    (g) **Sheet-time remediation is a PATCH, not the fix.** `vf_items guides --write` closes the gap
        on a built sheet; the guide should be present **by construction** upstream (generator +
        reship intake, §"upstream" below). Patching a sheet every week is the manual-intervention
        the north star says to remove.
    (h) **Duplicates are NEVER auto-resolved.** Two guides on one row is refused-and-named: choosing
        which to delete is a judgment (one of them may be a deliberate parked `PK-TMDT`), and
        auto-deleting a guide someone added is data loss. Same for a guide with qty ≠ 1.
    (i) **Retired/parked guides are never auto-replaced.** A row carrying `PK-FCUST`/`PK-TMDT` is
        reported, and `--fix-mismatch` skips it by name — correcting it requires an explicit human
        decision, because "retired" is a policy statement, not a defect classification.
    Command: `vf_items guides <sheet> [--fix-mismatch] [--guide-policy p.json] [--write]` — dry-run
    by default, `_rN.xlsx` revisions, ledgered, preservation-verified, exactly like every rule-24 op.
    `validate` reports guide coverage read-only. **Not enforced here:** anything Shopify-side (c),
    and whether RMFG has enough guides PRINTED (that is a cut-order concern — the weekly demand for
    `PK-BITESGUIDE`/`PK-TCUST` must be counted, `InventoryReorder/build_cut_order_xlsx_v2.py`).
    Tests: `tests/test_vf_items.py`.

Linked from `AppyHour/CLAUDE.md`. Audit that produced this doc:
`_outputs/reports/2026-07-02-matrix-tool-audit.md`.
