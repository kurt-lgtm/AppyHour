# ORCHESTRATION — Cold-Chain Refactor, Claude ∥ Codex

**Date:** 2026-06-11 · Orchestrator: Claude · Worker: Codex (background subagent) · Owner: Kurt
**Parent:** `2026-06-11-MASTER-HANDOFF-coldchain-refactor.md` (mission, milestones M1–M5, component inventory)
**This doc:** how the two agents split the work without colliding, the frozen contract, and the safety rails.

---

## North Star (unchanged)
ONE DB · ONE importer · ONE parser set · ONE attribution convention · engine LIVE · **cost as a routing INPUT, not a post-hoc report.**
Cost order within TNT≤2: Veho $6 → OnTrac $8 → FedEx HD $15 / UPS Ground $11 → LAST FedEx 2Day $25.

---

## Parallelization Thesis
A refactor parallelizes **badly through a shared spine** (the DB + the importer). The clean seam is **WRITE/INGEST vs COMPUTE/DECISION** — they meet only at the frozen schema. ShipRouting is already a **separate repo**, which gives us free isolation.

| | **Codex track — INGEST SPINE** | **Claude track — DECISION LAYER** |
|---|---|---|
| Milestones | M1 (one DB/importer) + M2 (complete ingest) | M3 (post-mortem) + M4 (ice enforcement) + M5 (routing go-live) |
| Repos/dirs | `AppyHour/GelPackCalculator/*.py` (root scripts) + `AppyHour/ShippingReports/` | `ShipRouting/` (engine) + `AppyHour/GelPackCalculator/kori/` + `~/.claude/skills/appyhour-shipping-data/` |
| Nature | mechanical, high-file-count consolidation against a clear spec | domain judgment + production-risk gating |
| Why this agent | Codex strong at large spec-driven grind | Claude holds the rules (Fixed_Route sacred, cost order, dietary, ice physics) + owns the warm/expensive-if-wrong gates |

**Directory disjointness:** Codex edits GelPackCalculator **root** scripts + ShippingReports. Claude edits the **kori/** subdir + ShipRouting + skills. Same repo (AppyHour) for kori vs importers → use **worktrees on separate branches** so working trees never thrash.

---

## The Contract — ❄️ FROZEN 2026-06-11 (verified by both audits; read-only, no unilateral/silent change)
Both audits cross-confirmed these. Any change = edit this doc + notify the other agent + re-validate.

1. **DB path:** `appyhour_lib/paths.py::db_path()` → `%APPDATA%/AppyHour/shipping.db` (live, 110 MB, Kori writes it). Snapshot: `…/AppyHour/backups/shipping.snapshot-2026-06-11.db`.

2. **Tables + row baseline (queried 2026-06-11):** `shipments` 72,779 · `delivery_status` 93,186 · `fulfillments` 93,107 · `tracking_order_link` 91,093 · `shopify_orders` 32,057 · `kori_snapshot_orders` 8,046 · `feedback` 2,904 · `gel_apply_log` 2,918 · `weather_history` 724 · `invoices` 219 · `kori_snapshots` 6.

3. **Engine/Kori read-columns — PRESERVE EXACT NAMES (do NOT rename):**
   - `shipments`: `carrier, service, hub, state, zip_code, city, zone, cost, weight, ship_date, delivery_date, transit_days, invoice_id, box_type, cohort_key, subcohort, acct`. ⚠️ Stays `zip_code` — never the dead-DB name `zip` (Kori aliases `zip_code AS zip` at query edge only; `shipping_invoice_db.py:1560`).
   - `weather_history`: `zip_prefix, peak_temp, avg_temp, date`. ⚠️ `zip_prefix` holds **5-digit** zips; join `substr(dest_zip,1,5)`.
   - `delivery_status`: `tracking_number, carrier, status, pickup_date, delivery_date, transit_days, last_event, service, order_number`.
   - `gel_apply_log`: full schema exact (`ship_tag, order_number, applied_at, applied_config, applied_48oz, applied_24oz, apply_margin_btu, apply_peak_temp_f, forecast_hash`) — M4 + drift audit depend on it.

4. **Writer interface (Codex owns guts; Claude builds against signatures):** `shipping_invoice_db.py` — `store_invoice` (upsert by `id`), `store_shipments` (upsert by `tracking`; on-conflict updates only `cost,delivery_date,transit_days,hub,box_type`), `store_fulfillments`/`store_delivery_status` (upsert by `tracking_number`), `parse_{fedex_xlsx,fedex_csv,ontrac_csv,ups_csv}_bytes`.

5. **Attribution + dedup:** pointer (coverage) vs `cohort_key` (wallet-share) — never compared across. **Dedup `(invoice_id, tracking)` at the cohort_key ROLLUP, not at storage.** Storage stays one physical row per `tracking` (unique index on `tracking`). ⚠️ A row-drop beyond duplicate `(invoice_id,tracking)` candidates would change per-`(carrier,hub,zip_code,transit_days)` historical eff-TNT → breaks routing. Canonical currently has 0 dupes (72,779 = distinct tracking = distinct invoice_id|tracking).

6. **Cross-repo coupling (must not break):** ShipRouting is a **separate repo** at `C:\Users\Work\Claude Projects\ShipRouting` (Codex's audit looked under `AppyHour\ShipRouting` → not found; my audit covers it). `kori/routing_v2.py:19-24` hard-codes `{ShipRouting, AppyHour, AppyHour/AppyHourMCP}` on sys.path + imports `box_simulation`. Veho parser is ShippingReports-only and `auto_import.py:221-237` imports `parse_veho_xlsx` from it → **move Veho parser into APPDATA before retiring ShippingReports.**

### M1 CORRECTNESS BLOCKERS (verified — must resolve in M1)
- **B-INGEST-1 — stale routing fields on re-import** (Claude's challenge to Codex, verified `shipping_invoice_db.py:940-945`). `store_shipments` `ON CONFLICT(tracking)` refreshes only `cost, delivery_date, transit_days, hub, box_type`. It does NOT refresh `state, zip_code, zone, service`. The engine routes on `state/zip_code/zone` → a bad/blank first insert is **frozen**, a later corrected file silently can't fix it. **Decide in M1:** expand the on-conflict SET to the routing fields (`COALESCE(excluded.x, shipments.x)`), or document why first-insert-wins is safe. Until resolved, re-imports can't heal routing inputs.

### VEHO TNT — weekly re-quote handling (CROSS-TRACK; ✅ 6.9.2026 file analyzed, design LEFT OPEN per Kurt)
Full analysis: `_outputs/reports/2026-06-11-veho-groundplussuite-analysis.md`. New file archived (non-destructive): `%APPDATA%/AppyHour/routing/archive/veho_groundplussuite_2026-06-09.xlsx`; **live `veho_ground_plus.xlsx` untouched.**
**How it flows today:** `ShipRouting/lib/zip_loaders.py::load_veho()` reads `%APPDATA%/AppyHour/routing/veho_ground_plus.xlsx` (stable home; Downloads fallback). Returns per-zip `{active, IN/CA/TN/TX}`. Engine consumes only **IN Veho + TN Veho** (`optimizer.ROUTE_OF_COL` — no CA/TX-Veho lane). Quoted TNT → `hist_risk`-corrected by `shipments` actuals. Single overwritten file → no version history, stale-file risk.
**Findings (6.9.2026 file):**
- ⚠️ **FORMAT CHANGED → `load_veho()` BREAKS on it.** New `GroundPlusSuite` export = multi-sheet (disclaimer `Coverage Details` is `wb.active`) + 5 tier sheets `Ground Plus Zero..Four`, two-row header, 11 injection-hub triples. `load_veho()` reads the disclaimer → 0 zips → engine loses ALL Veho serviceability → fences the cheapest carrier. **M1 needs a NEW Veho parser; do NOT drop this file into the stable home until it exists.**
- **Tier = Ground Plus Zero** (100% match to current TNTs, 7,296 common zips). One–Four = +1..+4 day variants, unused.
- **Veho hubs = IN(Indianapolis) + TN(Nashville) only.** "Inland Empire" = rename of old "LA" (NOT a new hub). **No new hubs** (Boston etc. ignored).
- **Precedence (TX/CA-Veho):** `shipments` Veho by hub = Indianapolis 2,716 · Nashville 2,561 · **Dallas 493 · Anaheim 38.** TX-Veho has real precedent (493, RMFG-originated, eff-TNT unaudited); CA-Veho thin (38). Default keep out; add-TX-Veho = open Kurt decision pending viability audit.
- **Churn:** 7,613 zips (was 7,452); **1,345 active-status flips** on common zips (stale-file risk is real); **146 new Active zips gain a Veho IN/TN ≤2 lane**; **194 "Pending - July Activation"** zips (forward expansion, don't route till Active).
**Two layers, design OPEN:**
1. **INGEST (Codex/M1):** new parser (GP-Zero, IN+TN, `Serviceable`+`Zip Code Status`) → version the weekly feed (archive pattern started) + optional canonical `veho_tnt(zip, in_tnt, tn_tnt, active, status, effective_week)` table; validate GP-Zero reproduces current TNTs (done: 100%).
2. **ENGINE POLICY (Claude/M5):** the 146 new ≤2 Veho lanes are unproven → survivor-invariant fences them (chicken-and-egg). Options: fence-till-proven (current) · **shadow-first→provisional** (rec; Kori shadow infra exists) · provisional-trust w/ auto-demote. LEFT OPEN.

### OPEN DECISIONS (Kurt owns — surfaced, not assumed)
- **DIM fields → ✅ RESOLVED 2026-06-11 (Kurt): preserve, but via the POINTER pattern — do NOT widen `shipments`.**
  - New side table `shipment_dims(tracking PRIMARY KEY → shipments.tracking, actual_weight, dim_l, dim_w, dim_h, dim_factor)`. Hot table (`shipments`, scanned per-cohort by `build_carrier_hist`) stays lean; dims joined only when box-cost/distvol analytics need them. Same precedent as `tracking_order_link`.
  - ⚡ Efficiency catch: **3 of the 6 "missing" fields aren't missing** — `order_id/order_name/ship_tag` are already reachable via `tracking_order_link → fulfillments` (`order_number`, `tags` carry the `_SHIP_` tag). Don't duplicate them; only the physical measurements (`actual_weight, dim_*`) are genuinely new → only those go in `shipment_dims`.
  - Contract impact: ADD a table, don't ALTER `shipments` → engine read-columns (§3) untouched, no breaking change.
- **B1 premise (M5 apply) — ✅ EVIDENCE IN (2026-06-11, cross-critiqued):** history shows **0 fence violations in 9,889** matched `!NO`-tagged orders, with REAL FedEx/UPS samples (FedEx !NO n=2,148, UPS n=3,702, OnTrac n=12,071, Veho n=2,874 — independently verified). Positive-tag compliance FedEx 99.2%. **Confidence cap:** all history predates the 2026-06-10 TNT-removal — old-regime RMFG TNT rules may never have chosen those lanes anyway (confound). Fences-honored-historically = HIGH; transfers-to-cheapest-rate-era = MEDIUM. **Resolution: the supervised 6/15 cohort IS the live B1 test** (post-ship invariant check). Don't block on asking RMFG; monitor instead. Pack: `_outputs/reports/2026-06-11-evidence-packs-b1-dallasveho.md`.
- **Dallas-Veho lane — ❌ DO NOT WIRE (verdict FLIPPED in cross-critique):** agent said 89.0% ≤2, "wire conditional" — but used a **delivered-only denominator** (feedback_ontime_denominator trap): 103/493 (21%) NULL-transit excluded, of which **58 still "in transit/OFD/pending" on a lane whose last ship was 6/05** → matured still-out = LATE, not pending. Corrected on-time **77.5%–89%** (PP-lag band) vs Indy 98.1% / Nashville 91.0%. Below bar either way. Re-audit after M2 wires 17track ground-truth. Precedence ≠ viability.
- **Bree MD + Pam FL** Fixed_Route manual decision (M5 apply).
- Doc-rot: `AppyHourShippingMCP/CLAUDE.md:39` still calls the dead DB canonical → fix in M1.

---

## Safety Rails (non-negotiable — production DB)
1. **Snapshot live DB before any write-side change.** 110 MB, written today 16:53, Kori reads it live.
2. **Refactor runs against a COPY** (`shipping.refactor.db`); validate row-parity vs live; **cut over only after green.** No in-place importer surgery on the live file.
3. **Git worktrees + separate branches per agent** — `refactor/ingest-spine` (Codex), `refactor/decision-layer` (Claude).
4. **No importer retired until cron/scheduler audit proves it unwired** — `weekly_scheduler.py`, `daily_shipping_sync.py`, `backfill_sync.py`, `sync_logon.py`. A retired-but-cron'd script = silent data loss.
5. **Fixed_Route sacred; engine stays SHADOW** until the explicit M5 flip (Bree MD + Pam FL resolved, cost guardrail, single 6/15 cohort).
6. **Verify Codex claims independently** — `ls` + row-counts, never trust "wrote/consolidated" reports blind (subagent-write-unreliability).

---

## Action Steps (sequence)

### STEP 0 — Setup (Claude, ~15 min)
- Snapshot live DB → `shipping.snapshot-2026-06-11.db`.
- Create 2 worktrees + branches (AppyHour `refactor/ingest-spine`, decision-layer branch across ShipRouting + AppyHour/kori).
- Dump current schema + writer signatures into the **Contract** section above → freeze.

### STEP 1 — Phase 0 audit (BOTH, read-only, parallel)
- **Codex:** overlap matrix of all 11+ importers (what each writes, by carrier/source), canonical path, **cron-wiring per script**, dead-DB diff (110 MB vs 20 MB / ~15k divergent rows). → consolidation spec + retirement list.
- **Claude:** engine go-live readiness (cost-as-input gaps, risk-label bug JS→Python port, Bree/Pam), ice-enforcement design (box-upgrade for neg-margin + Shopify dup-tag `!ExtraGel48oz_x2!`), post-mortem wiring into the Wed task. → decision-layer build spec.
- **GATE:** review both specs together → freeze contract, confirm retirement list.

### STEP 2 — M1 build (Codex critical path) ∥ Claude decision build
- **Codex (against the COPY):** `auto_import` absorbs the 10 overlapping importers; retire `output/shipments.db` + `ingest_all/ingest/build_db/merge_jsons`; migrate the good parsers to feed APPDATA, retire dupes; backfill `cohort_key` via pointer; coverage-% logging; `(invoice_id, tracking)` dedup.
- **Claude (against frozen schema, no DB-write contention):** engine cost-input + guardrail; ice-enforcement build; post-mortem into recurring Wed task.
- **Integrate** when Codex M1 lands green on the copy → row-parity validate → cut over.

### STEP 3 — M2 complete ingest (Codex)
One orchestrated run (all 6 sources), weather actuals nightly, 17track wired, OnTrac/UPS IMAP folded in. Clears the 900-unknown + 1 parse_error triage.

### STEP 4 — Integration + go-live (Claude owns the gates)
M4 ice enforcement merge; M5 engine SHADOW→APPLY (single 6/15 cohort, cost guardrail, Bree/Pam manual decision logged).

---

## Research method — EVIDENCE PACKS (lightweight autoresearch, Kurt 2026-06-11)
The old autoresearch (`/forge research` → gsd-phase-researcher + .planning state) is too heavy here. This epic uses **evidence packs**: each OPEN DECISION gets a background research agent with (a) a contract-bound brief, (b) internal-data-first sourcing (our DB/shadow history/codegraph before any web), (c) decision-ready output shape: *claim → evidence (queries/cites) → verdict → confidence*, (d) cross-critique before the verdict is trusted, (e) result filed into this doc's decision log — no .planning machinery. Research runs WHILE build continues; it never blocks the critical path. First two packs launched 2026-06-11: **B1 fence-honor pre-test** (history: did RMFG ever pick a !NO-fenced lane?) + **Dallas-Veho viability** (493-shipment eff-TNT audit) → `_outputs/reports/2026-06-11-evidence-packs-b1-dallasveho.md`.

## Coordination — independent execution + reconvene-and-critique (Kurt's call, 2026-06-11)
The two agents do **not** hand off blind. Each works its track independently, then they **reconvene and adversarially critique each other's work** at every gate. The split below is the *ownership* default; the critique is mutual.
- **Codex owns INGEST** (M1/M2); **Claude owns DECISION** (M3/M4/M5). Each produces; each then reviews the *other's* output before it's accepted.
- **Cross-critique gate (every milestone):** producer ships → reviewer (the other agent) red-teams it → disagreements surface to Kurt, not silently merged. Claude additionally verifies Codex's claims mechanically (`ls`, row-counts) — never trusts "done" reports.
- **Sync points:** (a) after Phase 0 audit → **cross-critique both specs → freeze contract → return to Kurt for code go**; (b) after M1 green → cross-critique → cut over; (c) before M5 flip → cross-critique → go-live gate.
- M1 is the only hard dependency for M5; everything else builds against the frozen schema and merges at the gates, so neither agent blocks the other.

## START DECISION (2026-06-11)
**Phase 0 = audit only.** Snapshot taken, both audits run read-only, no DB writes, no code edits. Hard stop at contract-freeze → report to Kurt → await go before any build.
