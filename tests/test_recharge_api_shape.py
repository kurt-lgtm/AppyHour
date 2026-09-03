"""Recharge /events API shape vs the CSV export shape.

🔴 They disagree on two fields, and both broke the customize gate on 2026-09-03:
  * `source`: CSV string ("CUSTOMER", "[API] support@…") vs API dict
    ({origin, user_type, api_token_name, …}). classify() on the dict crashed.
  * `changes` (CSV) vs `updated_attributes` (API, list of {attribute, previous_value,
    new_value}). The API has NO `changes`, so touches_contents() saw "" and 9,532 of
    11,168 touch events in the probe window were invisible -- 85% of the customize half.
api_event_to_row() is the one place that mapping lives.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from order_checks.recharge_gate import api_event_to_row, classify, touches_contents  # noqa: E402


def _api(verb, source, ua=None, desc=""):
    return {"id": 1, "customer_id": 900, "object_type": "subscription", "verb": verb,
            "source": source, "updated_attributes": ua, "description": desc,
            "created_at": "2026-06-01T10:00:00"}


def test_source_dict_maps_to_csv_vocabulary():
    cust = {"origin": "customer_portal", "user_type": "customer"}
    admin = {"origin": "merchant_portal", "user_type": "recharge_admin"}
    api = {"origin": "api", "api_token_name": "matrix", "user_type": None}
    proc = {"origin": "merchant_portal", "user_type": "recharge_process"}
    assert classify(api_event_to_row(_api("updated", cust))["source"]) == "human"
    assert classify(api_event_to_row(_api("updated", admin))["source"]) == "human"
    assert classify(api_event_to_row(_api("updated", api))["source"]) == "api"
    assert classify(api_event_to_row(_api("updated", proc))["source"]) == "automated"


def test_classify_accepts_the_dict_directly():
    assert classify({"origin": "customer_portal", "user_type": "customer"}) == "human"


def test_updated_attributes_drive_touch_detection():
    """The API's contents diff lives in updated_attributes, not changes."""
    ua = [{"attribute": "properties",
           "previous_value": "[{\"name\": \"box_contents\", \"value\": \"1x A\"}]",
           "new_value": "[{\"name\": \"box_contents\", \"value\": \"1x B\"}]"}]
    row = api_event_to_row(_api("updated", {"user_type": "customer"}, ua))
    assert touches_contents(row["verb"], row["changes"], row["description"]) is True


def test_non_content_update_is_not_a_touch():
    ua = [{"attribute": "next_charge_date", "previous_value": "x", "new_value": "y"}]
    row = api_event_to_row(_api("updated", {"user_type": "customer"}, ua))
    assert touches_contents(row["verb"], row["changes"], row["description"]) is False


def test_csv_shape_passes_through_unchanged():
    """A row that already has string source + changes (the CSV shape) is untouched."""
    row = api_event_to_row({"id": 2, "customer_id": 1, "verb": "login", "source": "CUSTOMER",
                            "changes": "{}", "created_at": "2026-06-01 10:00:00"})
    assert row["source"] == "CUSTOMER" and row["changes"] == "{}"


def test_created_at_normalised_to_csv_shape():
    assert api_event_to_row(_api("login", {}))["created_at"] == "2026-06-01 10:00:00"
