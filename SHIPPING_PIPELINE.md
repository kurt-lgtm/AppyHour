# 📦 AppyHour Shipping Pipeline — System of Record

> **This is the single source of truth for how AppyHour ships cold-chain boxes.**
> **GOLDEN RULE: if you change ANYTHING about the shipping process — a script, a rule, a carrier, a tag, a database, a threshold — you update THIS file in the same breath.** Out-of-date here = someone ships a warm box or burns money. Keep it current, keep it readable.

**Who this is for:** anyone — ops, CS, a new hire, an AI agent. No deep technical knowledge required to read §1–§3. Engineers go to §4+.
**Last updated:** 2026-06-11 · **Maintainer:** Kurt (Head of Ops)

---

## 📋 CHANGE LOG (newest first — add a row every time)
| Date | What changed | Why | Who |
|---|---|---|---|
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
- **ShipRouting `lib/engine.py compute_routing()`** — the cost-aware routing brain (North Star + TNT≤2 survivor rule). Imported by `ShipRouting/build.py` AND Kori. Currently **SHADOW** (writes tags, apply-gated). Guide: `ShipRouting/ENGINE_GUIDE.md`.
- **Kori** (`GelPackCalculator/kori/gel_pack_webview.py`) — assigns ice + routing, records Lock&Ship. Launch: `run_webview.bat`. Reads `%APPDATA%` via `appyhour_lib/paths.py::db_path()`.
- **Invoice importer (live):** `GelPackCalculator/sync_all_carriers.py` → APPDATA. ⚠️ NOT `ShippingReports/ingest_all.py` (builds a dead duplicate DB).

---

## 7. 🚧 WHAT WE'RE STILL BUILDING
🧭 **Fresh epic / big-refactor entry point: `.claude/plans/2026-06-11-MASTER-HANDOFF-coldchain-refactor.md`** — full component inventory (every script + KEEP/CONSOLIDATE/RETIRE status), refactor thesis (~11 overlapping importers → 1, two DBs → 1), and current state. Start a new `/forge epic` session there.
Roadmap: **`.claude/plans/2026-06-11-EPIC-unified-coldchain-pipeline.md`** (M1 one-DB + pointer backfill · M2 full ingest + weather actuals · M3 weekly post-mortem · M4 ice enforcement: box-upgrade + reship gel + Shopify dup-tag fix · M5 routing go-live).
Open items: retire `output/shipments.db`; backfill `cohort_key`; weather actuals not synced; ice gate can't add more ice yet (no box-upgrade + Shopify rejects duplicate gel tags); ShipRouting SHADOW→APPLY (resolve Bree/Pam Fixed_Routes).

---

## 8. 📁 WHERE THINGS LIVE
- DB: `%APPDATA%/AppyHour/shipping.db` · path helper: `appyhour_lib/paths.py`
- Importer: `GelPackCalculator/sync_all_carriers.py` · parsers: `ShippingReports/parsers/`
- Kori: `GelPackCalculator/kori/` · Routing: `ShipRouting/` (+ `ENGINE_GUIDE.md`)
- Weekly report: `~/.claude/skills/appyhour-shipping-data/queries/weekly_carrier_report.py --auto`
- Deep build handoff: `.claude/plans/2026-06-11-BUILD-HANDOFF-unified-coldchain-pipeline.md`

---
*Change the process? Add a CHANGE LOG row + update the section. That's the deal.*
