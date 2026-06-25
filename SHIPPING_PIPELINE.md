# 📦 AppyHour Shipping Pipeline — System of Record

> **This is the single source of truth for how AppyHour ships cold-chain boxes.**
> **GOLDEN RULE: if you change ANYTHING about the shipping process — a script, a rule, a carrier, a tag, a database, a threshold — you update THIS file in the same breath.** Out-of-date here = someone ships a warm box or burns money. Keep it current, keep it readable.

**Who this is for:** anyone — ops, CS, a new hire, an AI agent. No deep technical knowledge required to read §1–§3. Engineers go to §4+.
**Last updated:** 2026-06-11 · **Maintainer:** Kurt (Head of Ops)

---

## 📋 CHANGE LOG (newest first — add a row every time)
| Date | What changed | Why | Who |
|---|---|---|---|
| 2026-06-24 | **CARRIER-TNT TRUST + CLOSEST-HUB live; carrier-hub legality guard; Veho@Dallas data fix; carrier sync fix; MILP Indy epic (now the Indy decision via human-reviewed draft).** (1) **CARRIER_TNT_TRUST=1 live** (`build.py`): air-bound orders are rescued onto a FedEx/UPS GROUND lane the carrier itself quotes ≤2 via owned **ShipStation/ShipEngine** (`lib/carrier_tnt.py`), +1 ice, guarded by `legal_lane` + demote-guard; A/B 6/29 air 72→12, 0 regressions. Post-ship audit layer **`carrier_tnt_audit.py`** grades quoted-vs-actual TNT, and **every build writes `carrier_tnt_rescues.json`** (Kori-snapshot style). Needs `%APPDATA%/AppyHour/shipengine_api_key.txt`; **no key → safe no-op**. Kill: `=0`. (2) **CLOSEST_HUB_DEFAULT=1 live** (P4 phase-1): prefer the geographically closest PROVEN hub (distance tiebreak; a farther hub wins only if clearly cheaper); fixes cost-ties that broke toward Indy by lane-build order (trims Indy demand ~6%). (3) **Carrier-hub legality guard** (`lib/features.legal_lane`/`CARRIER_HUBS`): rejects physically-impossible lanes (Veho=Nashville+Indy only/never Dallas, UPS=Dallas-only, OnTrac=Anaheim+Nashville+Dallas, FedEx=all 4) at `build_history_lanes` + `choose_lane` AND at invoice ingest — caught the engine proposing `Veho - Dallas` tags. (4) **475 mis-hubbed Veho@Dallas rows re-attributed** Indy/Nashville; root-caused to the Veho parser trusting the injection-market DC over Origin Zip — fixed `parsers/veho.py` + added an all-carrier ingest guard in `shipping_invoice_db.py` (dev+prod). (5) **`sync_all_carriers.py` now pulls FedEx+OnTrac+Veho** (was FedEx-only → OnTrac/Veho invoices silently lagged); ingested OnTrac/Veho through the 6/15 ship-week. (6) **MILP Indy-capacity epic** merged to canon (`ShipRouting/milp/`): a global HiGHS solve replaces the greedy 6-pallet Indy gate's keep/spill choice with the globally cheapest set. **PROMOTED from shadow to a DECISION step** — `milp/draft_sheet.py` runs the cohort through carrier-TNT + the MILP and emits a DRAFT routing xlsx for **HUMAN REVIEW before apply** (human-gated, not autonomous; live `compute_routing` still calls the greedy `_indy_pallet_gate` as the shipped path until the draft workflow is adopted). A/B: ~$627/wk hard carrier+ice CASH saved BUT +$234/wk expected warm-arrival RISK → **~$393/wk NET** (a cash-vs-spoilage trade; quote the NET). 6/29 draft: 1664 orders, 41 carrier-TNT rescues, Indy 5.997/6, MILP differs from live greedy on 126 orders. | Lane visibility + capacity were the session's themes: vouch lanes we don't ship (carrier-TNT), route to the closest cheap hub (closest-hub), stop impossible lanes from dirty data (legality), and optimize the one hard cap (Indy MILP, now a human-reviewed draft decision). | Kurt + Claude |
| 2026-06-23 | **ENGINE FIX — Monday/Saturday carrier history now GROUND-only (air/ground contamination killed for both cohorts).** `engine.py compute_routing` changed `build_carrier_hist(ground_only=dallas_only)` → `ground_only=True`. The `(carrier,hub)` bucket qualifies GROUND lanes only (air floor is fixed cost, no history), so merging FedEx **2Day air** with **Home Delivery ground** had diluted slow ground to look 2-day: **Dallas-FedEx ground read 85% ≤2 but its TRUE ground rate is 56%**; **23 NC/FL/CA (hub,zip3) lanes mis-passed as proven-2-day-ground when real FedEx ground there hits 2-day 0% of the time.** Now fenced → those orders route OnTrac/Veho/UPS ground or 2Day air. Validated read-only first (`validate_ground_only_history.py`). Tuesday already had this (interim); now Monday too. Tradeoff: ground-only uses shipments-only (drops the service-blank delivery_status backfill — which was contaminating anyway). | The exact "Dallas-FedEx-ground = failure lane" delays were caused by the engine trusting air-diluted ground history; ground-only history fixes it at the source. | Kurt + Claude |
| 2026-06-23 | **Canon hub/tag refinements + injection-vs-transit resolved.** Hubs are last-mile PICKUP STATES — **5: TX/Dallas, TN/Nashville, CA/Anaheim, IN/Indianapolis, MA/Woburn(DEPRECATED, no live rows)**; `normalize_hub` now accepts state codes. Added **`canon.parse_routing_tag()`/`assigned_hub()`** — THE correct tag reader (skips `!NO …` blocks); replaces hand-rolled `'Dallas_AHB!' in tags`, which mis-read Veho/OnTrac as Dallas (the bug bit twice). **Injection hub (routing tag) vs transit hub (PP `origin_hub`) AGREE 97%** (OnTrac 99/FedEx 95/Veho 98/UPS 98); the 3% disagreement (88% → Dallas) is **NOT benign** — a TNT discriminator proved ~150 are **mis-fulfillments** (140 FL routed Nashville, physically shipped Dallas, delivered 3.24d > both Nashville/Dallas baselines = TNT3 late). **assigned (tag) ≠ actual (PP/invoice) is a mis-fulfillment FLAG, esp. with TNT degradation — store BOTH, never silently trust the tag.** Off-schedule pickups (Wed–Sun, 8,071) are **Veho's normal Fri/Sat regional cadence** (5,699) — not internal (0%), not reships (1%). Docs: vault `Canonical Dimension Layer.md` + `ShipRouting/CANON.md`. | One correct dimension reader kills the parser-bug class; resolving injection/transit removes the "hubs are ambiguous" false alarm (it was my parse bug). | Kurt + Claude |
| 2026-06-23 | **Canonical dimension layer + DB hygiene audit (swarm).** Established the canonical unit: a **LANE = (service_level, hub, carrier)**. New `ShipRouting/lib/canon.py` is the single source of truth — `normalize_service`→{Ground, Home Delivery, 2Day, Overnight, Unknown}, `normalize_hub` (strips `AHB`, HQ_IGNORE/Unknown/''→NULL), `normalize_carrier` (LaserShip→OnTrac), `is_ground` (Ground+Home Delivery; the predicate that keeps AIR out of ground buckets). **Root cause fixed:** `build_carrier_hist` merged FedEx **2Day air** with **Home Delivery ground** (and UPS 2nd-Day-Air) into one `(carrier,hub)` bucket → fast air diluted the ground late-rate → slow ground lanes mis-passed as 2-day (CO 80906, far-FL, HI). Fix = key history by the full lane triple; air keeps its own slice. New read-only gate `ShipRouting/db_hygiene.py` (23 invariants, severity-sorted) — baseline 16 violations: HUB-002 blank hub 7,896 (CRITICAL), SERVICE-001 un-normalized 50k, TRANSIT-002 null 16k, HQ_IGNORE 4.3k, orphans 1k, feedback 5-digit 1.4k. NOTE: **no true (invoice_id,tracking) dups** — the "17%" was NULL invoice_ids. Remediation scripts authored but **NOT applied** (adversarial critic flagged data-loss risk + unvalidated); canon layer + detection gate ARE live. Full audit: `ShipRouting/DB_HYGIENE_PLAN_2026-06-23.md`. | Un-normalized dimensions + mixed-service history silently corrupted routing qualification; one canonical layer at the source kills the whole contamination class. | Kurt + Claude |
| 2026-06-12 | **Indy pallet gate fix + violation found.** The 6-pallet Indianapolis cap is a CROSSDOCK capacity — both Veho-IND and FedEx-Home-Delivery-IND inject there. The gate counted only Veho, so Home-Delivery was invisible and Indy blew the cap: 6/15 = 428 Veho "passed at 6 pallets" while +150 Home Delivery made the TRUE load 578 (~8-9 pallets, ~50% over). Gate now counts EVERY positive `Indianapolis_AHB!` assignment. ⚠️ 6/15 was built+applied PRE-fix → its live Indy load is over cap (re-gate decision pending). Also documented: anomaly scan, eff-TNT=real (G13), engine=config (G14), trial lanes, auto course-corrections — reconciled across SKILL + vault. | Crossdock can't stage >6 pallets; uncounted Home-Delivery risks boxes sitting/delayed (the exact thing the cap prevents). | Kurt + Claude |
| 2026-06-12 | **ADDRESS-QUALITY CLOSED LOOP defined (G12) + first manual run.** Two detectors → one fixer: pre-ship `invalid_address` tag (EasyPost; 63% rural false-positives) + post-ship invoice address-correction fees (~$24 ea, corrected address in FedEx detail CSV). Fixer: merge-split → typo-fix → maps-verify → clear false positives (bare unit numbers KEPT) → ambiguous to Kurt. Today: 38 live triaged, 12 orders fixed + 25 untagged (37/37 ok) → **6/15 cohort address-clean**. Customer-default + Recharge propagation BLOCKED on scopes (Shopify `write_customers`, Recharge address-write) — renewals re-break until granted. Rule: REMOVE tag on fix (was never removed; 100+ stale). Also: anomaly_scan.py shipped (7 detectors, severity-sorted) — findings fold into the Slack issues report + cost-sheet ANOMALIES tab as ADDITIONS. | EasyPost detects but can't fix; subscribers pay correction fees monthly until source address fixed. | Kurt + Claude |
| 2026-06-11 | **Unknown-triage + weather actuals + Express probation.** (a) 906 "unknowns" = 94% Downloads noise; real find: **UPS RMFG breakdown pattern was MISSING** — 5/25+6/1 invoices (acct 2H9494) unparsed → pattern added (GelPack@1d35d4e), RMFG PDFs/old FedEx naming exempt, counter now signal-only. (b) `weather_history` was DEAD since Apr-17 (never task-scheduled) → 60d backfill run + daily 3am task; engine `dest_temp` + postmortem warm-analysis read this. (c) EXPRESS-PROBATION tier live (ShipRouting@507c257): no-local-data FedEx/UPS lane qualifies off state median when 0 proven lanes, temp<85°F, ice+1, self-demoting — shadow 76 rescues ≈ $1.4k/cohort. Vault + SKILL rulebook synced. | Fix what's fixable now; Express relaxation per positive-tagging era. | Kurt + Claude |
| 2026-06-11 | **M1 CUT-OVER COMPLETE — ONE DATABASE.** Canonical = `%APPDATA%/AppyHour/shipping.db`, sole importer = `auto_import.py` (new code live-proven: 0 dup tracking, 22,881 lane keys intact, `shipment_dims` created +1,193 rows). Dead pipeline retired (`ShippingReports/_retired/`), dead DB archived (`output/shipments.db.RETIRED-2026-06-11`). Branches merged (nested `master`@0f6f577, parent `main`@0f2b2a6). B-INGEST-1 healed (re-imports now fix routing fields). EXPRESS-PROBATION tier live in engine (ShipRouting@507c257, shadow: 76 rescues ≈ $1.4k/cohort, temp-gated 85°F). Backlog → M2: 906 unknown + 1 parse error; re-run counters over-report (count scanned, not inserted). | One source of truth; M1 milestone. | Kurt + Claude + Codex |
| 2026-06-11 | REFACTOR BUILD START (Claude orchestrating, Codex ingest track). M3 SHIPPED: Wednesday ops run now also runs routing post-mortem + cohort health (exception-only escalation, on-time floor 93%). M4 risk-label bug VERIFIED already fixed (score_risk parity, weather fallback). M1 in build (Codex, vs DB copy). M6 automation end-state designed (`_outputs/reports/2026-06-11-M6-automation-endstate-design.md`) — goal: Kurt out of the operational loop, digest+approve only. Veho GroundPlusSuite format change documented (new parser required; GP-Zero tier; IN+TN hubs only; never ingest unverified carrier×hub). | Kurt mandate: "automate me out of this loop; take the refactor over." | Kurt + Claude |
| 2026-06-11 | Created this system-of-record. Documented data map, attribution pointer, two-DB issue, North Star routing, ice/cost rules. | Unify the pipeline; stop re-discovering the same facts. | Kurt + Claude |
| 2026-06-11 | Rule: Fixed_Route customer locks are sacred — never cost/TNT-override (anecdotal customer reason behind each). | Honor customer carrier preferences. | Kurt |
| 2026-06-11 | Hardened `weekly_carrier_report.py` — `Order::%` (fulfillment) issue types HARD-EXCLUDED from shipping buckets. Verified Gorgias sync captures all views + tags `Shipping::%` vs `Order::%` correctly (no view-988673 gap). | Stop fulfillment issues counting as shipping. | Kurt |
| 2026-06-11 | Added §3.5 REPORTS WE RUN (carrier report + Gorgias issue report + derived transit/post-mortem) and §3.6 SUB-COHORTS, HUBS & TAG RULES (A=multi-hub all-carriers/Veho; B=Dallas-only no-Veho/Express; tag grammar). | Master was missing the report catalog + sub-cohort hub capabilities (existed only in vault). | Kurt |
| 2026-06-11 | RECONCILED attribution conflict: §5 now documents TWO valid attributions — pointer (`tracking_order_link`) for invoice COVERAGE/order-joins; `cohort_key` (`cohort_attribution.py`, Veho tender-offset) for WALLET-SHARE/physical-week. Corrected my over-strong "never cohort_key." Root of "152%" = double-source invoice rows → dedup by (invoice_id, tracking). Synced master + memory + vault `Shipping Data Pipeline` + `Cohort Attribution Rules`. | Stop the docs contradicting each other on cohort attribution. | Kurt |
| 2026-06-11 | Created MASTER-HANDOFF-coldchain-refactor.md — full component inventory (every script across 3 repos + KEEP/CONSOLIDATE/RETIRE status) + refactor thesis. Ingested 2 attached invoices + 23 more (FedEx/UPS→06-02). | Fresh-agent entry point for a big-refactor epic session. | Kurt |

*(When you change the process, add a row AND update the relevant section below.)*

---

## 1. 🌟 THE GOAL (plain English)
Every box must arrive **on time, still cold, without overspending on shipping.**

To do that, for each order our system (called **Kori**) picks two things:
1. **How much ice** (gel packs) the box needs — based on the weather and how long it'll be in transit.
2. **Which carrier + hub** ships it — the cheapest one that still delivers in **2 business days or less**.

**Carrier cost order (use the cheapest that still arrives in ≤2 days):**
> **Veho (~$6) → OnTrac (~$8) → FedEx Home Delivery (~$15) / UPS Ground (~$11) → LAST RESORT: FedEx 2Day Express (~$25)**

That's the **North Star**: more Veho/OnTrac, FedEx Home Delivery or UPS Ground next, and only fall back to expensive FedEx 2Day Express when nothing else delivers on time.

---

## 2. 🔄 HOW DATA FLOWS (plain English)
We pull data from **6 places**, and it all lands in **one database** so Kori and our reports can use it together:

| We get… | …from | …which tells us |
|---|---|---|
| Orders + tracking + ship-week tags | **Shopify** | what shipped, when, to where, in which weekly batch |
| Shipping cost + service + hub | **Carrier invoices** (FedEx, UPS, OnTrac, Veho — by email/download) | what each box actually cost and how it went |
| Pickup + delivery dates | **ParcelPanel** | how long it really took (was it on time?) |
| Customer complaints ("arrived warm", "delayed") | **Gorgias** | which boxes failed and why |
| Weather | weather service | how hot it was on each lane (drives ice) |
| Delivery ground-truth (when needed) | **17track** | the real delivery date when ParcelPanel lags |

**The catch:** a box's cost (from the invoice) and its order are linked by **tracking number**. We use a pointer table (`tracking_order_link`) to connect tracking → order → ship-week. **Always use that pointer** to figure out which week an invoice belongs to (see §5 — using the raw invoice date gives wrong answers).

---

## 3. ✅ THE RULES EVERYONE MUST FOLLOW
1. **One database is the truth:** `%APPDATA%/AppyHour/shipping.db`. Kori reads it. Don't write shipping data anywhere else.
2. **Cohort = the `_SHIP_<Monday>` tag**, never a carrier scan date. Sub-cohort A = Fri/Sat build→Mon ship; B = Tue/Wed build→Tue ship (Dallas-only, no Veho).
3. **Two attributions, pick by question (see §5):** invoice COVERAGE + order joins → the **pointer** (`tracking_order_link`→`_SHIP` tag); WALLET-SHARE / physical carrier-week → `cohort_key` (via `cohort_attribution.py`, deduped). Don't compare counts across the two.
4. **FedEx cost must always be shown by service** — 2Day Express ($25) is very different from Home Delivery ($15). Never quote a blended FedEx number.
5. **Fixed_Route customers are sacred** — they're locked to (or away from) a carrier for an anecdotal reason (a past complaint / preference). **Never override for cost or TNT.** If honoring their lock leaves no on-time lane, it's a manual decision (Express reship / contact them) — never force them onto the carrier they avoided.
6. **Ice is physics-first** — give the least ice that keeps margin ≥ 0; history only adds more, never less. Max ice can't hold → mark CRITICAL.
7. **ParcelPanel lags 1–3 days** — "undelivered" usually means "delivered, not yet recorded." Verify with 17track before calling anything late.
8. **On-time = delivered in ≤2 days ÷ the FULL cohort** (not just delivered orders — that fakes 100%).
9. **OnTrac = LaserShip** (same carrier, two brand names on scans).

---

## 3.5 🗓️ REPORTS WE RUN
| Report | What it shows | Cadence | Source / doc |
|---|---|---|---|
| **Weekly Carrier Report** | carrier MIX trend + spike flags + shipping ISSUES by carrier×bucket + COST by carrier×service (FedEx HD/Ground combined; 2Day separate) | weekly (Wed) | `appyhour-shipping-data/queries/weekly_carrier_report.py --auto` |
| **Weekly Shipping Issue Report** | Gorgias CS issues (Warm/Delayed/Lost/Damaged) by carrier, reconciled vs Slack `#reship-and-order-requests` ground truth | weekly (Tue, prior Mon–Sun ticket window) | `~/.knowledge/ops/Weekly Shipping Issue Report.md` runbook |
| **Derived transit times / post-mortem** | actual TNT (pickup→delivery, **business days**) vs the 2-day promise; on-time = delivered≤2 ÷ FULL cohort; warm/delayed **vs Kori snapshot** | weekly + ad-hoc | `ShipRouting/cohort_health.py`, `routing_postmortem.py`; method = `~/.knowledge/decisions/Universal transit calculation rule.md` |
| **Anomaly scan** ("what needs attention") | 8 detectors vs each lane's own trailing baseline (not raw counts), severity-sorted, evidence + action per finding: lane-late-rate spike (3σ) · warm clusters (state×carrier) · $/box drift (>12%) · Express-share creep (vs ~11%) · **fence violations** (actual carrier vs `!NO` tags = the live B1 monitor, ANY hit CRITICAL) · matured still-out · feed freshness · address-quality. Quiet = healthy. **Not a new report — ADDITIONS:** CS-actionable flags → Slack issue report; trend flags → cost-sheet ANOMALIES tab; feed-health → notify channel. | weekly (folds into Wed post-mortem) + ad-hoc ("anomaly scan") | `~/.claude/skills/appyhour-shipping-data/queries/anomaly_scan.py` |

**Two report rules:** (1) shipping issues count `Shipping::%` only — `Order::%` (fulfillment) is a SEPARATE report, never mixed in. (2) Derived TNT uses business days (skip weekends), pickup_date→delivery_date from `delivery_status`, verified against 17track when ParcelPanel lags.

## 3.6 🏭 SUB-COHORTS, HUBS & TAG RULES
Two sub-cohorts per `_SHIP_<Mon>` week (by the `RMFG_<date>` tag), with **different hub + carrier capabilities** — the engine and CS must respect them:

| Sub-cohort | Build → ship | Volume | Hubs | Carriers | TNT note |
|---|---|---|---|---|---|
| **A** (Sat cohort) | Fri/Sat build → **Mon** pickup | ~70% | **Multi-hub: TN, TX, IN, CA** | ALL incl **Veho** | multi-hub → 2-day reliable to most zones |
| **B** (Tue cohort) | Tue/Wed build → **Tue** pickup | ~20% | **Dallas (TX) ONLY** | FedEx / OnTrac / UPS — **NO Veho** | Dallas-only → zones 5-7 can't hit 2-day on Ground → **must use FedEx 2Day Express for SLA** |

Hubs are **TN / TX / IN / CA only** (not MA). The v2 routing engine runs for **A only**; B is Dallas-only.

**Tag grammar** (`!ANY`-solo / `!NO`-stack): `!ANY - <hub>_AHB!` = positive single-hub assign (only ever Veho/OnTrac); `!NO <carrier> - <hub>_AHB!` = fence a bad lane (FedEx/UPS steered by blocking, never positively tagged); `!FedEx 2Day OneRate - Dallas_AHB!` = Express floor. Ice tags: `!ExtraGel24oz!`, `!ExtraGel48oz!` (⚠️ Shopify rejects duplicate tags — need quantity-encoded tag for >1, see M4). Full grammar: `~/.knowledge/codebase/Routing rule engine (Kori).md` + `ShipRouting/ENGINE_GUIDE.md` §2.
Detail: `~/.knowledge/ops/Shipping Cohort Hub Structure.md`.

## 4. 🗂️ DATA MAP (technical)
Full schema, row counts, and freshness: **`.claude/plans/2026-06-11-BUILD-HANDOFF-unified-coldchain-pipeline.md` §2.** Quick reference:

| Table | What it is | Key columns |
|---|---|---|
| `fulfillments` | shipped order-lines; **cohort authority** (`tags` = `_SHIP_` + `RMFG_`) | order_number, tags, tracking_number, fulfilled_at, dest_zip |
| `tracking_order_link` | 🔑 **the pointer**: tracking → order# | tracking (PK), order_number |
| `shipments` | invoice line items (cost) | tracking, carrier, service, hub, cost, ship_date, cohort_key⚠, is_internal |
| `delivery_status` | ParcelPanel pickup/delivery | tracking_number, pickup_date, delivery_date, status |
| `feedback` | Gorgias CS issues | order_number, issue_type, date_reported, gorgias_link |
| `kori_snapshots` / `kori_snapshot_orders` | Kori's per-cohort/per-order prediction record | predicted_config, predicted_risk, margin_btu, dest_peak_temp_f |
| `shopify_orders` | order header (price, status, tags) | order_name, ship_tag, total_price |
| `weather_history` | per-zip daily temps (zip_prefix = 5-digit) | zip_prefix, date, peak_temp |
| `gel_apply_log` | when ice tags were applied + margin | ship_tag, order_number, applied_at, apply_margin_btu |
| `invoices` | invoice-email metadata | carrier, invoice_week, total_balance |

---

## 5. 🔑 THE ATTRIBUTION CHAIN (technical — how cost → cohort)
**There are TWO valid attributions. Pick by the QUESTION you're asking — they legitimately diverge (rolled-forward orders + carrier tender offsets).**

**(a) ORDER attribution (pointer) — use for invoice COVERAGE + order-level reconciliation:**
```
shipments.tracking → tracking_order_link → order_number
  → fulfillments.tags → _SHIP_<Mon> (cohort) + RMFG_<date> (sub-cohort A/B)
```
Answers "of the orders tagged `_SHIP_X`, how many have an invoice / what did they cost." This is the one for coverage %, CS/order joins, and the per-cohort cost we report.

**(b) PHYSICAL-WEEK attribution (`cohort_key`) — use for WALLET-SHARE / carrier spend by the week packages physically shipped:**
`shipments.cohort_key` via `GelPackCalculator/cohort_attribution.py::cohort_for()` — carrier ship_date − day-of-week, with **Veho tender-date offset** (Veho's date = Sat tender, picks up next Mon). Authoritative for "what physically shipped this carrier-week" (carrier negotiations). Detail: vault [[Cohort Attribution Rules]].

⚠️ **Do NOT compare counts ACROSS (a) and (b)** — they answer different questions; ~rolled-forward orders make them differ by design. My earlier "cohort_key 56%/152% vs pointer 100%" was an apples-to-oranges compare (order-tag coverage vs physical-week) **compounded by a real double-source bug**: the same invoice exists in both the RMFG breakdown XLSX and the FedEx/UPS account CSV → cohort_key double-counted (the 152%). **Fix = dedup invoice rows by (invoice_id, tracking) before any cohort_key rollup.** Once deduped, cohort_key is correct for wallet-share; the pointer stays the one for coverage.

---

## 6. 🧠 THE DECISION COMPONENTS (technical)
- **ShipRouting `lib/engine.py compute_routing()`** — the cost-aware routing brain (North Star + TNT≤2 survivor rule). It is **THE config now (not "shadow")** — `build.py`, `apply_shadow_ice.py`, AND Kori (`is_pipeline_v2=True`, G14 2026-06-12) all decide routing + ice from it. **Routing is fully unified; ICE has 3 entry points that can differ** (audit 2026-06-12): engine shadow uses DB-avg temp; Kori re-sizes with LIVE forecast temp (upgrade-only); `apply_shadow_ice` adds a summer-worst-case downgrade guard. Convergence tracked as G18. Guide: `ShipRouting/ENGINE_GUIDE.md`.
- **MILP Indy-capacity draft (`ShipRouting/milp/draft_sheet.py`)** — runs the cohort through carrier-TNT + a global HiGHS solve that picks the globally cheapest keep/spill set under the 6-pallet Indy cap, and emits a DRAFT routing xlsx. **This MAKES the Indy decision, but is HUMAN-GATED — surfaced as a draft for review before apply, NOT autonomous.** Until the draft workflow is adopted, live `compute_routing` still ships via the greedy `_indy_pallet_gate`. A/B trade: ~$627/wk hard CASH saved vs +$234/wk warm-risk → ~$393/wk NET. Guide: `ShipRouting/milp/`.
- **Kori** (`GelPackCalculator/kori/gel_pack_webview.py`) — assigns ice + routing, records Lock&Ship. Launch: `run_webview.bat`. Reads `%APPDATA%` via `appyhour_lib/paths.py::db_path()`.
- **Invoice importer (live):** `GelPackCalculator/sync_all_carriers.py` → APPDATA (now FedEx+OnTrac+Veho). ⚠️ NOT `ShippingReports/ingest_all.py` (builds a dead duplicate DB).

---

## 7. 🚧 WHAT WE'RE STILL BUILDING
🧭 **Fresh epic / big-refactor entry point: `.claude/plans/2026-06-11-MASTER-HANDOFF-coldchain-refactor.md`** — full component inventory (every script + KEEP/CONSOLIDATE/RETIRE status), refactor thesis (~11 overlapping importers → 1, two DBs → 1), and current state. Start a new `/forge epic` session there.
Roadmap: **`.claude/plans/2026-06-11-EPIC-unified-coldchain-pipeline.md`** (M1 one-DB + pointer backfill · M2 full ingest + weather actuals · M3 weekly post-mortem · M4 ice enforcement: box-upgrade + reship gel + Shopify dup-tag fix · M5 routing go-live).
Open items: retire `output/shipments.db`; backfill `cohort_key`; weather actuals not synced; ice gate can't add more ice yet (no box-upgrade + Shopify rejects duplicate gel tags); ShipRouting SHADOW→APPLY (resolve Bree/Pam Fixed_Routes); adopt the MILP `draft_sheet.py` Indy-decision workflow (today it's drafted + human-reviewed, but live ships the greedy `_indy_pallet_gate`).
⚠️ **Offsite backup is BROKEN (local-only).** `scripts/backup_offsite.py` snapshots `shipping.db` to `%APPDATA%/AppyHour/backups/`, but the Drive upload step (`gws drive +upload`) fails — **`gws` is not installed / not on PATH** — so the "weekly offsite" has been **LOCAL-ONLY**. Fix = reinstall/auth the `gws` CLI or swap the uploader (rclone / Drive API). (Code itself IS offsite via the GitHub repos.)
📝 **Deployment note: canon = live.** Branch `restore/cohort-scripts-2026-06` (GitHub `kurt-lgtm/ShipRouting`) is PRODUCTION — pushing to it deploys; prod imports the working tree at `C:\Users\Work\Claude Projects\ShipRouting`.

---

## 8. 📁 WHERE THINGS LIVE
- DB: `%APPDATA%/AppyHour/shipping.db` · path helper: `appyhour_lib/paths.py`
- Importer: `GelPackCalculator/sync_all_carriers.py` · parsers: `ShippingReports/parsers/`
- Kori: `GelPackCalculator/kori/` · Routing: `ShipRouting/` (+ `ENGINE_GUIDE.md`)
- Weekly report: `~/.claude/skills/appyhour-shipping-data/queries/weekly_carrier_report.py --auto`
- Deep build handoff: `.claude/plans/2026-06-11-BUILD-HANDOFF-unified-coldchain-pipeline.md`

---
*Change the process? Add a CHANGE LOG row + update the section. That's the deal.*
