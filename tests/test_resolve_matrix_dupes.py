"""RESOLVE_DUPES_RULES.md — one test block per rule the resolver must enforce.

No live Shopify: `gql` is monkeypatched to RAISE, so any code path that reaches the network fails
loudly. Order state + $0-variant stock are injected. Fixture CSVs live in tests/fixtures/resolve_dupes/.
"""
from __future__ import annotations

import csv
import importlib.util
import sys
from collections import Counter, OrderedDict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "resolve_dupes"
sys.path.insert(0, str(ROOT / "scripts" / "utilities"))

_spec = importlib.util.spec_from_file_location("rmd", ROOT / "scripts" / "utilities" / "resolve_matrix_dupes.py")
assert _spec and _spec.loader
rmd = importlib.util.module_from_spec(_spec)
sys.modules["rmd"] = rmd  # dataclasses resolve annotations via sys.modules[cls.__module__]
_spec.loader.exec_module(rmd)


@pytest.fixture(autouse=True)
def _no_live_shopify(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("live Shopify call attempted in a unit test")
    monkeypatch.setattr(rmd, "gql", boom)


def _rows(name="add_sheet.csv"):
    with open(FIX / name, encoding="utf-8-sig", newline="") as f:
        r = csv.DictReader(f)
        return list(r.fieldnames or []), list(r)


def _state(box=(), removed=(), ever=()):
    return {"box": list(box), "removed": list(removed), "ever": list(set(ever) | set(box)), "last": None, "email": None}


def _inv(stock: dict | None = None, default=100, **kw):
    """Fake $0-variant lookup: every SKU is unique/$0 with `default` live qty unless overridden (None = no $0 variant)."""
    stock = dict(stock or {})

    def prod(sku):
        q = stock.get(sku, default)
        return None if q is None else rmd.Prod(f"pid-{sku}", sku.lower(), q)
    return rmd.Inventory(prod, **kw)


def _states_for(rows, **per_order):
    return {rmd.norm(r["Name"]): per_order.get(rmd.norm(r["Name"]), _state()) for r in rows}


def _by_order(rows):
    g = OrderedDict()
    for r in rows:
        g.setdefault(rmd.norm(r["Name"]), []).append(r["child_sku"])
    return g


# ── Rule 1: never drop a row; swap only the 2nd+ in-sheet occurrence ─────────────────────────
def test_rule1_in_sheet_dupe_swaps_second_only_never_drops():
    _, rows = _rows()
    res = rmd.resolve(rows, _states_for(rows), _inv(), rmd.Options())
    assert len(res.rows) == len(rows)                          # row count preserved
    o2 = _by_order(res.rows)["200002"]
    assert o2[0] == "MT-CAPO" and o2[1] != "MT-CAPO"           # 1st kept, 2nd swapped
    assert o2[1].startswith("MT-") and len(set(o2)) == 2
    assert Counter(_by_order(res.rows)["200002"]).most_common(1)[0][1] == 1


def test_rule1_output_order_per_input_row_is_stable():
    _, rows = _rows()
    res = rmd.resolve(rows, _states_for(rows), _inv(), rmd.Options())
    assert [rmd.norm(r["Name"]) for r in res.rows] == [rmd.norm(r["Name"]) for r in rows]


# ── Rule 2: live dupe (currentQuantity>0) → swap ─────────────────────────────────────────────
def test_rule2_live_dupe_is_swapped_within_slot():
    _, rows = _rows()
    st = _states_for(rows, **{"200003": _state(box=["CH-FONT"])})
    res = rmd.resolve(rows, st, _inv(), rmd.Options())
    o3 = _by_order(res.rows)["200003"]
    assert o3[0] != "CH-FONT" and o3[0].startswith("CH-")
    assert any(e["orig"] == "CH-FONT" and e["reason"] == "LIVE-DUPE" for e in res.swaplog)


# ── Rule 3: removed-line switch applies to EVERY SKU ─────────────────────────────────────────
@pytest.mark.parametrize("order,sku,slot_prefix", [("200001", "AC-FCROSE", "AC-"), ("200003", "CH-FONT", "CH-"),
                                                   ("200002", "MT-CAPO", "MT-")])
def test_rule3_removed_line_switched_for_any_sku(order, sku, slot_prefix):
    _, rows = _rows()
    st = _states_for(rows, **{order: _state(removed=[sku])})
    res = rmd.resolve(rows, st, _inv(), rmd.Options())
    assert sku not in _by_order(res.rows)[order]
    e = next(e for e in res.swaplog if e["order"] == order and e["orig"] == sku)
    assert "REMOVED" in e["reason"] and e["new"].startswith(slot_prefix)


def test_rule3_fcrose_removed_goes_to_a_cracker_not_any_ac():
    _, rows = _rows()
    st = _states_for(rows, **{"200001": _state(removed=["AC-FCROSE"])})
    res = rmd.resolve(rows, st, _inv(), rmd.Options())
    new = _by_order(res.rows)["200001"][0]
    assert new in rmd.POOLS["cracker"]
    r = res.rows[0]
    assert r["Line: Product ID"] == f"pid-{new}" and r["Line: Product Handle"] == new.lower()


# ── Rule 5: pools keyed by parent_sku SLOT, never by prefix ──────────────────────────────────
def test_rule5_slot_from_parent_sku():
    assert rmd.slot_for("CEX-CR", "AC-RMC") == "cracker"          # slot wins even for a nut SKU
    assert rmd.slot_for("EX-EA", "AC-QUIC") == "accompaniment"
    assert rmd.slot_for("CEX-EA", "AC-X") == "accompaniment"
    assert rmd.slot_for("EX-EM", "MT-X") == "meat" and rmd.slot_for("CEX-EM", "MT-X") == "meat"
    assert rmd.slot_for("EX-EC", "CH-X") == "cheese" and rmd.slot_for("CEX-EC-L", "CH-X") == "cheese"
    assert rmd.slot_for("PR-CJAM-GEN", "CH-SOT") == "cjam"
    assert rmd.slot_for("AHB-MCUST-MDT", "CH-X") == "cheese"      # CH-/MT- prefix is unambiguous
    assert rmd.slot_for("AHB-MCUST-MDT", "AC-X") is None          # AC- is NOT categorised by prefix


def test_rule5_cracker_pool_is_exactly_kurts_set():
    assert rmd.POOLS["cracker"] == ["AC-FCROSE", "AC-TOK", "AC-FCFIGO", "AC-FCWALN", "AC-FCEVOO", "AC-PFLAT"]


def test_rule5_cex_cr_dupe_draws_from_cracker_pool_only():
    _, rows = _rows()
    st = _states_for(rows, **{"200003": _state(box=["AC-RMC"])})   # the AC-RMC-under-CEX-CR row is a live dupe
    res = rmd.resolve(rows, st, _inv(), rmd.Options())
    new = [r for r in res.rows if rmd.norm(r["Name"]) == "200003" and r["parent_sku"] == "CEX-CR"][0]["child_sku"]
    assert new in rmd.POOLS["cracker"] and new not in rmd.POOLS["accompaniment"]


def test_rule5_ac_under_unknown_parent_is_no_sub_not_guessed():
    _, rows = _rows()
    rows = [dict(rows[1], parent_sku="AHB-MCUST-MDT")]              # AC-QUIC under a box parent
    st = _states_for(rows, **{"200001": _state(box=["AC-QUIC"])})
    res = rmd.resolve(rows, st, _inv(), rmd.Options())
    assert res.rows[0]["child_sku"] == "AC-QUIC" and res.swaplog[0]["new"] == "NO-SUB:UNKNOWN-SLOT"


def test_rule5_prcjam_pair_swapped_coherently():
    _, rows = _rows()
    st = _states_for(rows, **{"200004": _state(box=["CH-SOT"])})    # cheese half is a live dupe
    res = rmd.resolve(rows, st, _inv(), rmd.Options())
    o4 = _by_order(res.rows)["200004"]
    assert o4 == ["CH-MONT", "AC-SCJ"]                              # BOTH halves move to the other legal pair
    assert res.report["cjam-swap"] == 1


def test_rule5_prcjam_jam_dupe_also_moves_the_cheese():
    _, rows = _rows()
    st = _states_for(rows, **{"200004": _state(box=["AC-MFJ"])})
    res = rmd.resolve(rows, st, _inv(), rmd.Options())
    assert _by_order(res.rows)["200004"] == ["CH-MONT", "AC-SCJ"]
    assert len(_by_order(res.rows)["200004"]) == 2                  # jam row never dropped


def test_rule5_prcjam_no_legal_pair_is_no_sub():
    _, rows = _rows()
    st = _states_for(rows, **{"200004": _state(box=["CH-SOT"], ever=["CH-MONT"])})
    res = rmd.resolve(rows, st, _inv(), rmd.Options())
    assert _by_order(res.rows)["200004"] == ["CH-SOT", "AC-MFJ"]    # untouched, flagged
    assert res.report["NO-SUB"] == 1


# ── Rule 6: Shopify-live stock, round-robin, floors/caps/zero, low-stock report ──────────────
def test_rule6_pool_sku_at_or_below_min_avail_is_skipped():
    inv = _inv({"MT-CCCS": 30, "MT-SPAP": 31})                     # 30 <= min_avail -> skipped; 31 ok
    p = rmd._Picker(inv)
    assert p.pick("meat", set()) == "MT-SPAP"
    assert p.pick("meat", set()) != "MT-SPAP"                       # 31-1=30 -> now skipped too


def test_rule6_round_robin_spreads_across_pool():
    inv = _inv()
    p = rmd._Picker(inv)
    picks = [p.pick("cheese", set()) for _ in range(4)]
    assert len(set(picks)) == 4 and picks == rmd.POOLS["cheese"][:4]
    assert all(inv.drawn[s] == 1 for s in picks)


def test_rule6_meat_priority_mt_cccs_first_no_rotation():
    p = rmd._Picker(_inv())
    assert [p.pick("meat", set()) for _ in range(3)] == ["MT-CCCS"] * 3  # always first while in stock


def test_rule6_floor_force_swaps_overflow_and_never_picks_floored():
    _, rows = _rows()
    inv = _inv({"CH-OGK": 32}, floors={"CH-OGK": 30})               # 32 live, floor 30 -> keep 2 of 3 adds
    res = rmd.resolve(rows, _states_for(rows), inv, rmd.Options())
    ogk = [r["child_sku"] for r in res.rows if r["Name"] in ("#200005", "#200006", "#200007")]
    assert ogk.count("CH-OGK") == 2 and ogk[2] != "CH-OGK" and ogk[2].startswith("CH-")
    assert any(e["reason"] == "FLOOR" and e["orig"] == "CH-OGK" for e in res.swaplog)
    assert not inv.can_draw("CH-OGK")


def test_rule6_cap_and_zero():
    _, rows = _rows()
    res = rmd.resolve(rows, _states_for(rows), _inv(caps={"CH-OGK": 1}), rmd.Options())
    assert [r["child_sku"] for r in res.rows[-3:]].count("CH-OGK") == 1
    res = rmd.resolve(rows, _states_for(rows), _inv(zero={"CH-OGK"}), rmd.Options())
    assert "CH-OGK" not in [r["child_sku"] for r in res.rows]
    assert res.report["ZERO"] == 3


def test_rule6_low_stock_report_lists_every_sku_pushed_below_30():
    _, rows = _rows()
    inv = _inv({"CH-OGK": 31, "AC-FCROSE": 500})
    low = rmd.low_stock_report(rows, inv)
    assert ("CH-OGK", 31, 3, 28) in low and not any(s == "AC-FCROSE" for s, *_ in low)


def test_rule6_live_qty_comes_from_zero_dollar_variant(monkeypatch):
    calls = []

    def fake_gql(q, v):
        calls.append(v["q"])
        return {"productVariants": {"edges": [
            {"node": {"sku": "AC-TOK", "price": "5.50", "inventoryQuantity": 999, "availableForSale": True,
                      "product": {"id": "gid://shopify/Product/1", "title": "Toketti", "handle": "toketti-paid"}}},
            {"node": {"sku": "AC-TOK", "price": "0.00", "inventoryQuantity": 41, "availableForSale": True,
                      "product": {"id": "gid://shopify/Product/10108946120984", "title": "Toketti", "handle": "toketti"}}},
        ]}}
    monkeypatch.setattr(rmd, "gql", fake_gql)
    p = rmd.fetch_prod("AC-TOK")
    assert p == rmd.Prod("10108946120984", "toketti", 41) and calls == ["sku:AC-TOK"]


def test_rule4_two_products_for_zero_dollar_sku_is_rejected(monkeypatch):
    monkeypatch.setattr(rmd, "gql", lambda q, v: {"productVariants": {"edges": [
        {"node": {"sku": "CH-LOU", "price": "0", "inventoryQuantity": 5, "product": {"id": "gid://x/1", "handle": "a"}}},
        {"node": {"sku": "CH-LOU", "price": "0", "inventoryQuantity": 5, "product": {"id": "gid://x/2", "handle": "b"}}},
    ]}})
    assert rmd.fetch_prod("CH-LOU") is None


# ── Rule 7: barred list enforced in every pool ───────────────────────────────────────────────
@pytest.mark.parametrize("sku", ["MT-FS-JAMS", "MT-HOTP", "AC-RMC", "MT-IBRES", "MT-BSS", "CH-MAFT", "AC-RBOL",
                                 "AC-BLUCAR", "AC-GBEF", "AC-SCJ", "AC-SRHUB", "AC-MFJ", "CH-BRIE", "CH-TTBRIE",
                                 "CH-PBRIE"])
def test_rule7_barred_never_in_any_pool(sku):
    assert rmd.is_barred(sku)
    assert all(sku not in pool for pool in rmd.POOLS.values())


def test_rule7_mini_jam_legal_only_inside_cjam_pair():
    assert rmd.is_barred("AC-MFJ") and not rmd.is_barred("AC-MFJ", generic=False)


def test_rule7_picker_refuses_barred_even_if_injected(monkeypatch):
    monkeypatch.setitem(rmd.POOLS, "meat", ["MT-BSS", "MT-HOTP", "MT-CCCS"])
    assert rmd._Picker(_inv()).pick("meat", set()) == "MT-CCCS"


def test_rule7_dietary_restricted_slot_is_flagged_not_guessed():
    _, rows = _rows()
    st = _states_for(rows, **{"200001": _state(box=["AHB-MCUST-NCFS-NMS", "AC-FCROSE"])})  # no-crackers box (FS twin)
    res = rmd.resolve(rows, st, _inv(), rmd.Options())
    assert res.rows[0]["child_sku"] == "AC-FCROSE"
    assert res.swaplog[0]["new"] == "NEEDS-DIETARY-REVIEW"


# ── Rule 8: post-import guard ────────────────────────────────────────────────────────────────
def test_rule8_already_imported_sheet_aborts():
    _, rows = _rows("already_imported.csv")
    st = {"300001": _state(box=["AC-FCROSE", "CH-FONT"]), "300002": _state(box=["MT-CAPO"])}  # 3/4 live
    with pytest.raises(rmd.AlreadyImported, match="not-landed scan"):
        rmd.guard_already_imported(rows, st, 0.5)
    assert rmd.guard_already_imported(rows, st, 0.9) == 0.75      # threshold is a CLI knob


# ── Rule 9: swap log every run; never overwrite an existing --out ────────────────────────────
def test_rule9_versioning_and_swaplog(tmp_path):
    fields, rows = _rows()
    res = rmd.resolve(rows, _states_for(rows, **{"200003": _state(box=["CH-FONT"])}), _inv(), rmd.Options())
    out = tmp_path / "x_RESOLVED.csv"
    out.write_text("KEEP ME", encoding="utf-8")
    p1, l1 = rmd.write_outputs(out, fields, res, ["# hdr"])
    p2, l2 = rmd.write_outputs(out, fields, res, ["# hdr"])
    assert out.read_text(encoding="utf-8") == "KEEP ME"
    assert (p1.name, p2.name) == ("x_RESOLVED-2.csv", "x_RESOLVED-3.csv")
    assert (l1.name, l2.name) == ("x_RESOLVED-2_SWAPLOG.txt", "x_RESOLVED-3_SWAPLOG.txt")
    txt = l1.read_text(encoding="utf-8")
    assert "#200003\tCH-FONT\t" in txt and "LIVE-DUPE" in txt and txt.isascii()


def test_rule9_swaplog_written_even_with_zero_swaps(tmp_path):
    fields, rows = _rows()
    res = rmd.resolve(rows[:1], _states_for(rows[:1]), _inv(), rmd.Options())
    _, lp = rmd.write_outputs(tmp_path / "o.csv", fields, res, ["# hdr"])
    assert lp.exists() and "order\torig\tnew" in lp.read_text(encoding="utf-8")


# ── Rule 11: swap storm ──────────────────────────────────────────────────────────────────────
def test_rule11_swap_storm_leaves_order_untouched_and_flags():
    _, rows = _rows("swap_storm.csv")
    processed = _state(box=["CH-FONT", "CH-GOUD", "MT-CAPO", "MT-SAL", "AC-FCROSE", "AC-QUIC"])
    st = {"175884": processed, "175885": _state(box=["CH-FONT"])}
    inv = _inv()
    res = rmd.resolve(rows, st, inv, rmd.Options(storm=5))
    assert _by_order(res.rows)["175884"] == [r["child_sku"] for r in rows if r["Name"] == "#175884"]
    assert res.report["PROBABLE-ALREADY-PROCESSED"] == 1 and any("175884" in f for f in res.flags)
    o2 = _by_order(res.rows)["175885"]
    assert o2[0] != "CH-FONT" and res.report["LIVE-DUPE"] == 1        # the normal order still resolved
    # drawn is seeded with the sheet's own adds (rule 6 committed stock); the one resolved swap nets 0
    # (sub +1, released source unit -1) and the storm order's draws were rolled back.
    assert sum(inv.drawn.values()) == len(rows)


def test_rule6_source_adds_are_committed_stock_so_subs_never_oversell():
    """Burn 2026-09-11: CH-CONI 67 live / 67 source adds was still picked as a sub -> -2."""
    rows = [{"Name": "#1", "child_sku": "CH-CONI", "parent_sku": "EX-EC"},
            {"Name": "#2", "child_sku": "CH-CONI", "parent_sku": "EX-EC"},
            {"Name": "#3", "child_sku": "CH-FONT", "parent_sku": "EX-EC"}]
    st = {"1": _state(), "2": _state(), "3": _state(box=["CH-FONT"])}
    qty = {"CH-CONI": 32}                 # 32 live - 2 source adds = 30 -> NOT drawable (> 30 required)
    inv = rmd.Inventory(lambda s: rmd.Prod(f"pid-{s}", s.lower(), qty.get(s, 0 if s in rmd.POOLS['cheese'][:0] else 100)))
    rmd.POOLS_BACKUP = dict(rmd.POOLS)
    try:
        rmd.POOLS["cheese"] = ["CH-CONI", "CH-OTTA"]
        res = rmd.resolve(rows, st, inv, rmd.Options())
    finally:
        rmd.POOLS.update(rmd.POOLS_BACKUP)
    assert _by_order(res.rows)["3"] == ["CH-OTTA"]


# ── CLI end-to-end (no network) ──────────────────────────────────────────────────────────────
def _batch_seams(monkeypatch, states: dict, prod, missing=()):
    """Patch the CLI's batched Shopify seams (fetch_states / fetch_prods) with injected state."""
    monkeypatch.setattr(rmd, "fetch_states", lambda orders: (
        {o: states.get(o, _state()) for o in orders}, [o for o in orders if o in set(missing)]))
    monkeypatch.setattr(rmd, "fetch_prods", lambda skus: {s: prod(s) for s in skus})
    monkeypatch.setattr(rmd, "fetch_prod", lambda s: pytest.fail(f"per-SKU fetch after prefetch: {s}"))


def test_cli_end_to_end(tmp_path, monkeypatch, capsys):
    states = {"200003": _state(box=["CH-FONT"])}
    _batch_seams(monkeypatch, states, lambda s: rmd.Prod(f"pid-{s}", s.lower(), 31 if s == "CH-OGK" else 100))
    out = tmp_path / "r_RESOLVED.csv"
    rc = rmd.main(["--src", str(FIX / "add_sheet.csv"), "--out", str(out), "--fresh",
                   "--cache-db", str(tmp_path / "c.db"), "--floor", "CH-SHADOW=10", "--zero", "CH-OGK"])
    assert rc == 0 and out.exists() and rmd.swaplog_path(out).exists()
    txt = capsys.readouterr().out
    assert "CH-OGK" in txt.split("-- rule 6")[1].split("#")[0]        # low-stock report printed BEFORE swaps
    with open(out, encoding="utf-8-sig", newline="") as f:
        got = list(csv.DictReader(f))
    assert len(got) == 11 and "CH-OGK" not in {r["child_sku"] for r in got}


def test_cli_aborts_on_already_imported(tmp_path, monkeypatch):
    live = _state(box=["AC-FCROSE", "CH-FONT", "MT-CAPO"])
    _batch_seams(monkeypatch, {"300001": live, "300002": live}, lambda s: rmd.Prod("p", s, 100))
    with pytest.raises(rmd.AlreadyImported):
        rmd.main(["--src", str(FIX / "already_imported.csv"), "--out", str(tmp_path / "o.csv"),
                  "--cache-db", str(tmp_path / "c.db")])
    assert not (tmp_path / "o.csv").exists()


# ── Batched Shopify reads (speed-up 2026-09-11) — a fake store answers the real query shapes ─
_REAL_GQL = rmd.gql   # captured before the autouse fixture swaps it for `boom`


def _li(sku, qty=1, cur: int | None = 1):
    return {"sku": sku, "quantity": qty, "currentQuantity": cur}


class FakeStore:
    """Answers ORDERS / aliased HIST / V queries from dicts; records every call. `page` forces pagination."""

    def __init__(self, orders=None, history=None, variants=None, page=50):
        self.orders = orders or {}        # "#N" -> {"email":..., "lines":[...]}
        self.history = history or {}      # email -> [[lines], [lines], ...]
        self.variants = variants or []    # variant nodes
        self.page, self.calls = page, []

    def _paged(self, nodes, after):
        start = int(after or 0)
        chunk = nodes[start:start + self.page]
        more = start + self.page < len(nodes)
        return {"pageInfo": {"hasNextPage": more, "endCursor": str(start + self.page) if more else None},
                "edges": [{"node": n} for n in chunk]}

    def __call__(self, q, v):
        self.calls.append((q, dict(v)))
        if "productVariants" in q:
            want = {t.split(":", 1)[1] for t in v["q"].split(" OR ")}
            hits = [n for n in self.variants if any(n["sku"].startswith(w) for w in want)]   # search is fuzzy
            return {"productVariants": self._paged(hits, v.get("after"))}
        if "$q0" in q:                    # aliased history
            return {k.replace("q", "h"): {"edges": [{"node": {"lineItems": {"nodes": ls}}}
                                                    for ls in self.history.get(em.split(":", 1)[1], [])[:20]]}
                    for k, em in v.items()}
        names = [t.split(":", 1)[1] for t in v["q"].split(" OR ")]
        hits = [{"name": nm, "customer": {"email": o["email"]} if o.get("email") else None,
                 "lineItems": {"nodes": o["lines"]}}
                for nm, o in self.orders.items() if any(nm.startswith(w) for w in names)]      # fuzzy prefix
        return {"orders": self._paged(hits, v.get("after"))}


def test_batch_orders_chunk_boundaries(monkeypatch):
    store = FakeStore(orders={f"#{100000 + i}": {"email": None, "lines": [_li("CH-X")]} for i in range(81)})
    monkeypatch.setattr(rmd, "gql", store)
    monkeypatch.setattr(rmd, "ORDER_CHUNK", 40)
    states, missing = rmd.fetch_states([str(100000 + i) for i in range(81)])
    sizes = sorted(len(v["q"].split(" OR ")) for q, v in store.calls if "orders(first:50" in q)
    assert sizes == [1, 40, 40] and missing == [] and len(states) == 81   # 81 = 40 + 40 + 1


def test_batch_orders_follow_pagination(monkeypatch):
    store = FakeStore(orders={f"#{200 + i}": {"email": None, "lines": [_li("CH-X")]} for i in range(7)}, page=3)
    monkeypatch.setattr(rmd, "gql", store)
    states, missing = rmd.fetch_states([str(200 + i) for i in range(7)])
    assert len(states) == 7 and not missing and len(store.calls) == 3    # pages of 3, 3, 1


def test_batch_missing_orders_reported_not_dropped(monkeypatch, tmp_path, capsys):
    store = FakeStore(orders={"#200001": {"email": None, "lines": [_li("MT-SAL")]},
                              "#2000010": {"email": None, "lines": [_li("CH-FONT")]}})   # fuzzy hit for #200001x
    monkeypatch.setattr(rmd, "gql", store)
    states, missing = rmd.fetch_states(["200001", "200002"])
    assert missing == ["200002"] and states["200002"] == rmd._empty_state()
    assert states["200001"]["box"] == ["MT-SAL"]                         # exact name, not the fuzzy #2000010
    # CLI: the missing order's rows stay in the output and it is named in stdout + swap log
    _batch_seams(monkeypatch, {}, lambda s: rmd.Prod(f"pid-{s}", s.lower(), 100), missing=["200004"])
    out = tmp_path / "m_RESOLVED.csv"
    assert rmd.main(["--src", str(FIX / "add_sheet.csv"), "--out", str(out), "--fresh",
                     "--cache-db", str(tmp_path / "c.db")]) == 0
    assert "MISSING: 1 order(s)" in capsys.readouterr().out
    with open(out, encoding="utf-8-sig", newline="") as f:
        assert sum(1 for r in csv.DictReader(f) if r["Name"] == "#200004") == 2
    assert "#200004" in rmd.swaplog_path(out).read_text(encoding="utf-8")


def test_batch_state_semantics_box_removed_ever(monkeypatch):
    store = FakeStore(
        orders={"#500": {"email": "a@x.com", "lines": [_li("CH-A", 1, 1), _li("AC-FCROSE", 1, 0), _li("MT-Z", 0, 0),
                                                       _li("", 1, 1)]},
                "#501": {"email": "a@x.com", "lines": [_li("CH-B")]}},
        history={"a@x.com": [[_li("CH-OLD", 1, 1), _li("CH-REFUND", 1, 0)], [_li("MT-OLD", 2, None)]]})
    monkeypatch.setattr(rmd, "gql", store)
    states, _ = rmd.fetch_states(["500", "501"])
    s = states["500"]
    assert s["box"] == ["CH-A"] and s["removed"] == ["AC-FCROSE"]        # currentQuantity>0 / q>0 & cur==0
    assert s["ever"] == ["CH-A", "CH-OLD", "MT-OLD"] and s["email"] == "a@x.com"
    assert sum(1 for q, _ in store.calls if "$q0" in q) == 1              # one customer -> one history read


def test_batch_history_alias_chunks(monkeypatch):
    emails = [f"c{i}@x.com" for i in range(16)]
    store = FakeStore(orders={f"#{900 + i}": {"email": em, "lines": [_li("CH-A")]} for i, em in enumerate(emails)},
                      history={em: [[_li(f"CH-H{i}")]] for i, em in enumerate(emails)})
    monkeypatch.setattr(rmd, "gql", store)
    monkeypatch.setattr(rmd, "HIST_ALIASES", 15)
    states, _ = rmd.fetch_states([str(900 + i) for i in range(16)])
    assert sorted(len(v) for q, v in store.calls if "$q0" in q) == [1, 15]
    assert states["915"]["ever"] == ["CH-A", "CH-H15"]                   # the 16th customer, 2nd request


def test_batch_prods_chunks_exact_sku_and_rules(monkeypatch):
    def var(sku, price, qty, pid, handle):
        return {"sku": sku, "price": price, "inventoryQuantity": qty, "availableForSale": True,
                "product": {"id": f"gid://shopify/Product/{pid}", "title": handle, "handle": handle}}
    store = FakeStore(variants=[
        var("AC-TOK", "5.50", 999, 1, "toketti-paid"), var("AC-TOK", "0.00", 41, 2, "toketti"),
        var("AC-TOKX", "0.00", 77, 3, "other"),                                        # fuzzy hit, not AC-TOK
        var("AC-PRPE", "0.00", 224, 4, "prpe"), var("AC-PRPE", "0.00", 5, 10384502784280, "free-vip-gift"),
        var("CH-LOU", "0", 5, 5, "a"), var("CH-LOU", "0", 5, 6, "b"),
    ] + [var(f"MT-S{i:02d}", "0", 50, 100 + i, f"s{i}") for i in range(40)], page=10)
    monkeypatch.setattr(rmd, "gql", store)
    monkeypatch.setattr(rmd, "SKU_CHUNK", 40)
    skus = ["AC-TOK", "AC-PRPE", "CH-LOU", "CH-NONE"] + [f"MT-S{i:02d}" for i in range(40)]
    got = rmd.fetch_prods(skus)
    assert set(got) == set(skus)
    assert got["AC-TOK"] == rmd.Prod("2", "toketti", 41)                 # $0 variant, exact sku only
    assert got["AC-PRPE"] == rmd.Prod("4", "prpe", 224)                  # free-vip-gift excluded (NON_INBOX)
    assert got["CH-LOU"] is None and got["CH-NONE"] is None              # two $0 products / no variant
    assert got["MT-S39"] == rmd.Prod("139", "s39", 50)
    assert sorted({len(v["q"].split(" OR ")) for q, v in store.calls}) == [4, 40]   # 44 skus -> 40 + 4


def test_inventory_prefetch_means_no_per_sku_reads():
    calls = []
    inv = rmd.Inventory(lambda s: pytest.fail(f"per-SKU read {s}"))
    inv.prefetch(["CH-A", "CH-B", "CH-A"], lambda ss: calls.append(list(ss)) or {s: rmd.Prod(s, s, 50) for s in ss})
    assert inv.live("CH-A") == 50 and inv.can_draw("CH-B") and calls == [["CH-A", "CH-B"]]


def test_prefetch_skus_covers_sheet_pools_and_cjam():
    _, rows = _rows()
    got = set(rmd.prefetch_skus(rows, rmd.DEFAULT_CJAM_PAIRS))
    assert {r["child_sku"] for r in rows} <= got
    assert {s for p in rmd.POOLS.values() for s in p} <= got and {"CH-SOT", "AC-MFJ", "CH-MONT", "AC-SCJ"} <= got


def test_load_states_fresh_refetches_all_cached_reuses(tmp_path, monkeypatch):
    cache = rmd.OrderStateCache(tmp_path / "c.db")
    cache.put_many({"1": _state(box=["STALE"])})
    seen = []
    monkeypatch.setattr(rmd, "fetch_states", lambda os_: (seen.append(list(os_)) or
                                                          ({o: _state(box=["NEW"]) for o in os_}, [])))
    st, _ = rmd.load_states(["1", "2"], cache, fresh=False)
    assert seen == [["2"]] and st["1"]["box"] == ["STALE"]               # only the miss is fetched
    st, _ = rmd.load_states(["1", "2"], cache, fresh=True)
    assert seen[-1] == ["1", "2"] and st["1"]["box"] == ["NEW"]          # rule 2: --fresh re-pulls EVERY order
    assert cache.peek("1")["box"] == ["NEW"]


# ── Cost throttling ──────────────────────────────────────────────────────────────────────────
def _cost(avail, requested=100, restore=200.0):
    return {"requestedQueryCost": requested, "actualQueryCost": 10,
            "throttleStatus": {"maximumAvailable": 4000.0, "currentlyAvailable": avail, "restoreRate": restore}}


def test_throttled_backs_off_then_retries(monkeypatch):
    replies = [{"errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}],
                "extensions": {"cost": _cost(avail=20, requested=420)}},
               {"data": {"ok": 1}, "extensions": {"cost": _cost(avail=3900)}}]
    sleeps = []
    monkeypatch.setattr(rmd, "_post", lambda q, v: replies.pop(0))
    monkeypatch.setattr(rmd.time, "sleep", sleeps.append)
    assert _REAL_GQL("q", {}) == {"ok": 1}
    assert sleeps == [pytest.approx(400 / 200 + 0.25)]                   # waited for the bucket to refill


def test_low_bucket_pauses_proactively_and_real_errors_raise(monkeypatch):
    sleeps = []
    monkeypatch.setattr(rmd.time, "sleep", sleeps.append)
    monkeypatch.setattr(rmd, "_post", lambda q, v: {"data": {"ok": 1}, "extensions": {"cost": _cost(avail=1000)}})
    _REAL_GQL("q", {})
    assert sleeps == []                                                   # bucket covers the next call -> no stall
    monkeypatch.setattr(rmd, "_post", lambda q, v: {"data": {"ok": 1}, "extensions": {"cost": _cost(avail=60)}})
    _REAL_GQL("q", {})
    assert sleeps == [pytest.approx((100 - 60) / 200)]                    # 60 < 100 requested -> wait the deficit
    monkeypatch.setattr(rmd, "_post", lambda q, v: {"errors": [{"message": "Field 'x' doesn't exist"}]})
    with pytest.raises(rmd.ShopifyReadError, match="doesn't exist"):
        _REAL_GQL("q", {})


def test_transient_errors_give_up_loudly(monkeypatch):
    monkeypatch.setattr(rmd.time, "sleep", lambda s: None)
    monkeypatch.setattr(rmd, "_post", lambda q, v: {"errors": [{"message": "HTTP 502",
                                                                 "extensions": {"code": "TRANSIENT"}}]})
    with pytest.raises(rmd.ShopifyReadError, match="gave up"):
        _REAL_GQL("q", {})
