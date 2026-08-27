# PP_ORIGIN_HUB_RULES.md — PP-native origin-hub derivation (SSOT)

🔴 **PRE-CHANGE GATE.** Single source of truth for `pp_origin_hub` (derived table) and
`appyhour_lib/pp_origin.py` (the callable). Read this BEFORE touching either. Change the rule HERE
first, in the SAME commit as the code.

**Status: authored 2026-08-27 alongside the first implementation.** Every measurement below is from
the 2026-08-20 → 2026-08-27 window of `pp_webhook_events` (8,664 raw events → 2,694 distinct
orders), re-derived in this session rather than taken on report.

---

## 🧭 NORTH STAR

> **Know which hub a box actually left from, for every box, from data we already ingested — and say
> `MISSING` the moment we do not know.**

This nests under AppyHour's north star (*lowest expected total cost, loud failures never silent
ones*) and under STATUS_INGEST_RULES (*one canonical status path, no consumer keeps its own carrier
call*). A change that raises coverage by GUESSING a hub moves AWAY from this and is worse than the
gap it fills: every downstream lane number, hub block, and engine-miss conclusion inherits the
guess. Coverage is not the goal — **provable** coverage is.

Two hard floors, never for sale:
1. **Zero ParcelPanel API calls.** Ever. This is pure derivation from ingested payloads.
2. **Read-only on cloud MySQL.** `pp_webhook_events` belongs to the flow-api webhook route.

---

# 🔴 GOTCHAS — what NOT to do (negatives first)

## The two that cost a pass each

**1. NEVER take `checkpoints[0]`, and NEVER assume the array's order.**
Measured: the `checkpoints` array is **NEITHER ascending NOR descending on 2,694 / 2,694 payloads.**
It is carrier scans newest-first, followed by PP/store status lines in their own order. A handover
note describing it as "sorted ASCENDING" was wrong on every payload we hold. `iter_checkpoints()`
sorts by `checkpoint_time` itself; nothing may bypass it.

**2. NEVER treat the earliest checkpoint as a carrier scan.**
The first 2–3 entries are PP/store status lines carrying no location:
*"Orders are prepared fresh weekly…"*, *"Order Ready"*, *"The package data was sent to OnTrac, but
we have yet to receive the package…"*. A naive `checkpoints[0]` + city regex returned **0 hits on
6,000 / 6,000 payloads** (Routing Coordinator, measured). The gate is the checkpoint's own
`status`: a physical scan has a `status` in `MOVEMENT`, which excludes `INFO_RECEIVED` and the
`status: null` store lines **by construction**, not by a text blacklist that the next PP copy edit
would break.

## Carrier identity

**3. NEVER key on ParcelPanel's raw carrier name.** PP says **LaserShip** where the scan text says
OnTrac — **1,806 of 2,694** payloads. `canon.normalize_carrier` folds it (OnTrac is canonical,
LaserShip is the alias; STATUS_INGEST_RULES rule 19 — do not re-reverse that fold). No LaserShip
bucket exists anywhere. `derive_origin()` takes `canon` as a **required keyword argument** precisely
so it cannot be called without a canonicaliser.

**4. NEVER hand-roll a routing-tag regex. `canon.parse_routing_tag` is the only correct reader.**
Two live burns, both in this session's first pass:
- `\b` after the hub name matched nothing, because `_` in `Swedesboro_AHB` is a word character →
  **0 hub tags on all 2,694 orders**, reported as a confident zero. A zero is a claim.
- A substring test (`'Dallas_AHB!' in tags`) reads Dallas out of the **exclusion** block
  `!NO OnTrac - Dallas_AHB!`. **46 live orders** in this window carry ONLY `!NO …` blocks
  (fence-only — RMFG picks freely, there is no assigned hub) plus **44** with no routing tag at all.
  For those 90 orders the scan-derived hub is the ONLY hub source that exists.

## Fields that look useful and are not

**5. NEVER build on `pickup_location`.** The key exists and is **NULL on 2,694 / 2,694** payloads.
Independently re-verified here, not taken on report.

**6. NEVER use `location.name` as the hub.** It is the SHIPPER — `RMFG` ×2,690, `COG` ×4.

**7. NEVER compute TNT from PP's `transit_time` integer.** TNT is delivery − **PICKUP SCAN**,
final-mile only, CALENDAR days (ShippingReports "TNT calc HARD RULE"; STATUS_INGEST_RULES rule 16).
`transit_days` is computed; `transit_time` is never read.

## The fabrication trap

**8. NEVER map a scan city to a hub on tag correlation alone.**
`WILMINGTON, MA 01887` appears as the first physical scan on **125 orders, 122 of them (97.6%)
Chicago-tagged** — a concentration that passes any threshold. It is **REFUSED**:
- Woburn MA is **HQ, not a hub** (ShippingReports/CLAUDE.md; `canon.DEPRECATED_HUBS`).
- FedEx stamps that scan at a synthetic `T00:00:00` with the shipper **ACCOUNT** address.
- The same payloads' pre-pickup line reads `Shipment information sent to FedEx, 60445` — a Chicago
  origin zip. The box came from Chicago; the SCAN is not evidence of where it came from.

Mapping it would have been inventing a hub out of a tag correlation — the [[never-fabricate]] class.
Unmappable facilities get `origin_hub = 'MISSING'` and appear in the builder's decision list.

**9. NEVER let a MISSING row count as a disagreement.** `hub_agree` is `1` / `0` / **NULL**. A
comparison against an unknown is neither agreement nor disagreement; collapsing NULL into `0` would
have reported a 16% "RMFG deviation" rate that is entirely our own coverage gap.

**10. NEVER report ONE blended tag-vs-scan rate.** Tier-2 facilities were derived FROM the tags, so
their agreement is partly circular. The headline number is the **tier-1** rate over facilities
mapped from `ShipRouting/lib/hubs.py` alone ([[count-only-independent-checks]]).

## Parsing

**11. `US` is NOT a state, and a sentence fragment is not a city.**
Without a negative lookahead, *"…sent to OnTrac, but we have yet to receive the package, US"* parses
as city=`BUT WE HAVE YET TO RECEIVE THE PACKAGE`, state=`US`. One live row DID leak through as the
facility `SEE ESTIMATED DELIVERY DATE, US`. The `MOVEMENT` gate hides most instances, which is
exactly why the parser must not lean on the gate for correctness. City is capped at 4 words.

**12. THREE carrier scan dialects. A regex that handles one is not a hit rate, it is a sample.**
| carrier | dialect | example |
|---|---|---|
| OnTrac/LaserShip | `…, CITY, ST ZIP US` | `…Estimated Delivery Date, BRIDGEPORT, NJ 08014 US` |
| FedEx | `…, CITY ST ZIP` — **no comma** | `Picked up, BARRINGTON NJ 08007` |
| UPS | `…, City ST US` — **no ZIP, ever** | `Arrived at Facility, Mesquite TX US` |
The first regex here required both the comma and the zip, matched OnTrac only, and reported **67%**
— which was really "we parsed one carrier out of three". UPS resolves by `(city, state)` alone.

**13. zip5 is TEXT.** `08007` is not `8007`, `08014` is not `8014` ([[zip-integrity-family]]).
Applies to `origin_scan_zip`, `origin_label_zip`, and `dest_zip5` (truncated from PP's ZIP+4).

**14. ALL date math in America/New_York BEFORE any `.date()`.** Skipping this doubled the late rate
once already (146 vs 62). PP sends BOTH offset-bearing (`2026-08-24T05:44:49-04:00`) and naive
(`2026-08-26T15:57:00`) timestamps. Offset-bearing → converted. Naive → treated as **already ET**,
corroborated by the naive `pickup_date` equalling the naive first-scan `checkpoint_time` on
**2,692 / 2,692** payloads. 🔴 If PP ever sends naive UTC, that assumption shifts every date by up
to 4h — the control below is what would catch it, so never delete it.

## Source discipline

**15. Newest event per order, `state='landed'` only, `order_number` only.**
PP resends the FULL checkpoint history on every notification, so the newest payload is a superset;
`MIN(id)` would read a payload written before the box was scanned. Quarantined rows are evidence and
never derive (`pp_webhook.land`). Tracking-only rows never derive — FedEx REUSES tracking numbers
(STATUS_INGEST_RULES rule 1).

**16. An empty pull must NEVER blank the table.** The builder refuses (`FLAG`, rc=1) and leaves the
local table untouched, exactly like `pull_cloud_replicas`.

**17. NEVER `sqlite3.connect()` shipping.db.** Writers go through `appyhour_lib.db.connect()` (WAL +
busy_timeout + single-writer lock). The DB corrupted three times in one week without it.

---

# The control — how we know the extraction found the RIGHT checkpoint

🔴 **A hit rate alone is a claim.** "We parsed a city on 99.9% of payloads" does not say we parsed
the *injection* city. The control is independent and known-present:

> **The time of the first physical checkpoint we select must equal ParcelPanel's own `pickup_date`.**
> Measured **2,691 / 2,692 = 100.0%**.

`pickup_date` is a genuine carrier pickup verified across 2,324 boxes (STATUS_INGEST_RULES rule 16)
and is computed by ParcelPanel independently of our checkpoint selection. It agreeing to the minute
is what upgrades "we found a checkpoint" to "we found the pickup". The builder prints this on every
run; **a drop here invalidates every origin_hub in the table** and must be treated as a hard stop,
not a warning.

---

# Measured results (2026-08-20 → 2026-08-27, 2,694 orders)

| metric | value |
|---|---|
| first physical scan found | **2,692 / 2,694 = 99.9%** (the 2 misses are the never-picked-up class) |
| assigned-hub routing tag parsed | 2,604 / 2,694 = 96.7% (90 are untagged or fence-only) |
| `origin_hub` MAPPED (not MISSING) | **2,323 / 2,694 = 86.2%** |
| control: first scan == PP `pickup_date` | **2,691 / 2,692 = 100.0%** |
| carrier canonicalised | OnTrac 1,806 (PP said LaserShip) · FedEx 860 · UPS 28 |

**🔴 TAG-vs-SCAN DISAGREEMENT — the headline finding**

| population | comparable | disagree | rate |
|---|---:|---:|---:|
| **tier-1 (independent of our tags)** | **906** | **0** | **0.00%** |
| tier-2 (derived from tags — circular) | 1,335 | 2 | 0.15% |
| all mapped | 2,241 | 2 | 0.09% |

**0.00% on 906 independently-mapped boxes corroborates Kurt's hand audit: RMFG follows our routing
tags.** The two tier-2 exceptions are named, not swept:
- `#163811` — tag `!ANY FedEx - Anaheim_AHB!`, scanned OnTrac at DESOTO TX (Dallas).
- `#175766` — tag `!OnTrac Ground - Nashville_AHB!`, scanned OnTrac at BRIDGEPORT NJ (Swedesboro).

Both are carrier AND hub mismatches, i.e. the box was produced at a different hub than tagged — not
a service substitution. They are worth a look, not a systemic conclusion.

---

# The facility map — three tiers, and why the tiers exist

## Tier 1 `scan_authority_zip` — INDEPENDENT of our tags
The facility zip appears **verbatim** in `ShipRouting/lib/hubs.py`. Pinned by
`test_authority_zips_match_shiprouting_hub_roster`, which fails if that roster moves.

| zip | facility | hub | authority |
|---|---|---|---|
| `75149` | Mesquite TX | Dallas | `HUB_ORIGIN_ZIP["Dallas"]` |
| `37122` | Mount Juliet TN | Nashville | `HUB_ORIGIN_ZIP["Nashville"]` |
| `08007` | Barrington NJ | Swedesboro | `HUB_ORIGIN_ZIP["Swedesboro"]` |
| `60446` | Romeoville IL | Chicago | `HUB_ONTRAC_ZIP["Chicago"]` |

Plus the zip-less UPS dialect: `("MESQUITE","TX") → Dallas` (same facility, no zip in UPS text).

## Tier 2 `scan_derived_facility` — clustered, NOT independent
Each is ≥99.6% concentrated on one hub over ≥240 observations AND is the carrier's own
ORIGIN-handoff scan (*"…on its way to your OnTrac Facility…"*).

| zip | facility | hub | concentration |
|---|---|---|---|
| `08014` | Bridgeport NJ | Swedesboro | 549/550 (99.8%) |
| `90040` | Los Angeles CA | Anaheim | 307/307 (100%) |
| `37090` | Lebanon TN | Nashville | 245/245 (100%) |
| `75115` | DeSoto TX | Dallas | 232/233 (99.6%) |

🔴 These exist **only because `HUB_ONTRAC_ZIP` carries just Swedesboro and Chicago.** The OnTrac
injection zips for Anaheim / Nashville / Dallas are an **AUTHORITY GAP**, not a fact this module may
invent — see "Open for Kurt". The moment Kurt confirms them they move to tier 1 and the headline
disagreement denominator roughly doubles.

## Tier 3 — REFUSED and MISSING
| facility | orders | why refused |
|---|---:|---|
| `WILMINGTON, MA 01887` | 125 | FedEx shipper-ACCOUNT address (Woburn HQ), not a facility — gotcha 8 |
| `SANTA FE SPRINGS, CA 90670` | 34 | only 65% on one hub; FedEx's own origin zip splits 90660/60445 |
| `SALT LAKE CITY, UT 84104` | 8 | `HUB_ORIGIN_ZIP` PLACEHOLDER for a hub with no volume; these are Anaheim-tagged boxes at a DESTINATION-side facility |
| 43 OnTrac tail facilities (Denver CO, Phoenix AZ, Milpitas CA, Lockbourne OH …) | 202 | the payload never carried an injection scan — its first physical scan is the DESTINATION-local facility. Not a hub; correctly MISSING |
| no physical scan | 2 | never-picked-up class; absence is the answer |

---

# 🔴 OPEN FOR KURT — do not answer by assumption

1. **The three missing OnTrac injection zips.** Evidence says Bridgeport NJ `08014` → Swedesboro,
   Los Angeles CA `90040` → Anaheim, Lebanon TN `37090` → Nashville, DeSoto TX `75115` → Dallas.
   Confirm and they become tier-1 authority (and belong in `ShipRouting/lib/hubs.HUB_ONTRAC_ZIP`,
   which is Routing Coordinator's surface, not ours).

2. **The FedEx label-origin zips are not in any authority file.** Recorded raw, never mapped:

   | zip | assigned-hub tags on those orders | matches an authority? |
   |---|---|---|
   | `37090` | Nashville 242 | — (same as OnTrac Nashville injection) |
   | `08085` | Swedesboro 149 | ✅ `HUB_ONTRAC_ZIP["Swedesboro"]` |
   | `75042` | Dallas 114 | ✅ ShippingReports "Dallas — Garland TX 75042" |
   | `60445` | Chicago 129 | ❌ (`HUB_ORIGIN_ZIP["Chicago"]` is 60638) |
   | `90660` | Anaheim 22 | ❌ (`HUB_ORIGIN_ZIP["Anaheim"]` is 92801) |

   🔴 **Chicago and Anaheim's documented FedEx quote origins do not match where RMFG's FedEx labels
   actually originate.** That is a routing-input question (quotes priced from the wrong origin),
   above this table's pay grade — flagged, not fixed.

3. **Scheduled owner.** Currently **UNOWNED** — see below.

---

# Ownership, cadence, freshness

| | |
|---|---|
| pure logic | `AppyHour/appyhour_lib/pp_origin.py` (stdlib only; `canon` injected) |
| builder | `AppyHour/ShippingReports/build_pp_origin_hub.py` (cloud MySQL READ-ONLY → local sqlite) |
| table | `pp_origin_hub` in `C:\AppyHourData\shipping.db`, PK `order_number`, full refresh |
| tests | `AppyHour/tests/test_pp_origin.py` (29) |
| **scheduled owner** | 🔴 **NONE — UNOWNED.** Every run so far is manual. |
| **freshness assert** | 🔴 **NONE.** |

🔴 **This table is NOT SHIPPED under the writer-ownership gate** (`~/.claude/rules/
feature-constraints-doc.md`): a data writer with no scheduled owner and no freshness assert is the
dead-cadence class that has already burned this operation four times. Until Kurt approves the two
patches below, treat `pp_origin_hub` as an **on-demand derivation**, and any consumer must check
`derived_at` itself rather than assume freshness.

Proposed pair, both one-liners, both **awaiting Kurt** (adding a writer to a live scheduled job is a
standing-configuration change):

1. `_outputs/scripts/freshness_sweep.py::main()` — a stage after the `pull_cloud_replicas` block,
   following the `db_invariants_check` pattern, running `build_pp_origin_hub.py --apply`.
2. `_outputs/scripts/freshness_sweep.py::TABLE_CHECKS` —
   `("pp_origin_hub", "derived_at", 8, "PP-native origin hub; stale = the Monday derive stopped")`.

Add them **together**: the assert alone FLAGs every Monday on a table nobody refreshes.

🔴 **DATA_CANON declaration is REQUIRED and is NOT ours to write.** `db_invariants_check.py` check A
FLAGs any undeclared table, so the first Monday sweep after this table exists will FLAG until
`ShipRouting/server/DATA_CANON_RULES.md` gains:

```yaml
- table: pp_origin_hub
  store: sqlite (derived)
  grain: >
    one ORDER per ship leg — its scan-derived origin hub, canonical carrier, and final-mile clock,
    derived from the newest landed pp_webhook_events payload for that order
  business_key: [order_number]
  key_enforced: true
  never_empty: true
  provenance: AppyHour/ShippingReports/build_pp_origin_hub.py
```

That file is Routing Coordinator's surface. **Hand it over; do not edit it from here.**

---

## Related

`ShipRouting/server/STATUS_INGEST_RULES.md` (rules 1, 9, 16, 19, 20, 25, 28) ·
`ShipRouting/server/pp_webhook.py` (`MOVEMENT`, the raw landing contract) ·
`ShipRouting/server/DATA_CANON_RULES.md` (declaration, check A) ·
`ShipRouting/lib/hubs.py` (`HUB_ORIGIN_ZIP` / `HUB_ONTRAC_ZIP` — the tier-1 authority) ·
`ShipRouting/lib/canon.py` (`normalize_carrier`, `parse_routing_tag`) ·
`AppyHour/ShippingReports/RESHIP_REPORT_RULES.md` **D35** (Carrier Mix pivot — the first consumer) ·
`AppyHour/ShippingReports/carrier_mix_pivot.py` (`build_column`, the integration point).
