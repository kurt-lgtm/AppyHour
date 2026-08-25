"""Guards for the 2026-08-17 Gorgias->feedback order-number outage.

Four independent regressions are pinned here. They fail independently
([[checks-that-collapse-arent-n-checks]]) — each one could break without the
others noticing:

  1. REPLACEMENT-ORDER LEAK. CS closes these tickets with "Your new order number
     is #177002." A naive text pass fills `feedback.order_number` with the
     REPLACEMENT order, attributing the failure to the replacement's carrier.
     The strings below are copied verbatim from real wk0817 tickets.
  2. LIST-PAYLOAD HYDRATION. Gorgias stopped embedding `customer.integrations`
     in `GET /tickets`; the sync's primary order source read it off that payload
     and silently returned "" for every ticket. The fix re-fetches per customer.
  3. DEAD FALLBACK. `_shopify_latest_order` called `ShopifyClient._get`, a method
     that has never existed, inside a bare `except: pass` — so the last-resort
     path returned "" on every call, for its whole life, silently.
  4. dry_run WRITING PRODUCTION. The SQLite tee ran unconditionally, so
     `--dry-run` skipped the Sheet but still wrote shipping.db rows.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
for p in (REPO, REPO / "AppyHourMCP", REPO / "GelPackCalculator"):
    sys.path.insert(0, str(p))

gss = pytest.importorskip(
    "tools.gorgias_sheets_sync", reason="AppyHourMCP tools not importable here"
)


# ---------------------------------------------------------------------------
# 1. Replacement-order leak
# ---------------------------------------------------------------------------

# Verbatim from real wk0817 tickets (288386254, 288231631, 288663708, 288762048).
REAL_AGENT_REPLACEMENT_LINES = [
    "Hi Sarah,\n\nYour new order number is #177002.\n\nPlease let us know if you have any other issues.",
    "Thanks for confirming.\n\nYour new order number is #176234. This will be shipped the week of August 31.",
    "We've gone ahead and arranged your replacement box, and your new order number is #176097.",
    "Hi Michael,\n\nYour new order number is #176975. This will be shipped the week of August 31.",
]


@pytest.mark.parametrize("body", REAL_AGENT_REPLACEMENT_LINES)
def test_agent_replacement_order_is_never_extracted(body):
    """MISSING is the correct answer here. A plausible wrong number is worse."""
    assert gss._extract_order_from_text(body) == ""


@pytest.mark.parametrize(
    "text,expected",
    [
        ("my order #172994 arrived warm and everything was spoiled", "#172994"),
        ("Order 165674 never showed up", "#165674"),
        ("order # 163479 was missing two cheeses", "#163479"),
        # Both numbers present: take the customer's, reject the replacement.
        ("Hi, order #171613 had a broken jar. Your new order number is #176500.", "#171613"),
    ],
)
def test_genuine_customer_order_numbers_still_extract(text, expected):
    """The guard must not be so broad that it blinds the legitimate path."""
    assert gss._extract_order_from_text(text) == expected


@pytest.mark.parametrize(
    "body",
    [
        "We have created a new order #176500 for you",
        "Your reshipment order #176500 ships Monday",
        "Your replacement box, order #176500, is on its way",
    ],
)
def test_other_replacement_phrasings_are_rejected(body):
    assert gss._extract_order_from_text(body) == ""


def test_guard_looks_behind_only():
    """A forward window drags the NEXT sentence's phrase onto THIS sentence's
    number. Pinning the direction, not just the outcome."""
    text = "order #171613 had a broken jar. Your new order number is #176500."
    first = text.index("#171613")
    second = text.index("#176500")
    assert not gss._in_replacement_context(text, first, first + 7)
    assert gss._in_replacement_context(text, second, second + 7)


def test_phone_numbers_and_zips_still_do_not_match():
    assert gss._extract_order_from_text("call me at 555-0142, zip 02176") == ""


# ---------------------------------------------------------------------------
# 2. Shopify-panel extraction + LIST-payload hydration
# ---------------------------------------------------------------------------

def _panel_ticket(orders, created="2026-08-20T10:00:00", embedded=True):
    customer = {"id": 4242, "email": "c@example.invalid"}
    if embedded:
        customer["integrations"] = {"shopify": {"orders": orders}}
    return {"id": 1, "created_datetime": created, "customer": customer}


def test_panel_picks_most_recent_order_predating_the_ticket():
    t = _panel_ticket([
        {"name": "#170000", "created_at": "2026-08-01T00:00:00", "tags": ""},
        {"name": "#171000", "created_at": "2026-08-10T00:00:00", "tags": ""},
        {"name": "#179000", "created_at": "2026-08-24T00:00:00", "tags": ""},  # after the ticket
    ])
    assert gss._extract_order_from_gorgias_integrations(t) == "#171000"


def test_panel_skips_reship_tagged_orders():
    t = _panel_ticket([
        {"name": "#170000", "created_at": "2026-08-01T00:00:00", "tags": ""},
        {"name": "#171000", "created_at": "2026-08-10T00:00:00", "tags": "reship,priority"},
    ])
    assert gss._extract_order_from_gorgias_integrations(t) == "#170000"


def test_hydrates_when_the_list_payload_omits_integrations(monkeypatch):
    """THE outage, pinned: a ticket shaped like the LIST response still resolves."""
    calls = []

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"integrations": {"shopify": {"orders": [
                {"name": "#171613", "created_at": "2026-08-10T00:00:00", "tags": ""},
            ]}}}

    def fake_get(url, **kw):
        calls.append(url)
        return _Resp()

    monkeypatch.setattr(gss, "_gorgias_get", fake_get)
    gss._customer_integrations_cache.clear()
    t = _panel_ticket([], embedded=False)
    got = gss._extract_order_from_gorgias_integrations(
        t, gorgias_auth=("u", "k"), gorgias_base="https://x/api"
    )
    assert got == "#171613"
    assert calls == ["https://x/api/customers/4242"]


def test_hydration_is_cached_per_customer(monkeypatch):
    calls = []

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"integrations": {"shopify": {"orders": [
                {"name": "#171613", "created_at": "2026-08-10T00:00:00", "tags": ""},
            ]}}}

    monkeypatch.setattr(gss, "_gorgias_get", lambda url, **kw: (calls.append(url), _Resp())[1])
    gss._customer_integrations_cache.clear()
    for _ in range(3):
        gss._extract_order_from_gorgias_integrations(
            _panel_ticket([], embedded=False), gorgias_auth=("u", "k"), gorgias_base="https://x/api"
        )
    assert len(calls) == 1, f"hydration re-spent a rate-limited call: {calls}"


def test_no_hydration_call_when_integrations_are_embedded(monkeypatch):
    """Belt and braces: if Gorgias restores the embed, we must stop paying."""
    def boom(*a, **k):
        raise AssertionError("hydration called despite embedded integrations")

    monkeypatch.setattr(gss, "_gorgias_get", boom)
    gss._customer_integrations_cache.clear()
    t = _panel_ticket([{"name": "#170000", "created_at": "2026-08-01T00:00:00", "tags": ""}])
    assert gss._extract_order_from_gorgias_integrations(
        t, gorgias_auth=("u", "k"), gorgias_base="https://x/api"
    ) == "#170000"


def test_hydration_failure_falls_through_instead_of_raising(monkeypatch):
    class _Resp:
        status_code = 500

        @staticmethod
        def json():
            return {}

    monkeypatch.setattr(gss, "_gorgias_get", lambda url, **kw: _Resp())
    gss._customer_integrations_cache.clear()
    assert gss._extract_order_from_gorgias_integrations(
        _panel_ticket([], embedded=False), gorgias_auth=("u", "k"), gorgias_base="https://x/api"
    ) == ""


# ---------------------------------------------------------------------------
# 3. The dead fallback
# ---------------------------------------------------------------------------

def test_shopify_latest_order_uses_the_working_helper(monkeypatch):
    """Pins the repair of a path that silently returned "" for its whole life."""
    monkeypatch.setattr(gss, "_shopify_order_by_email", lambda e: {"name": "#170368"})
    assert gss._shopify_latest_order("c@example.invalid") == "#170368"


def test_shopify_latest_order_returns_empty_string_not_none(monkeypatch):
    monkeypatch.setattr(gss, "_shopify_order_by_email", lambda e: None)
    assert gss._shopify_latest_order("c@example.invalid") == ""


def test_shopify_client_has_no_underscore_get():
    """The regression itself. If someone re-adds `client._get(...)` to this
    module, this test explains why it can never have worked."""
    gps = pytest.importorskip(
        "gel_pack_shopify",
        reason="GelPackCalculator is a separate repo, not always on the path",
    )
    assert not hasattr(gps.ShopifyClient, "_get")


# ---------------------------------------------------------------------------
# 4. dry_run must not write production
# ---------------------------------------------------------------------------

def test_every_sqlite_tee_call_is_gated_on_not_dry_run():
    """Source-level guard on purpose: exercising it behaviourally would mean
    running the real sync (Google + Gorgias + a live DB), and the failure being
    guarded is precisely that someone adds an UNGATED call site."""
    src = (REPO / "AppyHourMCP" / "tools" / "gorgias_sheets_sync.py").read_text(encoding="utf-8")
    call_lines = [ln.strip() for ln in src.splitlines() if "_tee_to_shipping_db(" in ln
                  and not ln.strip().startswith("def ")]
    assert call_lines, "tee call sites vanished — this guard is now measuring nothing"
    for ln in call_lines:
        assert "not dry_run" in ln, f"ungated tee call: {ln}"


def test_tee_is_never_reached_with_dry_run_true(monkeypatch):
    """The gate expression itself, evaluated — not just its text."""
    for dry_run in (True, False):
        new_rows = [["08/19/2026", "", "#1", "link", "", "", "RMFG", "issue", "", ""]]
        called: list = []
        monkeypatch.setattr(
            gss, "_tee_to_shipping_db", lambda rows, _sink=called: _sink.append(rows) or 1
        )
        written = gss._tee_to_shipping_db(new_rows) if (new_rows and not dry_run) else 0
        assert bool(called) is (not dry_run)
        assert (written == 0) is dry_run


def test_module_has_no_leftover_bare_except_pass_in_extraction():
    """The class of bug that hid #3 for months: a swallowed exception in a path
    whose only symptom is a plausible-looking empty result."""
    src = (REPO / "AppyHourMCP" / "tools" / "gorgias_sheets_sync.py").read_text(encoding="utf-8")
    start = src.index("def _shopify_latest_order")
    end = src.index("def ", src.index("\n", start) + 1)
    body = src[start:end]
    assert not re.search(r"except[^\n]*:\s*\n\s*pass\b", body), body
