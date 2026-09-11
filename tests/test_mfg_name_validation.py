"""MATRIX_RULES rule 21: a translation NAME outside the DO table `mfg_names_authoritative` is a hard
MfgOnboardingError — an invented header must never become a vF column (wk0803 CH-OTTA burn).

🔴 The authority is the DO MySQL table, never a csv (Kurt 2026-09-11). These tests are creds-free:
the ONE reader (`matrix_commander._mfg_from_db`) is driven through a fake connection, and an
unreachable table must raise BY NAME — the old warn-and-skip ("0 names checked" reads as "all
names valid") is the silent-degrade class this closes.
"""
import pytest

import matrix_commander as mc
from matrix_commander import (MfgAuthorityUnavailable, MfgOnboardingError, load_mfg_names,
                              load_mfg_translations, validate_mfg_names)

P = "AHB (S_REG): "
ROWS = [("CH-TETI", P + "Teti Packaged Slice"), ("CH-CUMIN", P + "Farmstead Cumin Gouda"),
        ("TR-ROME", P + "When in Rome"), ("PK-TCUST", P + "Tasting Guide - Custom Box")]


class _Cursor:
    def __init__(self, tables):
        self.tables, self.sql, self._rows = tables, [], []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        table = sql.split("FROM", 1)[1].split()[0]
        if table not in self.tables:
            raise RuntimeError(f"Table '{table}' doesn't exist")
        self._rows = list(self.tables[table])

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, tables):
        self.tables, self.closed = tables, False

    def cursor(self):
        return _Cursor(self.tables)

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _creds_free(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(mc, "MFG_AUTHORITY_OVERRIDE", None)


def _fake_db(monkeypatch, tables):
    conns = []

    def connect():
        c = _Conn(tables)
        conns.append(c)
        return c
    monkeypatch.setattr(mc, "mfg_db_connect", connect)
    return conns


def test_reader_reads_the_named_table_and_closes(monkeypatch):
    conns = _fake_db(monkeypatch, {"mfg_names_authoritative": ROWS, "mfg_translations": ROWS[:2]})
    assert load_mfg_names() == dict(ROWS)
    assert load_mfg_names("mfg_translations") == dict(ROWS[:2])
    assert load_mfg_translations() == dict(ROWS[:2])          # no path = the table, not a csv
    assert all(c.closed for c in conns)


def test_translations_validate_clean_against_the_table(monkeypatch):
    _fake_db(monkeypatch, {"mfg_names_authoritative": ROWS, "mfg_translations": ROWS})
    validate_mfg_names(load_mfg_translations())


def test_invented_name_rejected_by_type(monkeypatch):
    _fake_db(monkeypatch, {"mfg_names_authoritative": ROWS})
    bad = dict(ROWS)
    bad["CH-TETI"] = "AHB (S_REG): Cheese Slice, Frumage L'Ottavio"   # the wk0803 invented header
    with pytest.raises(MfgOnboardingError) as e:
        validate_mfg_names(bad)
    assert "CH-TETI" in e.value.skus
    assert isinstance(e.value, ValueError)   # existing except ValueError handlers keep catching it


@pytest.mark.parametrize("tables", [{}, {"mfg_names_authoritative": []}])
def test_unreachable_or_empty_table_is_fatal_by_name_never_a_csv(monkeypatch, tables):
    """No credential, no table, or an EMPTY table: raise naming the table. Never fall back to the
    local mirror — the mirror silently disagreed with the cloud on 2026-08-21."""
    _fake_db(monkeypatch, tables)
    with pytest.raises(MfgAuthorityUnavailable) as e:
        validate_mfg_names({"CH-TETI": ROWS[0][1]})
    assert "mfg_names_authoritative" in str(e.value)


def test_no_credential_is_fatal_by_name(monkeypatch):
    with pytest.raises(MfgAuthorityUnavailable) as e:
        load_mfg_names()
    assert "mfg_names_authoritative" in str(e.value) and "DATABASE_URL" in str(e.value)


def test_mirror_path_is_refused_as_a_source():
    with pytest.raises(MfgAuthorityUnavailable) as e:
        load_mfg_translations(mc.MFG_AUTHORITATIVE_PATH)
    assert "READ-MIRROR" in str(e.value)


def test_explicit_override_file_is_the_only_file_path(tmp_path, monkeypatch):
    p = tmp_path / "auth.csv"
    p.write_text("\n".join(f'{s},"{n}"' for s, n in ROWS) + "\n", encoding="utf-8")
    monkeypatch.setattr(mc, "MFG_AUTHORITY_OVERRIDE", p)          # what --authority binds
    assert load_mfg_names() == dict(ROWS)                          # no DB touched
    assert load_mfg_translations(p) == dict(ROWS)                  # explicit path = that file
