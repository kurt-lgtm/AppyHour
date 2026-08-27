"""Tests for appyhour_lib.pp_origin — the PP-native origin-hub derivation.

Every test here pins a MEASURED failure, not a happy path. The fixtures are real payload shapes
taken verbatim from `pp_webhook_events` on 2026-08-27 (identifying fields trimmed).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SR_LIB = str(Path(r"C:/Users/Work/Claude Projects/ShipRouting/lib"))
if _SR_LIB not in sys.path:
    sys.path.insert(0, _SR_LIB)
import canon  # noqa: E402

from appyhour_lib import pp_origin  # noqa: E402


# ── fixtures (verbatim shapes) ────────────────────────────────────────────────────────────────────
def _ontrac_payload():
    """OnTrac/LaserShip: 'CITY, ST ZIP US', and the checkpoint array in NEITHER time order."""
    return {
        "order_number": "#175458",
        "order_tags": ["Subscription First Order", "!ExtraGel48oz!", "_SHIP_2026-08-24",
                       "!ANY - Swedesboro_AHB!"],
        "shipping_address": {"zip": "11101-4820", "province_code": "NY"},
        "carrier": {"name": "LaserShip", "code": "lasership"},
        "status": "OUT_FOR_DELIVERY",
        "pickup_date": "2026-08-26T15:57:00",
        "delivery_date": "2026-08-28T09:10:00",
        "pickup_location": None,
        "location": {"name": "RMFG"},
        "transit_time": 2,
        "checkpoints": [
            {"detail": "Out for Delivery, MASPETH, NY 11378 US", "status": "OUT_FOR_DELIVERY",
             "checkpoint_time": "2026-08-27T09:42:00"},
            {"detail": "Package received by your local OnTrac facility and is processing for "
                       "delivery, see Estimated Delivery Date, MASPETH, NY 11378 US",
             "status": "IN_TRANSIT", "checkpoint_time": "2026-08-27T01:51:00"},
            {"detail": "Your package has been received and is on its way to your OnTrac Facility, "
                       "see Estimated Delivery Date, BRIDGEPORT, NJ 08014 US",
             "status": "IN_TRANSIT", "checkpoint_time": "2026-08-26T15:57:00"},
            {"detail": "The package data was sent to OnTrac, but we have yet to receive the "
                       "package from the sender. Please contact the sender for more "
                       "information, US",
             "status": "INFO_RECEIVED", "checkpoint_time": "2026-08-22T02:12:00"},
            {"detail": "Order Ready", "status": None, "checkpoint_time": "2026-08-24T05:44:49"},
            {"detail": "Orders are prepared fresh weekly. Your box is in queue to be prepared "
                       "and shipped.", "status": None, "checkpoint_time": "2026-08-21T14:51:18"},
        ],
    }


def _fedex_wilmington_payload():
    """FedEx: 'CITY ST ZIP' (NO comma), synthetic midnight pickup stamped with the shipper
    ACCOUNT address (Wilmington MA), and the real origin only in the pre-pickup label line."""
    return {
        "order_number": "#172302",
        "order_tags": ["!ANY FedEx - Chicago_AHB!", "_SHIP_2026-08-17"],
        "shipping_address": {"zip": "66018-9008", "province_code": "KS"},
        "carrier": {"name": "FedEx"},
        "status": "DELIVERED",
        "pickup_date": "2026-08-17T00:00:00",
        "delivery_date": "2026-08-20T16:04:28",
        "checkpoints": [
            {"detail": "Orders are prepared fresh weekly.", "status": None,
             "checkpoint_time": "2026-08-13T01:06:14"},
            {"detail": "Shipment information sent to FedEx, 60445", "status": "INFO_RECEIVED",
             "checkpoint_time": "2026-08-14T21:47:00"},
            {"detail": "Picked up, WILMINGTON MA 01887", "status": "IN_TRANSIT",
             "checkpoint_time": "2026-08-17T00:00:00"},
            {"detail": "Order Ready", "status": None, "checkpoint_time": "2026-08-17T05:10:53"},
            {"detail": "Arrived at FedEx location, CHICAGO IL 60638", "status": "IN_TRANSIT",
             "checkpoint_time": "2026-08-17T19:11:00"},
        ],
    }


def _ups_payload():
    """UPS: 'City ST US' — NO ZIP in any checkpoint detail, ever."""
    return {
        "order_number": "#173081",
        "order_tags": ["!UPS Ground - Dallas_AHB!"],
        "shipping_address": {"zip": "65733", "province_code": "MO"},
        "carrier": {"name": "UPS"},
        "status": "DELIVERED",
        "pickup_date": "2026-08-17T19:45:54",
        "delivery_date": "2026-08-20T15:00:49",
        "checkpoints": [
            {"detail": "Shipper created a label, UPS has not received the package yet, US",
             "status": "INFO_RECEIVED", "checkpoint_time": "2026-08-14T21:33:02"},
            {"detail": "Order Ready", "status": None, "checkpoint_time": "2026-08-17T05:21:30"},
            {"detail": "Arrived at Facility, Mesquite TX US", "status": "IN_TRANSIT",
             "checkpoint_time": "2026-08-17T19:45:54"},
            {"detail": "Arrived at Facility, Ft Worth TX US", "status": "IN_TRANSIT",
             "checkpoint_time": "2026-08-18T00:08:00"},
        ],
    }


# ── the checkpoints[0] trap ───────────────────────────────────────────────────────────────────────
def test_checkpoints_are_not_time_ordered_as_received():
    """🔴 The measured fact behind gotcha 1: NEITHER ascending nor descending, on every payload."""
    raw = [c["checkpoint_time"] for c in _ontrac_payload()["checkpoints"]]
    assert raw != sorted(raw)
    assert raw != sorted(raw, reverse=True)


def test_iter_checkpoints_sorts_ascending():
    ts = [c["checkpoint_time"] for c in pp_origin.iter_checkpoints(_ontrac_payload())]
    assert ts == sorted(ts)


def test_naive_checkpoints_zero_returns_no_location():
    """The trap that cost a whole pass: checkpoints[0] is a PP/store status line, so a naive
    'first checkpoint + city regex' returns nothing on payload after payload."""
    p = _ontrac_payload()
    assert pp_origin.parse_scan_location(p["checkpoints"][0]["detail"]) is not None  # sanity
    earliest = pp_origin.iter_checkpoints(p)[0]
    assert earliest["status"] is None
    assert pp_origin.parse_scan_location(earliest["detail"]) is None


def test_pre_pickup_status_lines_are_never_the_first_physical_scan():
    """INFO_RECEIVED and status-less store lines must be skipped even when they are EARLIER."""
    scan = pp_origin.first_physical_checkpoint(_ontrac_payload())
    assert scan["at"] == "2026-08-26T15:57:00"
    assert scan["city"] == "BRIDGEPORT" and scan["zip"] == "08014"


def test_fedex_zip_only_label_line_is_not_a_scan():
    """'Shipment information sent to FedEx, 08085' carries a ZIP but is not a physical scan."""
    p = _fedex_wilmington_payload()
    scan = pp_origin.first_physical_checkpoint(p)
    assert scan["city"] == "WILMINGTON"            # the pickup scan, not the 60445 label line
    assert pp_origin.label_origin_zip(p) == "60445"


# ── the three carrier scan dialects ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("detail,expect", [
    # OnTrac — comma between city and state, ZIP, trailing US
    ("…see Estimated Delivery Date, BRIDGEPORT, NJ 08014 US", ("BRIDGEPORT", "NJ", "08014")),
    # FedEx — NO comma between city and state
    ("Picked up, BARRINGTON NJ 08007", ("BARRINGTON", "NJ", "08007")),
    ("Delivered, Left at front door. Signature Service not requested, Clinton ME 04927",
     ("CLINTON", "ME", "04927")),
    # UPS — no ZIP at all
    ("Arrived at Facility, Mesquite TX US", ("MESQUITE", "TX", None)),
    # not a location
    ("Orders are prepared fresh weekly. Your box is in queue to be prepared and shipped.", None),
    ("The package data was sent to OnTrac, but we have yet to receive the package, US", None),
])
def test_parse_scan_location_covers_all_three_carrier_dialects(detail, expect):
    """🔴 The first regex required BOTH the comma and the zip, so it matched OnTrac only and
    reported a 67% hit rate that was really 'one carrier out of three'."""
    assert pp_origin.parse_scan_location(detail) == expect


def test_ups_has_no_zip_and_still_resolves_by_city_state():
    row = pp_origin.derive_origin(_ups_payload(), canon=canon)
    assert row["origin_scan_zip"] is None
    assert row["origin_hub"] == "Dallas"
    assert row["hub_source"] == "scan_authority_zip"


# ── carrier + tag canonicalisation ────────────────────────────────────────────────────────────────
def test_lasership_normalizes_to_ontrac():
    """🔴 PP says LaserShip where the scan text says OnTrac. No LaserShip bucket exists anywhere."""
    row = pp_origin.derive_origin(_ontrac_payload(), canon=canon)
    assert row["carrier_raw"] == "LaserShip"
    assert row["carrier"] == "OnTrac"


def test_derive_origin_requires_canon_keyword():
    """canon is un-defaultable BY DESIGN — that is what enforces gotchas 3 and 4 structurally."""
    with pytest.raises(TypeError):
        pp_origin.derive_origin(_ontrac_payload())          # type: ignore[call-arg]


def test_exclusion_only_tags_yield_no_assigned_hub():
    """🔴 A fence-only order (`!NO …` blocks and nothing else) has NO assigned hub. A hand-rolled
    `'Dallas_AHB!' in tags` reads Dallas out of `!NO OnTrac - Dallas_AHB!`; 46 live orders in the
    2026-08-27 window carry exactly that shape, which is why canon.parse_routing_tag is the only
    sanctioned reader."""
    p = _ontrac_payload()
    p["order_tags"] = ["!NO OnTrac - Dallas_AHB!", "!NO FedEx - Nashville_AHB!"]
    row = pp_origin.derive_origin(p, canon=canon)
    assert row["assigned_hub"] is None
    assert row["hub_agree"] is None          # unknown is NOT a disagreement


# ── hub mapping: never fabricate ──────────────────────────────────────────────────────────────────
def test_authority_zips_match_shiprouting_hub_roster():
    """🔴 Tier-1 mappings must stay VERBATIM equal to ShipRouting/lib/hubs.py. If that roster moves
    a hub's injection zip, this test fails instead of the table silently lying."""
    sr = str(Path(r"C:/Users/Work/Claude Projects/ShipRouting"))
    if sr not in sys.path:
        sys.path.insert(0, sr)
    hubs = pytest.importorskip("lib.hubs")
    for zip5, hub in pp_origin.AUTHORITY_FACILITY_ZIP.items():
        assert zip5 in (hubs.HUB_ORIGIN_ZIP.get(hub), hubs.HUB_ONTRAC_ZIP.get(hub)), (
            f"{zip5} is claimed as {hub}'s injection zip but is not in hubs.py for {hub}")


def test_wilmington_ma_is_refused_not_mapped_to_chicago():
    """🔴 THE fabrication trap. 125 orders scan at WILMINGTON MA 01887 and 122 of them carry a
    Chicago tag — 97.6%, which would pass any concentration threshold. It is still REFUSED: Woburn
    MA is HQ, not a hub, and FedEx stamps that scan with the shipper ACCOUNT address. Mapping it
    would be inventing a hub from tag correlation."""
    row = pp_origin.derive_origin(_fedex_wilmington_payload(), canon=canon)
    assert row["origin_hub"] == pp_origin.MISSING
    assert row["hub_source"] == "refused_shipper_account_address"
    assert row["assigned_hub"] == "Chicago"
    assert row["hub_agree"] is None           # MISSING is never counted as a disagreement
    assert row["origin_label_zip"] == "60445"  # the evidence is kept, unmapped


def test_unmapped_facility_is_missing_never_a_guess():
    p = _ontrac_payload()
    p["checkpoints"] = [{"detail": "Package received by your local OnTrac facility, DENVER, "
                                   "CO 80239 US", "status": "IN_TRANSIT",
                         "checkpoint_time": "2026-08-19T05:14:00"}]
    row = pp_origin.derive_origin(p, canon=canon)
    assert row["origin_hub"] == pp_origin.MISSING
    assert row["hub_source"] == "unmapped_facility"


def test_no_physical_scan_is_missing_not_zero():
    """A box with no carrier scan is the never-picked-up class. Absence is the answer, not a hub."""
    p = _ontrac_payload()
    p["checkpoints"] = [c for c in p["checkpoints"] if c["status"] is None]
    row = pp_origin.derive_origin(p, canon=canon)
    assert row["origin_hub"] == pp_origin.MISSING
    assert row["hub_source"] == "no_physical_scan"
    assert row["first_physical_checkpoint_at"] is None


def test_every_hub_source_is_declared():
    for src in (list(pp_origin.REFUSED_FACILITY_ZIP.values())
                + ["scan_authority_zip", "scan_derived_facility", "unmapped_facility",
                   "no_physical_scan"]):
        assert src in pp_origin.HUB_SOURCES


def test_facility_tiers_do_not_overlap():
    keys = [set(pp_origin.AUTHORITY_FACILITY_ZIP), set(pp_origin.DERIVED_FACILITY_ZIP),
            set(pp_origin.REFUSED_FACILITY_ZIP)]
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            assert not keys[i] & keys[j], "a facility zip may live in exactly one tier"


# ── zip5 and time ─────────────────────────────────────────────────────────────────────────────────
def test_dest_zip5_is_text_truncated_from_zip_plus_four():
    """🔴 TEXT, never int — 08014 is not 8014."""
    row = pp_origin.derive_origin(_ontrac_payload(), canon=canon)
    assert row["dest_zip5"] == "11101"
    assert isinstance(row["dest_zip5"], str)


def test_leading_zero_zip_survives_as_text():
    assert pp_origin.parse_scan_location("Picked up, BARRINGTON NJ 08007")[2] == "08007"


def test_to_et_converts_offset_and_assumes_naive_is_already_et():
    aware = pp_origin.to_et("2026-08-24T05:44:49-04:00")
    naive = pp_origin.to_et("2026-08-24T05:44:49")
    assert aware.utcoffset() is not None and naive.utcoffset() is not None
    assert aware.isoformat() == naive.isoformat()      # -04:00 IS ET in August


def test_transit_days_is_calendar_days_in_et_not_pp_transit_time():
    """🔴 Never PP's own `transit_time` integer. The OnTrac fixture ships PP transit_time=2 while
    pickup 08-26 → delivery 08-28 is 2 calendar days; the point is that the number is COMPUTED."""
    row = pp_origin.derive_origin(_ontrac_payload(), canon=canon)
    assert row["transit_days"] == 2
    assert pp_origin.transit_days("2026-08-26T23:50:00", "2026-08-27T00:10:00") == 1


def test_transit_days_none_when_either_end_missing():
    assert pp_origin.transit_days(None, "2026-08-28T09:10:00") is None
    assert pp_origin.transit_days("2026-08-26T15:57:00", None) is None


# ── cross-module invariants ───────────────────────────────────────────────────────────────────────
def test_movement_matches_pp_webhook():
    """🔴 MOVEMENT is RESTATED here (appyhour_lib is a stdlib-only leaf and must not import the
    ShipRouting server package). This test is what stops the restatement from drifting — the
    STATUS_INGEST_RULES open-question-5 class, where two definitions of 'moved' feed one table."""
    sr = str(Path(r"C:/Users/Work/Claude Projects/ShipRouting"))
    if sr not in sys.path:
        sys.path.insert(0, sr)
    pw = pytest.importorskip("server.pp_webhook")
    assert set(pp_origin.MOVEMENT) == set(pw.MOVEMENT)


def test_pickup_location_is_never_read():
    """`pickup_location` exists and is NULL on 2,694/2,694 payloads. Nothing may depend on it."""
    src = Path(pp_origin.__file__).read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert 'get("pickup_location"' not in code and "get('pickup_location'" not in code


def test_location_name_is_never_read_as_hub():
    """`location: {name: 'RMFG'}` is the SHIPPER, not the hub."""
    src = Path(pp_origin.__file__).read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    assert 'get("location"' not in code and "get('location'" not in code
