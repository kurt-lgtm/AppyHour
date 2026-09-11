"""DistVol comes from the DO table `distvol`, never a file; a missing row is a NAMED warning.

🔴 Burns (2026-09-11, weekend brief item 16):
  - `resolve_distvol()` fell back to PREFIX_DEFAULTS silently — the flag only reached the xlsx
    summary tab — and 18 cohort SKUs sat on guessed box sizes for a week. Now every flagged SKU is
    a `WARNING: DistVol MISSING for <sku> - <n> order(s)` line in the run log.
  - `_distvol_db()` was gated on ROUTING_INPUTS_DB=1 and fell back to the Desktop xlsx on ANY
    failure — a local build and the cloud could size one cohort off two sources.

Creds-free: the connection seam `distvol_db_connect` is faked; no credential file is ever read
under pytest (`_distvol_database_url` consults env DATABASE_URL only).
"""
import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import box_simulation as bs  # noqa: E402


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        assert "FROM distvol" in sql

    def fetchall(self):
        return list(self._rows)


class _Conn:
    def __init__(self, rows):
        self.rows, self.closed = rows, False

    def cursor(self):
        return _Cursor(self.rows)

    def close(self):
        self.closed = True


def _rows(n):
    return [(f"CH-T{i:03d}", 0.2) for i in range(n)]


@pytest.fixture(autouse=True)
def _creds_free(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)


def test_no_credential_is_fatal_naming_the_table():
    with pytest.raises(bs.DistVolUnavailable) as e:
        bs.build_lookup()
    assert "distvol" in str(e.value) and "DATABASE_URL" in str(e.value)


def test_table_rows_become_the_lookup_with_manual_overrides_on_top(monkeypatch):
    conns = []

    def connect():
        c = _Conn(_rows(250) + [("AC-QUIC", 0.99)])
        conns.append(c)
        return c
    monkeypatch.setattr(bs, "distvol_db_connect", connect)
    lk = bs.build_lookup()
    assert lk["CH-T000"] == 0.2 and len(lk) >= 250
    assert lk["AC-QUIC"] == bs.MANUAL_OVERRIDES["AC-QUIC"]      # code override still wins
    assert conns and conns[0].closed


@pytest.mark.parametrize("rows", [[], _rows(50)])
def test_empty_or_half_loaded_table_refuses(monkeypatch, rows):
    monkeypatch.setattr(bs, "distvol_db_connect", lambda: _Conn(rows))
    with pytest.raises(bs.DistVolUnavailable) as e:
        bs.build_lookup()
    assert "distvol" in str(e.value)


def test_connection_failure_is_named_not_swallowed(monkeypatch):
    def boom():
        raise bs.DistVolUnavailable("cannot connect to the DO database for table `distvol`")
    monkeypatch.setattr(bs, "distvol_db_connect", boom)
    with pytest.raises(bs.DistVolUnavailable):
        bs.build_lookup()


def test_explicit_xlsx_path_is_read_and_never_the_db(tmp_path, monkeypatch):
    def never():
        raise AssertionError("explicit path must not open the DB")
    monkeypatch.setattr(bs, "distvol_db_connect", never)
    p = tmp_path / "dv.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["SKU", "Name", "X", "Y", "Cubic Inches", "DistVol"])
    ws.append(["CH-ZZZ", "z", None, None, 105.3, None])        # DistVol derived from cubic inches
    ws.append(["AC-YYY", "y", None, None, None, 0.55])
    wb.save(p)
    lk = bs.build_lookup(p)
    assert lk["AC-YYY"] == 0.55 and round(lk["CH-ZZZ"], 2) == 1.0


def _order(name, *skus):
    return {"name": name, "customer": {}, "shippingAddress": {},
            "lineItems": {"edges": [{"node": {"sku": s, "currentQuantity": 1}} for s in skus]}}


def test_missing_distvol_is_a_named_warning_and_sizing_is_unchanged(capsys):
    lookup = {"CH-KNOWN": 0.5}
    orders = [_order("#1", "CH-KNOWN", "AC-NOROW"), _order("#2", "AC-NOROW"), _order("#3", "CH-KNOWN")]
    res = bs.simulate(orders, lookup)
    out = capsys.readouterr().out
    assert "WARNING: DistVol MISSING for AC-NOROW - 2 order(s)" in out
    assert "PREFIX_DEFAULT 0.12" in out
    assert "CH-KNOWN" not in out                       # only the guessed SKU is named
    by = {r["Order"]: r for r in res}
    assert by["#1"]["Total DistVol"] == round(0.5 + 0.12, 3)      # sizing itself unchanged
    assert by["#1"]["Flagged SKUs"] == "AC-NOROW" and by["#3"]["Flagged SKUs"] == ""


def test_no_missing_rows_prints_nothing(capsys):
    bs.simulate([_order("#1", "CH-KNOWN")], {"CH-KNOWN": 0.5})
    assert "WARNING" not in capsys.readouterr().out


def test_no_file_default_remains_in_the_module():
    src = Path(bs.__file__).read_text(encoding="utf-8")
    assert "DISTVOL_XLSX" not in src.split("class DistVolUnavailable")[1]
    assert "Onboarded Items with DistVol" not in src
