"""Smoke tests for shopify_bulk.py — offline (no live API calls).

Tests:
    1. reconstruct_orders: JSONL → REST-shaped order list
    2. gql_orders_by_tag: query builder + response parser via mocked _gql_post
    3. fetch_fulfilled_orders_bulk: full flow via mocked submit/poll/stream
"""

from __future__ import annotations

import io
import json
import sys
from unittest.mock import patch

sys.path.insert(0, ".")

import shopify_bulk as sb


def test_reconstruct_orders_basic():
    rows = iter([
        {"id": "gid://shopify/Order/1001", "name": "#A", "createdAt": "2026-04-01T00:00:00Z",
         "tags": ["_SHIP_2026-05-04", "Subscription"]},
        {"id": "gid://shopify/LineItem/9001", "sku": "CH-ALP", "quantity": 2,
         "__parentId": "gid://shopify/Order/1001"},
        {"id": "gid://shopify/LineItem/9002", "sku": "MT-PP", "quantity": 1,
         "__parentId": "gid://shopify/Order/1001"},
        {"id": "gid://shopify/Order/1002", "name": "#B", "createdAt": "2026-04-02T00:00:00Z",
         "tags": []},
        {"id": "gid://shopify/LineItem/9003", "sku": "TR-FTC", "quantity": 5,
         "__parentId": "gid://shopify/Order/1002"},
    ])
    out = sb.reconstruct_orders(rows)
    assert len(out) == 2, f"expected 2 orders, got {len(out)}"
    o1 = out[0]
    assert o1["id"] == 1001
    assert o1["name"] == "#A"
    assert o1["created_at"] == "2026-04-01T00:00:00Z"
    assert o1["tags"] == "_SHIP_2026-05-04, Subscription"
    assert len(o1["line_items"]) == 2
    assert o1["line_items"][0] == {"sku": "CH-ALP", "quantity": 2}
    assert o1["line_items"][1] == {"sku": "MT-PP", "quantity": 1}
    o2 = out[1]
    assert o2["id"] == 1002
    assert o2["tags"] == ""
    assert len(o2["line_items"]) == 1
    print("PASS: reconstruct_orders_basic")


def test_reconstruct_orders_orphan_child_ignored():
    rows = iter([
        {"id": "gid://shopify/LineItem/9999", "sku": "DEAD", "quantity": 1,
         "__parentId": "gid://shopify/Order/MISSING"},
        {"id": "gid://shopify/Order/1003", "name": "#C", "createdAt": "2026-04-03T00:00:00Z",
         "tags": []},
    ])
    out = sb.reconstruct_orders(rows)
    assert len(out) == 1
    assert out[0]["line_items"] == []
    print("PASS: reconstruct_orders_orphan_child_ignored")


def test_gid_to_int():
    assert sb._gid_to_int("gid://shopify/Order/12345") == 12345
    assert sb._gid_to_int("") == 0
    assert sb._gid_to_int("garbage") == 0
    print("PASS: gid_to_int")


def test_gql_orders_by_tag_query_built():
    """Verify query filter string + variables passed to _gql_post."""
    captured = {}

    def fake_gql_post(store, token, query, variables=None):
        captured["query"] = query
        captured["variables"] = variables
        return {
            "orders": {
                "edges": [
                    {"node": {
                        "id": "gid://shopify/Order/2001",
                        "name": "#X",
                        "tags": ["_SHIP_2026-05-04"],
                        "lineItems": {"edges": [
                            {"node": {"sku": "CH-ALP", "quantity": 3,
                                      "currentQuantity": 3, "unfulfilledQuantity": 3}},
                            {"node": {"sku": "MT-PP", "quantity": 2,
                                      "currentQuantity": 0, "unfulfilledQuantity": 0}},
                        ]},
                    }}
                ],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        }

    with patch.object(sb, "_gql_post", side_effect=fake_gql_post):
        out = sb.gql_orders_by_tag("teststore", "tok",
                                   ["_SHIP_2026-05-04", "_SHIP_2026-05-11"],
                                   status="open", fulfillment_status="unfulfilled")

    assert "(tag:_SHIP_2026-05-04 OR tag:_SHIP_2026-05-11)" in captured["variables"]["q"]
    assert "status:open" in captured["variables"]["q"]
    assert "fulfillment_status:unfulfilled" in captured["variables"]["q"]
    assert captured["variables"]["li"] == 50
    assert "lineItems(first: $li)" in captured["query"]
    assert "currentQuantity" in captured["query"]
    assert "unfulfilledQuantity" in captured["query"]

    assert len(out) == 1
    o = out[0]
    assert o["id"] == 2001
    assert o["name"] == "#X"
    assert o["tags"] == "_SHIP_2026-05-04"
    assert len(o["line_items"]) == 2
    li1 = o["line_items"][0]
    assert li1 == {"sku": "CH-ALP", "quantity": 3, "fulfillable_quantity": 3}
    # Refunded/edited line: ordered=2, unfulfilled=0
    li2 = o["line_items"][1]
    assert li2 == {"sku": "MT-PP", "quantity": 2, "fulfillable_quantity": 0}
    print("PASS: gql_orders_by_tag_query_built")


def test_gql_orders_by_tag_empty_tags():
    out = sb.gql_orders_by_tag("teststore", "tok", [])
    assert out == []
    print("PASS: gql_orders_by_tag_empty_tags")


def test_gql_orders_by_tag_pagination():
    """Paginates until hasNextPage=False."""
    pages = [
        {"orders": {
            "edges": [{"node": {"id": "gid://shopify/Order/1", "name": "#1", "tags": [],
                                 "lineItems": {"edges": []}}}],
            "pageInfo": {"hasNextPage": True, "endCursor": "CUR1"},
        }},
        {"orders": {
            "edges": [{"node": {"id": "gid://shopify/Order/2", "name": "#2", "tags": [],
                                 "lineItems": {"edges": []}}}],
            "pageInfo": {"hasNextPage": False, "endCursor": "CUR2"},
        }},
    ]
    cursors_seen = []

    def fake_gql_post(store, token, query, variables=None):
        cursors_seen.append(variables.get("cursor"))
        return pages.pop(0)

    with patch.object(sb, "_gql_post", side_effect=fake_gql_post):
        out = sb.gql_orders_by_tag("s", "t", ["_SHIP_X"])

    assert len(out) == 2
    assert cursors_seen == [None, "CUR1"]
    print("PASS: gql_orders_by_tag_pagination")


def test_fetch_fulfilled_orders_bulk_full_flow():
    """Mock submit + poll + stream end-to-end."""
    jsonl_lines = [
        json.dumps({"id": "gid://shopify/Order/3001", "name": "#H1",
                    "createdAt": "2026-03-15T00:00:00Z", "tags": ["Subscription"]}),
        json.dumps({"id": "gid://shopify/LineItem/501", "sku": "CH-ALP", "quantity": 1,
                    "__parentId": "gid://shopify/Order/3001"}),
    ]

    with patch.object(sb, "submit_bulk_query", return_value="gid://shopify/BulkOperation/X"), \
         patch.object(sb, "poll_bulk_op", return_value="https://fake-jsonl-url/"), \
         patch.object(sb, "stream_jsonl", return_value=iter([json.loads(l) for l in jsonl_lines])):
        out = sb.fetch_fulfilled_orders_bulk("s", "t", weeks_back=4)

    assert len(out) == 1
    assert out[0]["id"] == 3001
    assert out[0]["line_items"][0]["sku"] == "CH-ALP"
    print("PASS: fetch_fulfilled_orders_bulk_full_flow")


def test_fetch_fulfilled_orders_bulk_empty_result():
    """poll_bulk_op returns None when zero rows match."""
    with patch.object(sb, "submit_bulk_query", return_value="gid://shopify/BulkOperation/Y"), \
         patch.object(sb, "poll_bulk_op", return_value=None):
        out = sb.fetch_fulfilled_orders_bulk("s", "t", weeks_back=4)
    assert out == []
    print("PASS: fetch_fulfilled_orders_bulk_empty_result")


if __name__ == "__main__":
    test_gid_to_int()
    test_reconstruct_orders_basic()
    test_reconstruct_orders_orphan_child_ignored()
    test_gql_orders_by_tag_empty_tags()
    test_gql_orders_by_tag_query_built()
    test_gql_orders_by_tag_pagination()
    test_fetch_fulfilled_orders_bulk_full_flow()
    test_fetch_fulfilled_orders_bulk_empty_result()
    print("\nALL TESTS PASSED")
