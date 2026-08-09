# vF / Access_LIVE Production Sheet — Structure, Names, PO Boxes, Syntax (SSOT)

🔴 **PRE-CHANGE GATE.** Single source of truth for the weekly production upload sheet
(`AHB_WeeklyProductionQuery_*_vF.xlsx`, tab `Access_LIVE`). **Every rule here is extracted from
the authority that already enforces it — do NOT invent or "close-enough" any value:**

- **Sheet QC / syntax authority:** Kori — `AppyHour/GelPackCalculator/kori/gel_pack_webview.py`
  QC pass (`qc_check_sheet`, ~lines 2240-2646). This doc is a readable extract of that code; the
  code wins on any conflict.
- **MFG / product names authority:** `AppyHour/mfg_names_authoritative.csv` (the meal-type export),
  code-guarded by `matrix_commander.validate_mfg_names` (MATRIX_RULES rule 21).
- **Lanes / routing-tag & serviceability authority:** `ShipRouting/ROUTING_RULES.md` + coverage
  CSVs (`ShipRouting/lib/zip_loaders.py`); resolved by `lib/engine.serviceability_gate`.

Related: [[mfg-names-canonical-source]] · [[xlsx-live-edit-discipline]] · [[never-fabricate-lookup-or-ask]].
When editing the sheet by hand, map cells by **header name, never column index** (indices shift on
Excel re-save), and re-read the layout after any lock/save.

---

## 1. Sheet structure

- **Tab name MUST be `Access_LIVE`** (QC "Tab Name" check, ~2548). Any other name fails.
- **Header row = row 1; data from row 2.** Orders keyed by `OrderID` (col A).
- **Fixed left columns, in order (A-N):** `OrderID, Name, Distribution Type, Total, Phone Number,
  Email, Address, Address 2, City, State, Zip, Tags, Notes, ProductionDay`.
  - **`ProductionDay` header is at Col N** (QC "ProductionDay Header (Col N)", ~2556). Do not move it.
- **Product columns (O onward):** one column per SKU, header = **`AHB (S_REG): <MFG name>`**. A cell
  holds the per-order quantity (`1`, blank/`0`/`None` = not in box). Values counted with the
  blank/`0`/`None` guard (~2345).
- **OrderIDs MUST be sorted ascending** (QC "Sort Order", ~2601) and **unique** — a repeated OrderID
  fails "Duplicate Orders" (~2595).

## 2. Names (columns)

- Every `AHB (S_REG): <name>` header MUST match a name in `mfg_names_authoritative.csv` exactly.
  **Never derive a name from a Shopify product title** (that fabrication reached a sent vF on 234
  rows once, and again 2026-08-04 as "Farmstead Smoked Cumin Gouda" vs the real "Farmstead Cumin
  Gouda"). SKU not in the authority → STOP and ask; onboard via the RMFG Translator, re-export.

## 3. PO Boxes (+ address syntax)

Kori "Address (PO Box / Slash)" check (~2518-2533, 2613):
- **No PO Box in `Address` or `Address 2`.** Detector (~2255):
  `\b(?:p\.?\s*o\.?\s*box|pobox)\b`, case-insensitive — matches `PO Box`, `P.O. Box`, `P O Box`,
  `P.O.Box`, `POBOX`, etc. A carrier can't deliver a cold box to a PO Box → the row is flagged and
  must be corrected (real street address) before ship.
- **No forward slash `/` in `Address` or `Address 2`** (breaks downstream parsing).

## 4. Syntax rules (all QC checks, negatives-first)

- **Zip (~2458):** must be TEXT, 5-digit. Fails if stored as a number (leading zeros lost) or a
  4-digit string missing its leading zero (`1234` → should be `01234`).
- **ProductionDay values (~2506):** every row must equal the ship day — `TUE` on a Tuesday run,
  `SAT` otherwise. Any other value fails.
- **Routing / `!bang` tags (in `Tags`, ~2244-2455):**
  - Only **approved** bang-tags may carry a leading `!` (canonical `ROUTING_TAG_SET` + gel/weather
    tags `!ExtraGel24oz!`,`!ExtraGel48oz!`,`!WeatherHold!`,`!WeatherHold_Origin!` + active
    `routing_tag_configs`). An unapproved `!tag` fails; a well-formed-but-unlisted lane is flagged
    "add to ROUTING_TAGS if valid [info]" — **never hand-mint a lane tag; add it to the authority.**
  - **No `!!` (double bang)** anywhere in a tag (~2422) — malformed by definition.
  - **Combo rules** (`validate_routing_tag_combo`, ~2377): multiple routing tags must be a legal
    combination (`!ANY` is solo-only; exclusive carriers can't combine; `!NO` tags may stack).
  - **Tuesday = Dallas-only** (~2391): a routing tag naming a hub not shipping Tuesday fails; and
    **CA / FL on Tuesday require `!FedEx 2Day OneRate` (~2404).**
  - **force-2Day override** (prior-failure reship, ~2358): routing must resolve to
    `!FedEx 2Day OneRate - Dallas_AHB!`; any other carrier left in is an error. `Fixed_Route`
    pins routing and exempts route-selection checks (physical Tuesday-hub checks still run).
- **Gift Redemption (~2316):** orders tagged `gift redemption` are hard-locked (un-editable via
  import) → removed/excluded from the sheet, not shipped through it.
- **Low item count (~2482):** `Total` < 10 items fails unless the order is a `reship` or contains a
  tray (`TR-`) — else it's a suspected short box.

## 5. Asymmetric generation — INTENT-first + async verify (Kurt 2026-08-07; supersedes ledger-first)

Sheet generation reads the **INTENT BUILD**, never a blocking live-Shopify fetch and no longer a
wait on apply. Kurt, 2026-08-07: *"ship the sheet from the intent build FIRST — with the divergence
set ENUMERATED and asserted… we can always align it later."*

### 🔴 READ BOTH DIRECTIONS BEFORE CHANGING THIS — the source of truth has moved twice, for reasons

**Neither direction is naively "safer". Do not restore either one without its guard.**

| | source | why it was chosen | what it costs |
|---|---|---|---|
| **wk0703 and before** | live tags at export | a stale tag column had shipped | a blocking live fetch; the sheet serialized behind Shopify |
| **8/06 ledger-first** | apply-time ledger | col L = what apply ACTUALLY wrote | the sheet still waits for **apply** to finish |
| **8/07 INTENT-first (current)** | the intent build | removes the serialization entirely — the 2-hour drift-in crunch that nearly scrapped wk0810 | the sheet can show a tag Shopify **has not received yet** |

🔴 **INTENT-first re-opens the wk0703 failure class — a sheet showing a tag that was never written —
and the ONLY thing closing it is the divergence assert below.** That assert is therefore
**load-bearing**, not diagnostic. Weaken it to a warning and this section is strictly worse than
what it replaced: fast, and quietly wrong. Kurt, on "asserted": **hard gate**.

### The divergence contract (the load-bearing piece)

Three-way, per order: **`intent_tag`** (the build's decision) · **`ledger_tag`** (what `apply.py`
wrote) · **`live_tag`** (READ-ONLY GraphQL). Every order classifies as exactly one of:

- **matched** — intent == ledger == live. Nothing to do.
- **pending-apply** — intent set, apply hasn't reached this order yet. Expected during the async
  window; must resolve before send.
- **operator-corrected** — live diverges because a human wrote it. Honor the live tag, patch the row.
- 🔴 **ANOMALY** — any divergence that is **not** in the pending-correction set.

**Gate: `anomaly_count > 0` FAILS the sheet and NAMES the orders.** Mismatches ⊆ pending-correction
list, zero others. A sheet cannot be sent while an anomaly stands — this is the wk0703 guard.

### Unchanged by async — these gate the BUILD, not the timing

- **Multi-leg union**: P2+ legs build and apply on the **WEEK tag**; caps are per-build, so sub-tag
  builds undercount the shared trailer ([[multi-leg-shipweek-union-doctrine]]). **Tag-count
  stability** still gates. Async changes *when* the sheet is produced, never *what the build may
  produce from*. **Never format-validate operator tags.**
- A vF whose verifier pass hasn't run/completed is marked **UNVERIFIED** on its Summary line, and
  `presend_check` refuses an unverified sheet older than its cohort's last apply. **That guard does
  not relax because generation got faster** — it matters more now, not less.

### Acceptance (before any live run)

Replay wk0810 frozen: the intent-generated sheet must be **cell-identical to the ledger-generated
one on every non-divergent row**, and:

> **divergence set == corrective set MINUS net-zero round-trips, with ZERO unexplained members.**

🔴 **The obvious wording — "the divergence set reproduces the 376 logged writes exactly" — is
UNMEETABLE, and knowing why is the point.** 13 of those writes are Kurt's *"nO, WESTERN la, keep
them on dallas"* reverts: Dallas → Nashville → Dallas round-trips that **net to zero**. A
state-based diff cannot see a write that was undone, and **should not** — the sheet cares where the
box ENDED, which is the sheet-first doctrine's own logic. An implementation contorted to "reproduce
376" would have to treat an undone write as a live divergence, i.e. deliberately break the thing
the gate is for.

So the criterion has two halves and both must hold:
- **zero divergent-not-corrective** — every divergence we find is a write we logged (no phantoms);
- **corrective-not-divergent must be exactly the net-zero round-trips** — nothing else may hide there.

**Measured 2026-08-07 on wk0810** (`lib/vf_divergence.py`, real ledger + 16:12 intent build + live):
matched **1,976** · operator_corrected **275** · left_cohort **9** · **anomaly 0** · divergent∉corrective
**0** · corrective∉divergent **13** (the reverts, confirmed independently against the write log).

### Divergence classes — `left_cohort` and the emptiness trap

An order can legitimately leave the cohort (cancelled, fulfilled, or **re-cohorted** — wk0810's
`#170540` moved to `RMFG_20260811`, so stripping its routing tag was correct and the row drops).

🔴 **`left_cohort` requires POSITIVE evidence** — absent from the cohort tag query, or carrying a
different `RMFG_` batch tag. It is **never** inferred from "live has no routing tag", because an
order still IN this cohort with its tag stripped is the wk0703 symptom exactly. Emptiness-as-benign
would make this class the loophole that swallows the failure the gate exists to catch. Both
directions are test-pinned.

- 🔴 **The verifier, not live-fetching, is what fixes the wk0703 burn.** The old rule "col L from
  live tags at export" existed because a stale tag column shipped. The async verifier diffs
  intent → ledger → live AFTER generation, flagging any divergence loudly (Q1=A: alarm+patch — the
  automated wk0803 rev-2..7 workflow).
- 🔴 **The ledger is written AT APPLY TIME by apply.py, full item snapshot** (Q2=A): every order's
  tags (as-written), address (as-shipped — MASS/COG boxes keep the COG address even after Shopify
  restore), items (fulfillable lines at apply moment), box/tray class, ProductionDay inputs. One
  JSON per cohort under `_outputs/cache/vf_ledger_<ship-date>.json`; re-apply OVERWRITES with a
  diff line (rule-17 style — one file per cohort, never forked parallels).
- 🔴 **Ledger ≠ authority for names/lanes** — headers still validate against
  `mfg_names_authoritative.csv` (rule 21) and tags against the routing authority; the ledger only
  answers "what did apply write", never "what is a valid name/lane".
- 🔴 **Mid-window mutations (CS address edits, cancels, swaps) do NOT invalidate the sheet
  silently.** The verifier's diff classes: address changed → patch row + re-check lane
  serviceability per (hub, zip5); cancelled/fulfilled → remove row; tags diverged → the LIVE tag
  wins only if a human wrote it (the ledger records apply's write; any later live change is either
  a hand-fix to honor or an anomaly to flag — verifier distinguishes by diffing against the
  ledger's post-apply read-back).
- 🔴 **Verifier Shopify access is READ-ONLY GraphQL** — no sync writers (cloud ownership matrix,
  DATA_CANON). It must never write tags, orders, or the retired sync tables.
- **Gift orders**: the vFGR merge (`merge_gift_xlsx`, MATRIX_RULES 20) still overrides ledger item
  cells — vFGR remains item-truth for uneditable gift orders, and it must be swap-translated or
  re-exported post-swap ([[vfgr-gift-order-replace]]).
- **Offline regen** (Q3=A: iteration speed): any rev-N patch loop reads ledger + local caches only;
  zero Shopify round-trips between verifier passes.

## 5b. ASYNC APPLY — sheet first, tags in the background (Kurt 2026-08-09)

🔴 **PRE-CHANGE GATE for §5b.** Code: `ShipRouting/lib/apply_queue.py` (queue/lock/SLA/status),
`ShipRouting/scripts/apply_runner.py` (the ONE writer), `ShipRouting/lib/apply_writer.py` (the
mutation layer, shared verbatim with `apply.py`). Kurt's directive: *"sheet now, apply while I look
at it"*, *"we should be doing it async now"*.

**Why:** one cycle is ~9 min of machine time (build 253s, sheet 46s, apply 3-5 min) and apply sat ON
the critical path between cohort lock and the delivered sheet **for no reason**. RMFG prints labels
from the SHEET; orders are not fulfilled until **Monday 05:00 EDT**. Routing tags are a historical
record consumed days later (Fixed_Route pins, reship carrier derivation, `executed_lanes`,
DecisionLookup). The owner metric is **lock → delivered sheet** (`.claude/codex-audit-ledger.md`,
2026-08-08), so anything that is not the sheet gets off that span.

### The contract, failure-first — every line below is a failure this closes

- 🔴 **The sheet is the AUTHORITY; apply reconciles TO it, never the reverse.** If the queue and the
  sent sheet disagree, the sheet is right and the tags are the thing that gets fixed. Never
  regenerate a sent sheet to match what apply happened to write.
- 🔴 **NEVER two writers.** Two apply processes racing the same cohort double-strip `_AHB!` tags and
  the loser's read-back is garbage. `apply_queue.ApplyLock` writes **pid + start time + tag + argv**
  and a second runner is **REFUSED** (exit 3) while that pid is alive. A lock whose pid is provably
  dead is taken over *loudly*, never silently — and only because resume reconciles against live
  first (below). Do not add a `--force` that skips the liveness check.
- 🔴 **NEVER blind-retry a batch whose outcome is unknown.** A batch that died mid-flight may have
  applied. On any resume the runner **re-reads live Shopify (READ-ONLY) first** and recomputes each
  pending order's remove/add against *live*, so an already-applied order is recorded
  `reconciled-noop` and never rewritten. Retrying from the frozen plan would re-strip tags a human
  fixed in the async window — that window is exactly when Kurt is editing.
- 🔴 **The queue artifact is IMMUTABLE and content-addressed.** `apply_queue_<ship-date>.json` carries
  a `digest` over `{tag, ship_date, plan}`; the runner recomputes it and **refuses** a tampered or
  swapped artifact, and refuses to resume when the results log's digest is from a different queue. A
  queue edited by hand between plan and write is an unreviewed live mutation.
- 🔴 **Per-order results are flushed as they land** (`apply_queue_<ship-date>.results.jsonl`,
  append-only). A crash must never lose the record of what was written — that record is the only
  thing that makes resume safe. apply.py's per-line ledger flush is unchanged.
- 🔴 **A DRAINED queue requires a TERMINAL marker.** `apply_queue_<ship-date>.done.json` is written
  only when every planned order has a terminal `ok`/`reconciled-noop` record. **Failures leave the
  queue UNDRAINED** — "we tried" is not "it landed".
- 🔴 **SLA = ship-day 05:00 America/New_York, computed as an ABSOLUTE UTC instant.** This machine's
  clock is not ET; a naive "5am" fires at 1am his time and the alarm reads as fine while nothing is
  written. `apply_queue.sla_deadline_utc()` converts the ET wall time to UTC (zoneinfo; a missing
  tzdata falls back to fixed −04:00 **and says so loudly** — that fallback is wrong in EST months and
  must not be trusted past the DST switch). Every comparison is against `datetime.now(timezone.utc)`.
  Past the deadline with the queue undrained → **ALARM**: loud stdout block + `alarm: true` in
  `_outputs/reports/apply_queue_status.json`, which the weekly freshness sweep reads. The alarm never
  aborts the writes — the correct response to "late" is to finish, loudly.
- 🔴 **Dry-run is the default everywhere.** `apply.py --queue` only PLANS (all existing guards — the
  per-hub pallet cap check and `qc_gate` — still run before an artifact is ever written; a queue that
  failed a guard is never created). `apply_runner.py` without `--apply` prints and writes nothing and
  takes no lock. `--apply` is explicit, on both.
- 🔴 **Never a background zombie.** The runner processes the queue ONCE and exits — there is no watch
  loop. `--max-minutes` (default 30) is a hard wall, SIGINT finishes the in-flight batch then
  releases the lock, and `--stop` drops a sentinel the runner checks between batches. THROTTLED
  retries are depth-capped; an un-capped retry is how a live-write loop becomes unkillable.
  **Kurt stops it:** `python scripts/apply_runner.py --stop` (graceful, next batch boundary), or
  `Stop-Process -Id <pid>` using the pid printed in the status file / lock file. Killing it is always
  safe: resume reconciles.
- 🔴 **On-demand catch-up is first-class.** Dan compares orders mid-window, so tags must be
  current on request: `apply_runner.py --apply` any time; it is idempotent and resumable.
  `--check` is the read-only SLA/status probe (no lock, no writes) for the sweep.

### The divergence gate MOVES — post-apply reconciliation, not a pre-sheet gate

§5's three-way assert is unchanged in *what it checks* and changed in *when*: it runs **after the
queue drains**, as reconciliation, not as a gate the sheet waits behind.

🔴 **It must never report "clean" against a partially-written ledger.** A ledger mid-apply is missing
entries that look exactly like `pending_apply`, so a naive verify would stamp `verified_at` on a
half-written cohort and `presend_check` would wave the sheet through — the wk0703 class with extra
steps. `vf_verify.py` therefore requires the terminal completion marker for the cohort (matching
digest) before it will stamp anything; queue present + marker absent/mismatched/incomplete →
**UNVERIFIED**, exit non-zero, no stamp. A cohort with no queue at all (legacy synchronous apply) is
unaffected.

### Sheet source in async mode

`gen_rmfg_sheet` gains `VF_FROM_QUEUE=1` (default OFF): col L comes from the queue's
**`projected_tags`** — the intent-first tag set (`live − remove + add`) computed at plan time from a
fresh live fetch, for every covered order. This is what takes the sheet off apply's critical path
without falling back to pre-apply live tags. Precedence: explicit `live=` arg > `VF_FROM_QUEUE` >
`VF_FROM_LEDGER` > live fetch; a missing/mismatched queue falls through **loudly**, never silently.
The projection is intent, so §5's divergence assert is what keeps it honest — it is load-bearing here
for the same reason.

## 6. Editing discipline

Hand-editing the vF is allowed, but: pull names from `mfg_names_authoritative.csv`, lanes/tags from
the ROUTING authority, keep PO-box/zip/sort/tag syntax above, and **run Kori's QC (or an equivalent
header/name validator) on the file after any edit** — the QC is the guard; a raw openpyxl edit that
skips it is how an invented name or bad lane reaches a sent sheet.

## 7. Routing-tag EDITING on a built vF — `ShipRouting/scripts/vf_tags.py` (SSOT for the edit path)

🔴 **PRE-CHANGE GATE for §7.** The tool is `ShipRouting/scripts/vf_tags.py` (CLI + importable).
**Never hand-edit col `Tags` with find-and-replace, and never hand-roll an openpyxl tag script.**
Everything below is a real burn, written failure-first. Rules 1-8 are HARD CHECKS in the code; a
rule that cannot be enforced is named as such at the bottom.

**Why this exists:** the submitted vF is THE AUTHORITY — RMFG prints labels from the sheet, not from
Shopify. wk0810 took ~290 hand corrections in Excel (western-LA reverts, MI→IL flips, NC/SC→Nashville)
with zero validation. That is how **3 invented OnTrac-Chicago lanes** (#169610 VA 23030, #169696
KY 41262, #169785) reached a SUBMITTED sheet, and how 53 promoted lanes were rejected at induction
on wk0803 (`AHB_Failed Tags_8-3-26.xlsx`). Find-and-replace has no authority check; this tool does.

### 7.1 Never overwrite a sent/existing file — write a NEW revision
Dated/versioned output files have been clobbered before ([[never-delete-prior-output-files]]; a
`-07-27` name written on a Tuesday destroyed the real day's file). `--write` emits
`<stem>_r2.xlsx`, `_r3.xlsx`, … next to the source, **refusing** any path that already exists.
In-place editing is not an option the CLI offers. Never `delete_rows`/`delete_cols` on a flow xlsx
([[wk0727-shipping-run-lessons]]).

### 7.2 NEVER INVENT A LANE — the OnTrac master CELL is the authority, not row presence
🔴 The mechanism of the 3 invented lanes: a nationwide **zone/TNT** reference was read where a
**serviceability footprint** belongs. All 7 leaked/rejected zips (60155, 41262, 23030, 76073, 75090,
75103, 77531) are **absent from the master's per-hub column**. Every one.
- Serviceability for OnTrac = `zip_loaders.load_ontrac()[zip5][HUB_CODE[hub]]` **NONBLANK**. A zip
  being *present as a row* proves nothing — the master is mostly `None` per hub, by design.
- **Import the loader.** Never re-open the master with your own column guesses; `NASC/ILSC/DASC/
  COM/LTSC/UTA TNT` → hub mapping lives in `zip_loaders`, and a per-hub exception loader is exactly
  what invented the lanes.
- **Veho is GONE** (`CARRIER_HUBS["Veho"] == set()`, Kurt 2026-08-02 "never going back"). Any
  POSITIVE Veho tag is an ERROR, not a warning.
- FedEx/UPS legality = `features.CARRIER_HUBS` (config-effective, **not** the baseline literal).
  Import it; never re-type the roster. 🔴 A lane legal ONLY via a settings override is reported as
  `legal-by-override` so a stale toggle can't pass silently as physics.

### 7.3 Tag grammar comes from `lib/canon.py` — never hand-write a tag regex
Scattered hand-rolled regexes shipped the 378-order leaky fence (2026-07-03), and `(\w+)` patterns
broke on the **first multi-word hub** ("Salt Lake City") in 5 places at once — `\w` excludes the
space, so `!NO OnTrac - Salt Lake City_AHB!` silently reads as NOT BLOCKED. Use `canon.parse_tag`,
`canon.validate_combo`, `canon.HUB_PAT/NO_LANE_RE/NO_LANE_FIND/ANY_RE`, and the `emit_*` builders.
Hub roster = `lib/hubs.HUBS`.

### 7.4 ICE/GEL TAGS ARE ADD-ONLY — a routing edit touches ONLY routing tags
[[tray-ice-tags-never-remove]]. `!ExtraGel24oz!`, `!ExtraGel48oz!`, `!WeatherHold!`,
`!WeatherHold_Origin!`, `Reship*`, `Fixed_Route`, `Gift Redemption` and every non-`_AHB!` token are
**carried through verbatim, in original order and multiplicity** (a doubled `!ExtraGel48oz!,
!ExtraGel48oz!` is a real 2×48oz upgrade — deduping it silently downgrades the ice). The rewrite
replaces the `_AHB!` span only; the tool asserts the non-routing token multiset is unchanged and
aborts the row if it is not.

### 7.5 Fence coherence — an all-`!NO` fence must leave a NON-EMPTY, fully-legal survivor set
Emitting a fence that leaves an **uncovered** lane open is precisely how an unserviceable lane
reached RMFG's rate shop. For a row whose result is only `!NO` blocks, the tool computes the
survivor set = every (carrier, hub) that is legal AND covered for that zip5, minus the blocks. It
**FAILS** the row if the survivor set is empty (RMFG has nothing to pick) or if any survivor is
uncovered/illegal. A bare `!ANY - <Hub>` (no courier) is checked the same way: every legal carrier at
that hub must be covered for the zip, or it is a leak.

### 7.6 PRESERVE THE REST OF THE WORKBOOK — verify after writing, don't assume
openpyxl round-trips silently drop formatting, formulas, and widths. After `--write` the tool
**re-opens both files** and asserts: identical sheet names, identical max_row/max_column, and every
non-`Tags` cell value equal to source (plus every unedited `Tags` cell equal). A mismatch **deletes
the output** and exits non-zero. Columns are located **by header name, never index** (§ header note;
`Tags` is col L *today* — that is not a contract).

### 7.7 Emit a LEDGER (jsonl) of every change — it is the reconciliation authority
One line per changed row: `ts, sheet, order, zip, state, before, after, op, reason, rule, verdict`
(+ `force_reason` when forced). Path `_outputs/cache/vf_tag_edits_<stem>.jsonl`, **append-only** —
this is what `revert` reads to restore prior tags (the western-LA revert case), and it is the
record class `lib/vf_divergence.py` reconciles against. Distinct file from the apply-time
`vf_ledger_<ship-date>.json`; never write into that one.

### 7.8 DRY-RUN BY DEFAULT; any failing row blocks the write
No flag = print the per-row verdict table + counts, write **nothing** (not even the ledger).
`--write` is explicit. **If ANY targeted row fails validation the write is refused entirely** —
no partial application. `--force --reason "<text>"` overrides and logs the reason on every forced
ledger line; `--force` without a reason is rejected.

### 7.9 Validate-only is a FIRST-CLASS command, not a side effect
`validate` audits **every** row's tags against the authority with no edit. This is the check that
finds the invented lanes. It is the one command safe to run on a sent sheet.

### Known result to regress against (do not "fix" the tool until this reproduces)
`AHB_WeeklyProductionQuery_08-10-26_vF.xlsx` (2,253 rows): **exactly 3** invented OnTrac lanes —
**#169610, #169696, #169785**, all `!OnTrac Ground - Chicago_AHB!` on zips absent from the master's
`ILSC TNT` column — out of **1,521** positive OnTrac tags, and **0** positive Veho tags.

### Not enforced by this tool (know the gap)
- **Kori's full sheet QC** (PO box, zip-as-text, sort order, ProductionDay, MFG headers) is NOT run
  here — §6 still applies: run Kori QC on the `_r2` before sending.
- **Tuesday = Dallas-only** and the CA/FL 2Day-OneRate rule (§4) are cohort-level facts the sheet
  does not carry; the tool cannot infer the ship day from the workbook and does not guess.
- **Whether the edit is *right*** — the tool proves a lane is legal and covered, never that it is the
  cheapest or fastest. That judgment stays with the engine and Kurt.
