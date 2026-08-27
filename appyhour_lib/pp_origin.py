"""PP-native ORIGIN-HUB derivation — pure logic over one ParcelPanel webhook payload.

🔴 CONSTRAINTS SSOT: ``AppyHour/PP_ORIGIN_HUB_RULES.md``. Read it before changing anything here.
Gotchas first, because every one of them cost a measurement pass:

1. **NEVER take ``checkpoints[0]``.** The array is neither ascending nor descending — measured
   NEITHER on **2,694 / 2,694** payloads (2026-08-27). It is carrier scans newest-first followed by
   PP/store status lines in their own order. Sort by ``checkpoint_time`` yourself, always.
2. **NEVER treat the earliest checkpoint as a carrier scan.** The first 2–3 entries are PP/store
   status lines with no location ("Orders are prepared fresh weekly…", "Order Ready", "The package
   data was sent to OnTrac, but we have yet to receive the package…"). A naive
   ``checkpoints[0]`` + city regex returns **0 hits on 6,000/6,000 payloads** — the Routing
   Coordinator measured exactly that. The gate here is the checkpoint's own ``status``:
   only :data:`MOVEMENT` statuses are physical scans, which excludes ``INFO_RECEIVED`` and the
   ``status: null`` store lines by construction rather than by text blacklist.
3. **NEVER key on PP's raw carrier name.** PP says **LaserShip** where the scan text says OnTrac —
   1,806 of 2,694 payloads. Everything goes through ``canon.normalize_carrier`` (LaserShip → OnTrac,
   STATUS_INGEST_RULES rule 19). That is why :func:`derive_origin` REQUIRES the ``canon`` module as
   a keyword argument: you cannot call it without supplying the canonicaliser.
4. **NEVER hand-roll a routing-tag regex.** ``canon.parse_routing_tag`` is the only correct reader —
   a naive ``'Dallas_AHB!' in tags`` also matches the ``!NO … - Dallas_AHB!`` EXCLUSION block. The
   first version of this derivation used its own regex with ``\\b`` after the hub name and returned
   **0 hub tags on all 2,694 payloads**, because ``_`` in ``Swedesboro_AHB`` is a word character and
   killed the boundary. A zero is a claim; that one was false.
5. **NEVER build on ``pickup_location``.** The key exists and is NULL on **2,694 / 2,694** payloads
   (independently re-verified here 2026-08-27, not taken on report).
6. **NEVER use ``location.name``** — it is the SHIPPER ("RMFG" 2,690 / "COG" 4), not the hub.
7. **NEVER map a scan city to a hub on tag correlation alone.** ``WILMINGTON, MA 01887`` clusters
   97.6% onto Chicago-tagged boxes, and mapping it would be fabrication: Woburn MA is HQ, not a hub
   (ShippingReports/CLAUDE.md), and FedEx stamps that "Picked up" scan with the shipper ACCOUNT
   address. Its own pre-pickup line says ``Shipment information sent to FedEx, 60445`` — a Chicago
   origin zip. Unmappable facilities return :data:`MISSING`, never a guess.
8. **NEVER ``.date()`` a timestamp before converting to America/New_York.** Skipping this doubled
   the late rate once already (146 vs 62). ``transit_days`` is CALENDAR days between ET dates.

The whole point of this module is that a COMPLETE lane — carrier, origin hub, destination, pickup,
delivery, transit — comes out of events we have ALREADY ingested. It makes **zero** ParcelPanel API
calls and must keep making zero (DO is the canonical ingester).
"""
from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

MISSING = "MISSING"

__all__ = [
    "ET", "MISSING", "MOVEMENT", "AUTHORITY_FACILITY_ZIP", "DERIVED_FACILITY_ZIP",
    "REFUSED_FACILITY_ZIP", "FACILITY_CITY_STATE", "HUB_SOURCES",
    "iter_checkpoints", "first_physical_checkpoint", "parse_scan_location",
    "label_origin_zip", "dest_zip5", "to_et", "transit_days", "hub_for_facility",
    "derive_origin",
]

# 🔴 The movement allow-list, mirroring ``ShipRouting/server/pp_webhook.MOVEMENT`` (STATUS_INGEST
# rule 9). It is RESTATED rather than imported because appyhour_lib is a stdlib-only leaf and must
# not import the ShipRouting server package. Its use here is narrower than pp_webhook's — "is this
# checkpoint a physical carrier scan?" — and it is pinned by
# ``tests/test_pp_origin.py::test_movement_matches_pp_webhook``, which imports BOTH and asserts
# equality, so the restatement cannot silently drift.
MOVEMENT = frozenset({"IN_TRANSIT", "OUT_FOR_DELIVERY", "ATTEMPTED_DELIVERY",
                      "PICKED_UP", "DELIVERED"})

# ── Scan-location grammar ─────────────────────────────────────────────────────────────────────────
# THREE carrier dialects, all tail-anchored (the location is always the end of ``detail``):
#   OnTrac/LaserShip : "…see Estimated Delivery Date, BRIDGEPORT, NJ 08014 US"   city, ST zip US
#   FedEx            : "Picked up, BARRINGTON NJ 08007"                          city ST zip
#   UPS              : "Arrived at Facility, Mesquite TX US"                     city ST (NO ZIP)
# 🔴 The comma between city and state is OPTIONAL and the zip is OPTIONAL. The first version of this
# regex required both, matched only OnTrac, and reported a 67% hit rate that was really "we parsed
# one carrier out of three". UPS carries NO zip at all in any of its 371 checkpoint details.
# 🔴 `US` is NOT a state. Without the negative lookahead, "…sent to OnTrac, but we have yet to
# receive the package, US" parses as city="BUT WE HAVE YET TO RECEIVE THE PACKAGE", state="US" —
# and one live row DID leak through as the facility "SEE ESTIMATED DELIVERY DATE, US". The
# MOVEMENT gate hides most of these (that text is INFO_RECEIVED), which is exactly why the parser
# must not rely on the gate to be correct.
# 🔴 The city is capped at 4 words for the same reason: a sentence fragment is not a city.
_SCAN_LOC_RE = re.compile(
    r",\s*(?P<city>[A-Za-z][A-Za-z.'\-]*(?:\s+[A-Za-z][A-Za-z.'\-]*){0,3}),?"
    r"\s+(?!US\b)(?P<state>[A-Z]{2})\b"
    r"(?:\s+(?P<zip>\d{5})(?:-\d{4})?)?"
    r"(?:\s+US)?\s*$")

# The PRE-pickup label line, FedEx only: "Shipment information sent to FedEx, 60445".
# 🔴 This is NOT a scan and must never be selected as the first physical checkpoint (its status is
# INFO_RECEIVED, so the MOVEMENT gate already excludes it). It is captured separately because it
# carries the SHIPPER's declared origin zip, which is the only signal that explains the Wilmington
# MA rows. It is recorded RAW and never mapped to a hub — see PP_ORIGIN_HUB_RULES "open for Kurt".
_LABEL_ORIGIN_RE = re.compile(
    r"^[^,]*information (?:sent|has been sent) to [A-Za-z]+,\s*(?P<zip>\d{5})\s*$", re.I)

# ── Injection-facility → hub ──────────────────────────────────────────────────────────────────────
# TIER 1 "authority": the facility zip appears VERBATIM in ShipRouting/lib/hubs.py. These mappings
# are INDEPENDENT of our routing tags, which is what makes the tag-vs-scan disagreement rate
# computed over them a real measurement rather than a tautology.
AUTHORITY_FACILITY_ZIP = {
    "75149": "Dallas",       # hubs.HUB_ORIGIN_ZIP["Dallas"]      — Mesquite TX   (FedEx + UPS)
    "37122": "Nashville",    # hubs.HUB_ORIGIN_ZIP["Nashville"]   — Mount Juliet TN (FedEx)
    "08007": "Swedesboro",   # hubs.HUB_ORIGIN_ZIP["Swedesboro"]  — Barrington NJ (FedEx)
    "60446": "Chicago",      # hubs.HUB_ONTRAC_ZIP["Chicago"]     — Romeoville IL (OnTrac)
}

# TIER 2 "derived": clustered from first-scan city × assigned-hub tag over the 2026-08-20→08-27
# window (2,694 orders). Each is ≥99.6% concentrated on ONE hub across ≥240 observations, and each
# is the carrier's own ORIGIN-handoff scan ("…on its way to your OnTrac Facility…").
# 🔴 These are NOT independent of the routing tag — they were derived FROM it. Any agreement
# statistic over these rows is circular and must be reported separately from the tier-1 number
# ([[count-only-independent-checks]]). They exist because ShipRouting's HUB_ONTRAC_ZIP carries only
# Swedesboro + Chicago; the OnTrac injection zips for Anaheim / Nashville / Dallas are an AUTHORITY
# GAP (PP_ORIGIN_HUB_RULES "open for Kurt"), not a fact this module may invent.
DERIVED_FACILITY_ZIP = {
    "08014": "Swedesboro",   # OnTrac Bridgeport NJ    549/550 Swedesboro-tagged (99.8%)
    "90040": "Anaheim",      # OnTrac Los Angeles CA   307/307 Anaheim-tagged   (100%)
    "37090": "Nashville",    # OnTrac Lebanon TN       245/245 Nashville-tagged (100%)
    "75115": "Dallas",       # OnTrac DeSoto TX        232/233 Dallas-tagged    (99.6%)
}

# 🔴 REFUSED — observed at volume, deliberately NOT mapped. Listed so the next person does not
# "finish the map" by pattern-matching. Each returns MISSING with the reason as hub_source.
REFUSED_FACILITY_ZIP = {
    # FedEx stamps its synthetic midnight "Picked up" scan with the shipper ACCOUNT address.
    # Woburn MA is HQ, not a hub. The same payloads declare origin zip 60445 (Chicago).
    "01887": "refused_shipper_account_address",
    # 34 orders, only 65% on one hub (Anaheim 22 / Chicago 7 / Dallas 5) — below any threshold that
    # is not just tag-echo. FedEx's own declared origin zip splits 90660 vs 60445 on the same city.
    "90670": "refused_ambiguous_facility",
    # hubs.HUB_ORIGIN_ZIP["Salt Lake City"] is an explicit PLACEHOLDER for a hub with no cohort
    # volume; the 8 rows scanning here are Anaheim-tagged OnTrac boxes hitting a DESTINATION-side
    # facility. Mapping it would invent a Salt Lake City hub out of downstream scans.
    "84104": "refused_placeholder_zip",
}

# Zip-less dialect (UPS). Only (city, state) pairs whose hub is UNAMBIGUOUS across the tables above.
FACILITY_CITY_STATE = {
    ("MESQUITE", "TX"): ("Dallas", "scan_authority_zip"),   # UPS "Arrived at Facility, Mesquite TX US"
}

HUB_SOURCES = (
    "scan_authority_zip",        # tier 1 — facility zip is in ShipRouting/lib/hubs.py
    "scan_derived_facility",     # tier 2 — clustered facility, tag-correlated (NOT independent)
    "no_physical_scan",          # no MOVEMENT checkpoint carried a location (never-collected class)
    "unmapped_facility",         # a real scan at a facility we cannot map — MISSING, listed for Kurt
    "refused_shipper_account_address",
    "refused_ambiguous_facility",
    "refused_placeholder_zip",
)


# ── Checkpoint access ─────────────────────────────────────────────────────────────────────────────
def iter_checkpoints(payload):
    """Checkpoints sorted ASCENDING by ``checkpoint_time`` — the ONLY sanctioned access order.

    🔴 Do not "optimise" this into ``payload['checkpoints']`` or a reversal. The raw array was
    measured NEITHER ascending nor descending on 2,694/2,694 payloads; any assumption about its
    order is wrong for every payload we have. Entries without a ``checkpoint_time`` are dropped —
    an undated checkpoint cannot be ordered, and "first" is only meaningful in time.
    """
    cps = [c for c in (payload.get("checkpoints") or [])
           if isinstance(c, dict) and c.get("checkpoint_time")]
    return sorted(cps, key=lambda c: str(c["checkpoint_time"]))


def parse_scan_location(detail):
    """``detail`` text → ``(CITY, ST, zip5|None)``, or None when it carries no location.

    zip5 is returned as **TEXT** and may be None (UPS never sends one). 🔴 Never int() it —
    leading zeros are real (``08007`` Barrington NJ, ``08014`` Bridgeport NJ).
    """
    m = _SCAN_LOC_RE.search(str(detail or "").strip())
    if not m:
        return None
    return (m.group("city").upper().strip(), m.group("state").upper(), m.group("zip"))


def first_physical_checkpoint(payload):
    """Earliest checkpoint that is a CARRIER SCAN carrying a location, or None.

    "Physical scan" = ``status`` in :data:`MOVEMENT` **and** ``detail`` parses to a location. The
    status gate is what skips the PP/store status lines (gotcha 2); the location requirement is what
    skips a movement scan that happens to be textless.

    Returns ``{'city','state','zip','at','status','detail'}``. Measured hit rate **2,692 / 2,694
    (99.9%)**; the 2 misses are boxes with no carrier scan at all (the never-picked-up class, where
    absence is the correct answer — see STATUS_INGEST_RULES rule 25).
    """
    for cp in iter_checkpoints(payload):
        if str(cp.get("status") or "").strip().upper() not in MOVEMENT:
            continue
        loc = parse_scan_location(cp.get("detail"))
        if loc:
            return {"city": loc[0], "state": loc[1], "zip": loc[2],
                    "at": str(cp["checkpoint_time"]), "status": cp.get("status"),
                    "detail": cp.get("detail")}
    return None


def label_origin_zip(payload):
    """The shipper's declared origin zip from the PRE-pickup label line, or None (FedEx only).

    Present on 695/860 FedEx payloads and on ZERO OnTrac/UPS payloads. Recorded RAW: it is the
    evidence that resolves the Wilmington MA rows, but it is deliberately NOT mapped to a hub here —
    three of its five observed values (60445, 90660, 75042) appear in no authority file.
    """
    for cp in iter_checkpoints(payload):
        if str(cp.get("status") or "").strip().upper() in MOVEMENT:
            continue
        m = _LABEL_ORIGIN_RE.match(str(cp.get("detail") or "").strip())
        if m:
            return m.group("zip")
    return None


def dest_zip5(payload):
    """Destination zip5 as **TEXT**, truncated from PP's ZIP+4. None when absent.

    🔴 TEXT, never int — ``08014`` is not ``8014`` ([[zip-integrity-family]]).
    """
    raw = ((payload.get("shipping_address") or {}).get("zip") or "")
    m = re.match(r"\s*(\d{5})", str(raw))
    return m.group(1) if m else None


# ── Time ──────────────────────────────────────────────────────────────────────────────────────────
def to_et(value):
    """PP timestamp → timezone-aware datetime in America/New_York, or None.

    🔴 PP sends BOTH shapes: offset-bearing (``2026-08-24T05:44:49-04:00``) and naive
    (``2026-08-26T15:57:00``). An offset-bearing value is CONVERTED to ET. A naive value is treated
    as ALREADY ET — that is PP's store-timezone presentation, corroborated by the naive
    ``pickup_date`` matching the naive ``checkpoint_time`` of the first physical scan on
    **2,692 / 2,692** payloads. 🔴 If PP ever starts sending naive UTC, that assumption silently
    shifts every date by up to 4h; the control above is the test that would catch it, and it is
    pinned in the builder's report.
    """
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt.astimezone(ET) if dt.tzinfo is not None else dt.replace(tzinfo=ET)


def transit_days(pickup, delivered):
    """CALENDAR days between the ET **dates** of pickup and delivery, or None.

    🔴 ``.date()`` is taken only AFTER the ET conversion (gotcha 8), and this is a CALENDAR-day
    count, never PP's own ``transit_time`` integer — TNT is delivery minus PICKUP SCAN, final-mile
    only (ShippingReports/CLAUDE.md "TNT calc HARD RULE"; STATUS_INGEST_RULES rule 16).
    """
    p, d = to_et(pickup), to_et(delivered)
    if p is None or d is None:
        return None
    return (d.date() - p.date()).days


# ── Hub resolution ────────────────────────────────────────────────────────────────────────────────
def hub_for_facility(city, state, zip5):
    """``(city, state, zip5)`` → ``(hub_or_MISSING, hub_source)``. Never guesses.

    Resolution order — zip first because it is the precise key, then the zip-less UPS dialect:
      1. tier-1 authority zip   → ``scan_authority_zip``
      2. tier-2 derived zip     → ``scan_derived_facility``
      3. explicitly REFUSED zip → ``MISSING`` + the refusal reason
      4. (city, state) fallback → only for unambiguous zip-less pairs
      5. anything else          → ``MISSING`` + ``unmapped_facility``
    """
    if zip5:
        z = str(zip5).strip()
        if z in AUTHORITY_FACILITY_ZIP:
            return AUTHORITY_FACILITY_ZIP[z], "scan_authority_zip"
        if z in DERIVED_FACILITY_ZIP:
            return DERIVED_FACILITY_ZIP[z], "scan_derived_facility"
        if z in REFUSED_FACILITY_ZIP:
            return MISSING, REFUSED_FACILITY_ZIP[z]
        return MISSING, "unmapped_facility"
    key = (str(city or "").upper().strip(), str(state or "").upper().strip())
    if key in FACILITY_CITY_STATE:
        hub, src = FACILITY_CITY_STATE[key]
        return hub, src
    return MISSING, "unmapped_facility"


def derive_origin(payload, *, canon):
    """ONE ParcelPanel webhook payload → one derived lane row.

    ``canon`` is the ``ShipRouting.lib.canon`` module and is REQUIRED, keyword-only, by design: it
    supplies ``normalize_carrier`` (LaserShip → OnTrac) and ``parse_routing_tag`` (the only correct
    reader of our routing tags). Making it un-defaultable is how gotchas 3 and 4 are enforced
    structurally rather than by comment.

    Returns a dict with, among others: ``carrier`` (canonical), ``origin_hub`` (scan-derived, or
    ``MISSING``), ``hub_source``, ``assigned_hub`` (from ``order_tags``), ``dest_zip5`` (TEXT),
    ``pickup_at`` / ``delivered_at`` (ET ISO), ``transit_days`` (calendar, ET),
    ``first_physical_checkpoint_at``, and ``hub_agree`` (None when either side is unknown — a
    comparison against an unknown is not an agreement AND not a disagreement).
    """
    scan = first_physical_checkpoint(payload)
    if scan is None:
        origin_hub, hub_source = MISSING, "no_physical_scan"
    else:
        origin_hub, hub_source = hub_for_facility(scan["city"], scan["state"], scan["zip"])

    tags = payload.get("order_tags") or []
    if isinstance(tags, str):
        tags_s = tags
    else:
        tags_s = ", ".join(str(t) for t in tags)
    parsed = canon.parse_routing_tag(tags_s)
    assigned_hub = (parsed or {}).get("hub")
    assigned_tag = (parsed or {}).get("raw")

    pickup = to_et(payload.get("pickup_date"))
    delivered = to_et(payload.get("delivery_date"))

    order_number = str(payload.get("order_number") or "").strip().lstrip("#") or None

    # 🔴 None, not False, when either side is unknown. A row with no scan hub and a row that
    # genuinely disagrees must never land in the same bucket — that is how a MISSING population
    # silently inflates a disagreement rate.
    if origin_hub == MISSING or not assigned_hub:
        hub_agree = None
    else:
        hub_agree = (origin_hub == assigned_hub)

    return {
        "order_number": order_number,
        "tracking": (str(payload.get("tracking_number")).strip()
                     if payload.get("tracking_number") else None),
        "carrier": canon.normalize_carrier((payload.get("carrier") or {}).get("name")),
        "carrier_raw": (payload.get("carrier") or {}).get("name"),
        "pp_status": payload.get("status"),
        "origin_hub": origin_hub,
        "hub_source": hub_source,
        "origin_scan_city": scan["city"] if scan else None,
        "origin_scan_state": scan["state"] if scan else None,
        "origin_scan_zip": scan["zip"] if scan else None,
        "origin_label_zip": label_origin_zip(payload),
        "first_physical_checkpoint_at": scan["at"] if scan else None,
        "assigned_hub": assigned_hub,
        "assigned_tag": assigned_tag,
        "hub_agree": hub_agree,
        "dest_zip5": dest_zip5(payload),
        "pickup_at": pickup.isoformat() if pickup else None,
        "delivered_at": delivered.isoformat() if delivered else None,
        "transit_days": transit_days(payload.get("pickup_date"), payload.get("delivery_date")),
    }
