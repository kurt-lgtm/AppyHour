import json

from tools import gorgias


class DummyMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200

    def json(self):
        return self.payload

    def raise_for_status(self):
        return None


def registered_tools():
    mcp = DummyMCP()
    gorgias.register(mcp)
    return mcp.tools


def test_get_ticket_ignores_non_dict_custom_fields(monkeypatch):
    def fake_get(endpoint, params=None):
        assert params is None
        assert endpoint == "tickets/123"
        return {
            "id": 123,
            "subject": "Damaged box",
            "status": "open",
            "channel": "email",
            "created_datetime": "2026-06-10T12:00:00Z",
            "tags": ["reship", {"name": "damaged"}],
            "custom_fields": [
                "bad-field-shape",
                {"id": 13282, "name": "Issue Type", "value": "Shipping::Damaged in transit"},
            ],
            "customer": "bad-customer-shape",
        }

    def fake_paginate(endpoint, params=None, limit=100):
        assert endpoint == "tickets/123/messages"
        return [
            {
                "sender": "bad-sender-shape",
                "body_text": "hello" * 200,
                "created_datetime": "2026-06-10T12:01:00Z",
                "source": "bad-source-shape",
            }
        ]

    monkeypatch.setattr(gorgias, "gorgias_get", fake_get)
    monkeypatch.setattr(gorgias, "gorgias_paginate", fake_paginate)

    payload = json.loads(registered_tools()["gorgias_get_ticket"](123))

    assert "error" not in payload
    assert payload["custom_fields"] == {
        "13282": {"name": "Issue Type", "value": "Shipping::Damaged in transit"}
    }
    assert payload["customer"] == {}
    assert payload["messages"][0]["sender"] == ""
    assert payload["messages"][0]["source_type"] == ""


def test_list_tickets_filters_dates_client_side_without_api_date_params(monkeypatch):
    calls = []

    monkeypatch.setattr(gorgias, "get_auth", lambda: (("email", "token"), "https://example.gorgias.com/api"))

    def fake_gorgias_get(url, *, auth, params=None, timeout=30, max_retries=5):
        calls.append(dict(params or {}))
        if params and params.get("cursor") == "next":
            return FakeResponse(
                {
                    "data": [
                        {"id": 3, "created_datetime": "2026-06-08T12:00:00Z", "status": "open"},
                    ],
                    "meta": {},
                }
            )
        return FakeResponse(
            {
                "data": [
                    {"id": 1, "created_datetime": "2026-06-12T12:00:00Z", "status": "open"},
                    {"id": 2, "created_datetime": "2026-06-10T12:00:00Z", "status": "open"},
                ],
                "meta": {"next_cursor": "next"},
            }
        )

    monkeypatch.setattr(gorgias, "_gorgias_get", fake_gorgias_get)

    payload = json.loads(
        registered_tools()["gorgias_list_tickets"](
            status="open",
            created_after="2026-06-09",
            created_before="2026-06-11",
            limit=10,
        )
    )

    assert [ticket["id"] for ticket in payload["tickets"]] == [2]
    assert calls == [
        {"limit": 100, "order_by": "created_datetime:desc", "status": "open"},
        {"limit": 100, "order_by": "created_datetime:desc", "status": "open", "cursor": "next"},
    ]
    assert all("created_datetime__gte" not in call for call in calls)
    assert all("created_datetime__lte" not in call for call in calls)


def test_ticket_stats_filters_dates_client_side(monkeypatch):
    monkeypatch.setattr(
        gorgias,
        "_paginate_tickets_client_filtered",
        lambda **kwargs: [
            {
                "status": "open",
                "channel": "email",
                "tags": [{"name": "reship"}, "plain-tag"],
            }
        ],
    )

    payload = json.loads(
        registered_tools()["gorgias_ticket_stats"](
            created_after="2026-06-01",
            created_before="2026-06-12",
        )
    )

    assert payload["total_tickets"] == 1
    assert payload["by_status"] == {"open": 1}
    assert payload["top_tags"] == {"reship": 1, "plain-tag": 1}
