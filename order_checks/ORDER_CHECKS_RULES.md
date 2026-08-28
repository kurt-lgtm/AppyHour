# Order Checks — SSOT

🔴 **PRE-CHANGE GATE.** Read this before touching anything in `order_checks/`. Every rule below
was paid for with a wrong answer on a real cohort. Change rules HERE first, in the same commit
as the code.

## 🧭 NORTH STAR

Every box that ships contains exactly what the customer is owed — no item missing, none doubled,
nothing given away. The check exists to make a short or over-packed box impossible to send, and
to do it with few enough false positives that a human reads every line of the output.

A check nobody trusts is worse than no check: 727 flags on a 2,477-order cohort gets ignored, and
the four real defects ship anyway.

## SCOPE

Count-only. `RULE SET` totals, never `LIKELY` type-mix — Kurt 2026-08-25: "I'm only going to do
checks where we expect the children to be in there." The LIKELY tab describes a DEFAULT composition,
not a rule; deviation is a customization signal, and reporting it buries the real defects (25-row
`likely.csv`: 17 rows were a stale `AHB-MED` row and CORS no-meat boxes behaving correctly).

Runs AFTER child SKUs are applied. An order with zero children is UNBUILT, not short.

## 🔴 GOTCHAS — the negatives, each with the incident that motivated it

### Counting

- **`currentQuantity`, NEVER `quantity`.** Removed lines stay on the order with `quantity` intact.
  Same script, one field: 727 flags vs 44 (`RMFG_20260821`). 3,664 removed child units sat in that
  cohort across 1,128 of 2,477 orders. The VALIDATE sheet has this bug — it produced 34 phantom
  `-1`s, and #175422 (17 removed lines) read `-5`.
- **Box rule × parent QUANTITY.** `AHB-LCUST-TRAY` q=2 expects 20 trays, not 10 (#176563).
- **A `CEX-` placeholder counts INSIDE the box's N and resolves into a child.** `AHB-MCUST-SS`
  "7 Items" = `CH-ALP CH-MAFT CH-BRZ CEX-EM MT-SPAP CEX-CR AC-DTCH` (#176088). The parent line
  SURVIVES resolution — `CEX-EC` + `CEX-EC-SS` coexisting is expected, not a duplicate.

### Price — three fields, only one is right

- **`total_discount` (line-level) is the paid signal.** A 100%-discounted line is a box-builder
  slot "Included with subscription", not an add-on: #174407 `CEX-EA` price 5.50, total_discount
  5.50 → net 0.
- **NEVER `pre_tax_price`.** It reads 0.00 on orders with no discount at all — #176576 shows 0.00
  for an undiscounted $89 box, which made a paid $14 `CH-MAFT` look free.
- **NEVER `discount_allocations`.** Those carry ORDER-level codes. #176576 is paid by an
  "AppyHour Credit" $103 fixed_amount spread across the box ($89) + `CH-MAFT` ($14) — allocated,
  but the customer paid.
- Reading gross `price` inflates the paid allowance on every SS box (allowances of 6–8 that
  should be 0) and silently forgives real over-packs: #176565 sat at OK until this was fixed.

### Parents

- **Box parent may have a NULL SKU** — 226 orders in one cohort bill `AppyHour Box + FREE…` at
  $79/$89/$99/$109 with no SKU. Resolve from `variant_title`: "Medium (Serves 2-4)" → `AHB-MED`,
  "Large (Serves 4-6)" → `AHB-LGE`. Without this they read as NO_BOX.
- **Priced null-SKU packs grant a paid allowance** — `Prosciutto (5-Pack) - 5 Items`,
  `Ultimate Add-on Package: Summer Cookout - 4 items`, and the $26
  `Curator's Choice - Extra Meat, Cheese & Accompaniment` (= 3). Their children are $0 with the
  price on the parent, so without this they read as free curation (#174712, #176023).
- **`EX-PS` "Party Size Upgrade" = 2 CH + 2 MT + 2 AC** (Kurt 2026-08-25). Absent from RULE SET;
  the only parent contributing to all three types. Add it to the rule set.
- **A REMOVED priced parent forfeits its components' allowance.** #176565: `BL-4USA` @ $28.50
  removed, its `AC-KETT`/`MT-PARM`/`AC-BLUCAR`/`CH-FAG` left live.

### Out of scope entirely

- `Reship*`, `Gift Redemption`, `PR box` (internal sample, no `AHB-` parent, #175430), cancelled.
  🔴 `Gift Redemption` is excluded from **check 3 ONLY** (docx: a gift "doesn't need to be
  on the Shopify order" for the brochure). It stays IN checks 1 and 2, and in the cracker
  check. Widening it hides a real gap: on `RMFG_20260828` the ten gift orders sitting in
  `c1_unresolved` have FULL item lists on the sheet - #175930 shows 9 items on the sheet
  against 1 child plus six unfilled CEX slots in Shopify. The pick list already knows what
  ships; the order never got it. Excluding gifts also silenced c2 to zero (Kurt 2026-08-28).
- `AHB-X*` / `BL-*` parents — the docx says these are added separately.
- **BYO = COUNT ONLY, never per-type.** Customer picks any mix (#174819: 10 cheese / 0 meat is
  legal; #176749 `AC-4 CH-4 MT-1` = 9 = correct).
- **A tray box may be all one variety.** #176563 `box_contents: 10x American Artisan Board` ×2
  boxes = 20× `TR-AAB`, correct. Never flag a `TR-` pile.

### Substitutes

- **Blocked-set = box ∪ REMOVED ∪ history ∪ this-order-adds.** A removed SKU cannot be re-added:
  Matrixify MERGE sees the `currentQuantity: 0` line, calls it already present, and skips. Five
  `_SHIP_2026-08-31` orders shipped short because the rebuild re-picked the one cracker on the
  blocked list (`AC-FCROSE`). Fix path is GraphQL `orderEditAddVariant` with a SKU never on the
  order — or `orderEditSetQuantity` to restore the existing line. See `matrixify-import-dupe-check`
  Phase D.
- **Any add resolves to the $0.00 variant.** `AC-TOK` has both $0.00 (in-box) and $5.50 (paid).
  Use the `gid_zero()` resolver in the `shopify-api` skill; ABORT if no $0 variant exists.

## CRACKER SLOT

`CEX-CR` must deliver an actual CRACKER, not merely any `AC-`. A count check cannot see
this: #176361 (9/9) and #176392 (11/11) are full and still wrong.

Eligible: `AC-FCROSE AC-FCEVOO AC-ACRISP AC-TCRISP AC-EFLAT AC-FCWALN AC-PFLAT AC-TOK`.

🔴 `AC-TOK` (Toketti) is Kurt's 2026-08-25 addition and is NOT in Dan's `CRACK` set, so his
run reports every Toketti fill as "CEX-CR slot filled with a non-cracker". Keep the two sets
in sync or the same seven orders get re-reported every week.

## BARE CEX-EC (rule 11)

`CEX-EC` (bare) + `CEX-EC-{CURATION}` on the same order is **EXPECTED**, not a duplicate: the
bare line is the placeholder written first, the suffixed line is its curation-specific
resolution. Never flag the pair.

A **bare `CEX-EC` with no `CEX-EC-*` counterpart** is the defect - the resolution never ran.
Check open unfulfilled orders; Gift Redemption is out of scope.

🔴 The fix is to add the **`CEX-EC-<CURATION>`** line qty 1 - NOT the `CH-` SKU. Adding the
cheese directly fills the count while leaving the slot unresolved, which is the same class of
wrong as filling `CEX-CR` with a non-cracker.

Found on `_SHIP_2026-08-31`: #178549 (Marilu Madariaga, `AHB-MCUST-SS`, bare `CEX-EC` qty 1,
zero `CEX-EC-*` lines incl. removed/fq=0) -> fixed with `CEX-EC-SS`. Trays carrying `CEX-EC`: 0.

## FIXED_ROUTE PIN vs CUSTOMER PROFILE

An order whose CUSTOMER profile carries `Fixed_Route` must carry the same `!..._AHB!` route
tag on the order itself. The profile is authoritative; the sheet row and the order follow it.

🔴 **The Shopify Flow only fires on `order_created`.** An order that already existed when the
pin was set never re-triggers it, so the profile reads "pinned" while the live order routes on
the default carrier and ships on exactly the lane the customer complained about.

`_SHIP_2026-08-31`: **3 of 4** pinned customers had an unpinned order - #178090 Kameron
Lewellen, #177442 Victoria Tooker, #176917 Daniel Ramirez, all `!UPS Ground - Dallas_AHB!` on
the profile with NO route tag on the order. Only #177243 Candice Angotti matched.

Also checked: a `Military` profile must never be routed OnTrac (Kurt 2026-08-13).

Fix = append the customer's pin to the order and correct the sheet row. Never overwrite the
order's other tags. Before changing a pin itself, read the customer's Gorgias ticket - ~90% of
pins exist to AVOID a carrier, usually OnTrac. See [[fixed-route-read-the-ticket]].

## PEER CHECK

Second opinion with no rule-set dependency: group by box SKU, compare each order's child count to
the group's modal count. Catches what a wrong rule forgives (#176565 15/11, 37 of 47 peers ship 11).

🔴 **Only valid on built orders.** Unscoped it returned 341 outliers on `_SHIP_2026-08-31`, 200+ of
them orders created after the sheet with zero children yet. Scope to sheet membership → 123.
Require ≥8 peers before judging a group.

## SHEET ↔ SHOPIFY

The sheet is the pick list of record; compare its per-order item total against live Shopify children.
Two real defects found this way, both sheet-side: #175526 carried BOTH the live 10 trays and the 10
removed originals (20); #174939 omitted `AC-KETT` ×2, a paid `BL-4USA` board component (15 vs 17).

## OUTPUT

Severity by signal agreement: BOTH rule+peer = highest. Never emit a bare count — a flag without
its children, parents, and paid allowance can't be triaged, and an untriageable list gets ignored.
