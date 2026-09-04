# RESOLVE_DUPES_RULES.md — Matrixify resolver role (single source of truth)

> 🔴 **PRE-CHANGE GATE:** read this BEFORE touching `scripts/utilities/resolve_matrix_dupes.py`,
> `scripts/utilities/order_state_cache.py`, `scripts/resolve_import_dupes.py`, or running ANY
> Matrixify resolve pass. Change rules HERE first — same commit as the code. Negatives-first:
> every rule below encodes a failure that shipped or nearly shipped.
> Process doc: `~/.claude/skills/matrixify-import-dupe-check/SKILL.md`. Rewritten 2026-09-04
> from the full session transcripts (`_outputs/reports/` audit pending) — supersedes the
> 2026-08 version, whose rule 7 ("AC- dupes are dropped") is now WRONG (see rule 1).

## 🧭 North Star (Kurt, verbatim, 2026-09-04)

> **"when i give you a matrixify export file, you look for dupes in order, dupes in the sheet,
> look for removed items on the orders so I don't silent error when i try to add the same thing
> back."**
>
> *"what you also should be doing next time — look for any child skus i'm about to add through
> matrixify that were removed on an order … those need to be switched."*

What it is: Kurt hands over a Matrixify "add line item" export (one row per order + child_sku,
MERGE-adding free $0 box contents to Shopify orders). The resolver returns ONE corrected CSV that
imports clean on the first pass and shorts no box. Output is a file; Shopify is never edited by
the resolver. Changing this north star is a Kurt decision, never an agent edit.

## The mandatory checks — every sheet, every row, every time

Run ALL of these on every export before it is called resolved. Skipping one is how a short box or
a silent fail ships.

| # | Check | Detect | Fix (in the sheet) |
|---|-------|--------|--------------------|
| 1 | **In-sheet dupe** | same `child_sku` twice in one order | swap the 2nd+ occurrence — NEVER drop (rule 1) |
| 2 | **Live dupe** | `child_sku` already on the order, `currentQuantity>0` | swap (rule 2) |
| 3 | **Removed-line class** | `child_sku` on the order with `quantity>0 AND currentQuantity==0` | switch it — a MERGE re-add silently fails (rule 3) |
| 4 | **Inventory** | Shopify LIVE `inventoryQuantity` of the $0 variant minus the sheet's adds | report `<30`; enforce SKU floors/caps (rule 6) |
| 5 | **Slot category** | substitute must match the row's `parent_sku` slot | cracker/accompaniment/meat/cheese pool (rule 5) |
| 6 | **$0 + unique** | every target = $0 in-box variant resolving to exactly ONE product | else `NO-SUB`, never invent (rule 4) |
| 7 | **Not-landed scan** (post-import only) | sheet row's SKU not live on the order now | list + re-add sheet, or live edit on explicit go (rule 8) |

## 🔴 Rules (negatives-first)

1. **Never drop a row. A duplicate must be SWAPPED to another child_sku.** Kurt 2026-09-04:
   *"duplicate rows must be resolved. if there's a duplicate, it must have another child sku, we
   don't delete rows."* Burns: a duped jam row was dropped → *"all orders need a cheese and jam for
   PR-CJAM"*; an in-sheet MT-CAPO×2 was collapsed → LGE box shipped a meat short (#177359 …
   2026-08-28). Row count per order is preserved. This supersedes the old "AC- dupes are
   dropped" call. Only the 2nd+ occurrence is swapped — swapping every matching row creates a
   NEW dupe of the replacement.

2. **Check live Shopify for dupes, sheet-wide, on fresh state.** Presence = GraphQL
   `currentQuantity>0` — never raw `quantity` (refunded lines → 43 false dupes vs 3 real), never
   `fulfillableQuantity` (fulfilled → missed dupes). Burn: a **stale order-state cache** reused a
   box read from an earlier sheet → *"you missed some in order dupes … check your code and fix"*
   (2026-08-28). `order_state_cache` `box`/`removed` are MUTABLE — re-pull them every run
   (`--fresh`); only `ever`/`last` history is safe to reuse. Kurt: *"we try to avoid dupes in the
   order in the end."*

3. **Removed-line class: a removed line cannot be re-added by MERGE — Matrixify reports success
   and adds nothing (silent fail). Switch it in the sheet.** Removed = `quantity>0 AND
   currentQuantity==0`. Kurt: *"if an order has had FCROSE removed in shopify, we can't add it
   back via matrixify"* · *"they're silent fails"* · *"if its removed line class, just swap whats
   in the sheet first."* Applies to EVERY SKU, not just AC-FCROSE (the FCROSE-only special case was
   the code gap closed 2026-09-04 — `REMOVED` reason in the swap log). Known sub: AC-FCROSE removed → AC-TOK **$0 variant `51720027996440`**
   (product `10108946120984`, handle `toketti`) — not the $5.50 variant. Burn 2026-09-04: six
   CEX-CR orders shipped with the cracker slot empty and had to be repaired by live order edit
   after the sheet was sent; #178839 was a gift redemption and could not be repaired at all.
   Note: a row can also fail with **no line at all** on the order — the removed-set signature
   alone misses that; the post-import not-landed scan (rule 8) catches it.

4. **Never fabricate a SKU identity, a category, or a taxonomy.** Every swap target is verified
   against Shopify: `price == 0`, all $0 variants share ONE product id (else Matrixify fails
   "Found more than 1 of ShopifyAPI::Product with handle"), in stock. Two-variant traps: AC-TOK
   ($5.50/$0), MT-SFEN ($9/$0), CH-LOU, CH-PVEC. No candidate clears → `NO-SUB` in the log,
   never an invented one. **A SKU's category comes from Kurt or `product-rules`, never from its
   title, its prefix, or the slot label.** Burn 2026-09-04: assistant declared AC-RMC (Organic
   Roasted Maple Glazed Cashews) a "cracker," built a "cracker-only pool," mass-swapped 30 rows —
   *"WHO TOLD YOU THAT" · "AC-RMC IS NOT A CRACKER" · "you made it up."* Same class as the
   invented MFG name (2026-08-04). When the rule isn't in hand: STOP and ASK.
   See [[never-fabricate-lookup-or-ask]], [[cex-cr-slot-is-crackers]].

5. **The row's `parent_sku` IS the slot Kurt is filling — the substitute must match the slot,
   not the SKU prefix.** Kurt: *"matrixify gets parent skus, and i have a matrix assigned to them.
   if its EX-EA it calls for an accompaniment i assign. if its CEX-CR, then i'm trying to add a
   CRACKER."* `AC-` means ACCOMPANIMENT (spans crackers, nuts, jams, etc.) — a prefix-only AC→AC
   pool is WRONG for a cracker slot.
   - **CEX-CR → cracker.** Cracker set (Kurt, 2026-09-04): `AC-FCROSE, AC-TOK, AC-FCFIGO,
     AC-FCWALN, AC-FCEVOO, AC-PFLAT`. Nothing else. ⚠️ Dan's SKU-CHECK `CRACK` set
     (`AC-FCROSE, AC-FCEVOO, AC-ACRISP, AC-TCRISP, AC-EFLAT, AC-FCWALN, AC-PFLAT`) does NOT
     include AC-TOK — a CEX-CR filled with AC-TOK comes back flagged "non-cracker." Kurt's
     instruction overrides (*"Toketti is the cracker in use"*), but expect the flag.
   - **EX-EA / CEX-EA → accompaniment** (AC-RMC, AC-QUIC, AC-SDF, AC-DTCH, AC-MARC … belong here).
   - **EX-EM / CEX-EM → meat.  EX-EC / CEX-EC(-suffix) → cheese.**
   - PR-CJAM-* → cheese + jam, both required; swap the duped half, keep the pair coherent
     (SOT→AC-MFJ, MONT→AC-SCJ). See [[prcjam-pairing-needs-cheese-and-jam]].
   Burn: prefix pool put AC-QUIC/AC-SDF/AC-DTCH into CEX-CR; the source itself carried 30 AC-RMC
   under CEX-CR and 26 of them went out live in the cracker slot (2026-09-04).
   - **Parent is not a slot SKU** (e.g. a box `AHB-…` parent): a `CH-`/`MT-` child falls back to
     cheese/meat (those prefixes are unambiguous per `product-rules`); an **`AC-` child is NOT
     categorised** — it logs `NO-SUB:UNKNOWN-SLOT` and the row is left for Kurt. Never guess.
   - Dietary box in the order (`NN`/`CO`/`NC` + `RS|FS`): NC → cracker slot, CO → meat slot, NN →
     accompaniment slot (nuts live there) are `NEEDS-DIETARY-REVIEW`, never auto-picked.
   - Pools are slot-keyed in `resolve_matrix_dupes.POOLS` (gap closed 2026-09-04; barred members
     are stripped at load, and `tests/test_resolve_matrix_dupes.py` pins the cracker set).

6. **Inventory is Shopify LIVE, not the processed on-hand CSV.** Kurt: *"you have to look at
   inventory from what's available on shopify … because inventory upload has to calculate what's
   already assigned."* The on-hand CSV is a hint; Kurt's number wins over both (*"i only have 24
   brie available actually"* after live read 25). **HAVE ≠ available** — the on-hand file is not
   committed-adjusted: *"just know it doesn't mean available. I'm sure a number of them have been
   committed. also I don't want to go out of stock on anything"* (2026-09-04). Never plan a
   substitute down to zero.
   - Report every SKU the sheet would push **below 30** available (after adds), before resolving.
   - Substitutes are drawn stock-aware: a pool SKU whose (live − already drawn this run) ≤ 30 is
     skipped. **Spread across the pool** (round-robin) — never dump overflow onto one SKU (18 Brie
     overflow all landed on CH-CARO; corrected).
   - Standing floors/caps (Kurt): **CH-OGK never to 0 and never below 30 → force-swap OGK adds
     out; never pick it.** CH-CCC = 0 stock → swap all out. CH-SHADOW floor 10 (relaxed from 30).
     CH-QOTA may go to 0. A capped SKU (Brie 24 → 30) keeps the first N rows, overflow swapped.
   - Kurt naming SKUs is usually a **suggestion, not a restriction** — ask before narrowing a
     pool (*"i wasn't restricting it … just making suggestions"*).

7. **Banned / vetoed substitutes — regardless of stock.** Anything containing **`-FS-` is
   BANNED** (*"anything with FS is BANNED"* — 29 MT-FS-JAMS came from the resolver's own pool, 0
   in the source). **MT-HOTP vetoed.** Standing barred list (Kurt 2026-08-28, cross-session
   brief): **AC-RMC** (*"I have 600, but don't use it"*), **MT-IBRES, MT-BSS, CH-MAFT** (*"we
   don't give them MAFT"*), **AC-RBOL, AC-BLUCAR, all mini jams (AC-GBEF/AC-SCJ/AC-SRHUB/AC-MFJ)
   as generic subs, all brie** — and *"a cracker only ever swaps for a cracker."* **MT-CCCS is
   the first-priority meat sub** (always tried first, no rotation). BL-FSJ / BL-FFJ rows are
   removed from the sheet, not resolved. Dietary boxes (`NN`/`CO`/`NC` fragments — six variants,
   match the letters not `RS`) → the restricted category is off-limits; never guess an exclusion
   list — flag `NEEDS-DIETARY-REVIEW`. PR-CJAM legality is **config, not inference**: only the
   Kurt/Tommy-specified cheese+jam combos are legal; the legacy config (MS = CH-SOT/AC-MFJ,
   MONT/AC-SCJ) is the authority over the unified one.

8. **An already-imported sheet must never be re-resolved.** Post-import, every add reads as a
   live dupe (13 → **1007** "dupes" on the same file) and the pass produces garbage. Before ANY
   re-run: confirm with Kurt whether the sheet was imported. After import, the only valid work is
   the **not-landed scan** (sheet rows whose SKU is not live on the order now) → a separate
   re-add sheet, or a live order edit on explicit go. The Import_Result zip does NOT show these
   (silent fail); only live state does.

9. **Never overwrite a known-good output.** The contaminated re-run in rule 8 clobbered the sent
   Brie-30 sheet and, later, the sent file-(4) sheet — the artifact-of-record was lost and had
   to be re-derived from live Shopify. Version alongside (`_RESOLVED-2.csv`), quarantine a bad
   file by renaming it `…_CONTAMINATED-…DO-NOT-USE`, and log every per-row swap so a sent sheet
   can be reconstructed. Output goes to `_outputs/artifacts/`, named as Kurt asked (one clean +
   one fully-resolved when asked; the whole order's rows when regenerating; always the full
   absolute path in the reply). UTF-8-sig; never print non-ASCII to Windows stdout.

10. **The resolver never edits Shopify. A live order edit happens only on Kurt's explicit go, per
    stated plan.** Kurt 2026-08-28: *"I DON'T WANT YOU TO MODIFY THE ORDERS / I DIDN'T TELL YOU
    TO / I FIXED THEM MYSELF. YOU JUST FIX YOUR SCRIPTS."* Live-edit protocol: restate the exact
    orders + SKU + $0 variant id + count → wait for a literal go → run → report attempted vs
    committed. **"WAIT" / "NO" / "STOP" / "ITS TOO LATE" = hard stop mid-batch**; report the
    exact committed set, touch nothing else. Gift-redemption orders are hard-locked (*"you can't
    do 178839 even if you wanted"*) — expect 5–10 %, never retry, report the skip. "Hold on to
    it" means hold: do not resolve until told.

11. **A swap-storm on one order (≥5 substitutions) means the box was already processed — surface
    it, don't mass-swap.** #175884 (2026-09-04): *"remove 175884. good catch. it was already
    processed before."*

12. **Answer the question before acting.** *"did you check for the CEX-CR removed class issue?"*
    → the right reply was "no." Launching a re-run instead is what triggered rules 8–9.

13. **Kurt's sheet is the authority; resolve only what's broken.** (prior sessions, 8/28–9/04)
    - A hand-edited export Kurt re-uploads is *"the sheet authority"* — use it verbatim, never
      re-derive it. A row Kurt fixed himself is NOT re-solved (*"if its just a sku issue, i can fix
      it but it shouldn't re solve"*); when he says drop a bad row for his re-upload, drop it.
    - *"the point of my telling you was to not go overboard on the swaps"* — never expand scope
      from one instruction; only REAL exceptions in the report (nested CEX-EM/EA, paid EX-, zeroed
      lines are not exceptions — *"WHY ARE YOU SHOWING ME THOSE"*).
    - Never swap a SKU out from a customer who already had it in their order (AC-BLUCAR, CH-SOT
      protect); reconcile against Kurt's own manual assignments — revert, don't re-resolve.
    - **A failed leg means no swap at all** — never ship a partial two-leg swap (*"if any one are
      failed, that means we don't swap"*). Verify state AFTER an edit, not the tool response
      (`orders()` is eventually consistent).
    - Multi-box orders (`AHB-MCUST*` ×2): one item per box is NOT a dupe — collapse to one row
      with summed `Line: Quantity`, never drop (dropping under-fills box 2).
    - Friday submit windows are ~25 min: when Kurt says execute or stop, stop analyzing.

Sources: current session `C--Users-Work-Claude-Projects-AppyHour/1ae704c3-….jsonl`; prior threads
`C--Users-Work-Claude-Projects/dab31900-….jsonl` (8/21–9/04), `b9ccce3f-….jsonl` (7/31–8/28),
`4f4dd896-….jsonl` (8/25), `79b5ed87-….jsonl` (PR-CJAM config), `01be8fb8-….jsonl` (barred list).

## Contract

Canonical resolver (READ-ONLY vs Shopify; writes one CSV + one swap log):

```
python scripts/utilities/resolve_matrix_dupes.py --src <export.csv> --out <…_RESOLVED.csv> [--fresh]
    [--floor CH-OGK=30 --floor CH-SHADOW=10] [--cap CH-BRIE=24] [--zero CH-CCC]
    [--min-avail 30] [--storm 5] [--imported-threshold 0.5] [--cjam-pair CH-SOT=AC-MFJ]
```

| Flag | Rule | Behaviour |
|------|------|-----------|
| `--fresh` | 2 | re-pull mutable `box`/`removed` for every order (default reuses the cache — a stale cache hid dupes) |
| `--floor SKU=N` | 6 | keep only enough source adds that live − kept ≥ N; overflow force-swapped (`FLOOR`); SKU never picked as a sub |
| `--cap SKU=N` / `--zero SKU` | 6 | keep the first N add-rows / none; overflow swapped (`CAP` / `ZERO`); never picked |
| `--min-avail N` (30) | 6 | a pool SKU with live − drawn-this-run ≤ N is skipped; the pre-resolve report lists every SKU the sheet pushes below N |
| `--storm N` (5) | 11 | an order with ≥ N substitutions is left untouched, flagged `PROBABLE-ALREADY-PROCESSED`, its draws rolled back |
| `--imported-threshold F` (0.5) | 8 | if > F of add-rows are live dupes → **exit 2**, no file written: "sheet appears already imported -- run the not-landed scan instead" |
| `--cjam-pair CH-X=AC-Y` | 5/7 | override the legal PR-CJAM pairs (default = legacy config CH-SOT/AC-MFJ, CH-MONT/AC-SCJ) |

Outputs (rule 9): `--out` is never overwritten — an existing file versions to `-2`, `-3`, …;
`<out>_SWAPLOG.txt` is written on EVERY run (tab-separated `order orig new reason slot`, flags at
the bottom, ASCII only). Swap targets carry the $0 product's **handle** (not its title) in
`Line: Product Handle` — Matrixify resolves by handle and the old code wrote the title. Stock =
`inventoryQuantity` summed over the SKU's $0 variants, which must all share ONE product (rule 4).
`fetch_prod`/`fetch_state` are the only Shopify reads; both are monkeypatched in tests.

Order state cache: `scripts/utilities/order_state_cache.py` (`_outputs/cache/matrix_order_state.db`;
`--cache-db` points elsewhere). Phase-A detector: `scripts/utilities/check_import_dupes.py`. Older
CLI `scripts/resolve_import_dupes.py` is governed by the same rules but does NOT implement rules
3/5/6/8/11 — use the canonical resolver. The dated `resolve_dupes_2026_*.py` /
`apply_ch_mt_replacements.py` one-shots are retired — never copy them; extend the canonical tool.

Tests: `tests/test_resolve_matrix_dupes.py` (one block per rule, fixture CSVs in
`tests/fixtures/resolve_dupes/`, `gql` patched to raise — no live Shopify). Code gaps listed here
on 2026-09-04 (slot pools, removed-line for every SKU, barred list, live stock-aware draw + floors,
swap log, post-import guard, swap storm) were closed the same day.

Linked from `AppyHour/CLAUDE.md` (Map). Memories: [[matrixify-removed-line-precheck]],
[[cex-cr-slot-is-crackers]], [[prcjam-pairing-needs-cheese-and-jam]],
[[never-fabricate-lookup-or-ask]].
