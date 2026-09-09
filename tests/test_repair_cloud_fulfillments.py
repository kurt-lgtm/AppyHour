"""Exercise `scripts/repair_cloud_fulfillments.py` by TRIGGERING its branches, not reading them.

🔴 WHY THIS FILE EXISTS. The repair script sat for days described in four handoffs as "ready"
while containing zero INSERT statements, and its gates were verified by reading. `py_compile` and
`ruff` both pass a `NameError` on a branch nothing ever enters — that class hit four separate
files on 2026-08-31 alone. So every refusal here is proven by CONSTRUCTING the condition and
asserting the run refuses, never by inspecting the code that would refuse.

🔴 WHAT THIS DOES **NOT** PROVE. The destination is a sqlite database behind a `%s`->`?` shim, not
MySQL. It proves the LOGIC — the anti-join, the '#'-normalization, the gates, batching,
commit-per-batch, resume, and byte-verbatim timestamp copying. It does NOT prove MySQL type
coercion (a DATETIME column silently truncating an ISO-with-offset string), pymysql's executemany
behaviour, real UNIQUE-index enforcement, or that the DO table's schema matches. Those need the
socket, which was closed from this machine on 2026-08-31 (`2003 ... timed out`). Do not read a
green run here as a cloud rehearsal.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import repair_cloud_fulfillments as R  # noqa: E402

DDL = """
CREATE TABLE fulfillments (
    id INTEGER PRIMARY KEY, order_number TEXT NOT NULL, order_id TEXT, order_date TEXT,
    tags TEXT, tracking_number TEXT NOT NULL, tracking_company TEXT, tracking_url TEXT,
    fulfilled_at TEXT, customer_name TEXT, dest_city TEXT, dest_state TEXT, dest_zip TEXT,
    updated_at TEXT, ship_date TEXT, ship_week TEXT)
"""


# --------------------------------------------------------------------- the fake MySQL side

class FakeCursor:
    """A sqlite cursor pretending to be pymysql: `%s` params, backticks, SHOW TABLES,
    information_schema. Deliberately thin — the point is to run the REAL script functions."""

    def __init__(self, con: sqlite3.Connection, fail_on=None, fail_with=RuntimeError,
                 column_extra=None, lie_rowcount=None):
        self._con = con
        self._cur = con.cursor()
        self._fail_on = fail_on          # int: raise on the Nth executemany (interrupt sim)
        self._fail_with = fail_with
        # `column_extra`: {column: information_schema EXTRA}. sqlite has no such concept, so a
        # MySQL-only hazard (`ON UPDATE CURRENT_TIMESTAMP`, the server-side repair clock) can only
        # be exercised by injecting it here.
        self._column_extra = column_extra or {}
        # `lie_rowcount`: force `rowcount` after executemany, to prove the run stops when an UPDATE
        # matched a number of rows other than one.
        self._lie_rowcount = lie_rowcount
        self._many = 0
        self._rowcount = -1
        self._rows: list = []

    @property
    def rowcount(self):
        return self._rowcount

    def execute(self, sql, params=()):
        s = sql.replace("`", '"')
        m = re.match(r"\s*SHOW TABLES LIKE '([^']+)'", s, re.I)
        if m:
            self._rows = self._cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (m.group(1),)).fetchall()
            return
        if "information_schema.columns" in s:
            table = params[0] if params else "fulfillments"
            names = [r[1] for r in
                     self._cur.execute(f'PRAGMA table_info("{table}")').fetchall()]
            if "extra" in s.lower():
                self._rows = [(n, self._column_extra.get(n, "")) for n in names]
            else:
                self._rows = [(n,) for n in names]
            return
        m = re.match(r'\s*CREATE TABLE "?(\w+)"? LIKE "?(\w+)"?', s, re.I)
        if m:
            new, src = m.groups()
            ddl = self._cur.execute(
                "SELECT sql FROM sqlite_master WHERE name=?", (src,)).fetchone()[0]
            self._cur.execute(ddl.replace(src, new, 1))
            self._rows = []
            return
        self._rows = self._cur.execute(s.replace("%s", "?"), params).fetchall()

    def executemany(self, sql, seq):
        self._many += 1
        if self._fail_on and self._many == self._fail_on:
            raise self._fail_with("simulated interrupt mid-run")
        self._cur.executemany(sql.replace("`", '"').replace("%s", "?"), seq)
        self._rowcount = (self._lie_rowcount if self._lie_rowcount is not None
                          else self._cur.rowcount)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeMySQLBase:
    """🔴 `busy_timeout` is load-bearing, not tidiness. Without it this suite failed roughly one
    run in eight: several of these handles are open on the same file at once (a test holds the
    interrupted connection while the resume connection writes), and sqlite intermittently raised
    `database is locked` on the second batch. That surfaced as `pytest.raises(... "simulated
    interrupt")` not matching — i.e. a flaky test in the middle of the safety proof, which is
    worth less than no test at all. The flake was in the harness; the script was never at fault.
    """

    def __init__(self, path: Path, fail_on=None, fail_with=RuntimeError,
                 column_extra=None, lie_rowcount=None):
        self.con = sqlite3.connect(path, timeout=30)
        self.con.execute("PRAGMA busy_timeout=30000")
        self.fail_on = fail_on
        self.fail_with = fail_with
        self.column_extra = column_extra
        self.lie_rowcount = lie_rowcount

    def cursor(self):
        return FakeCursor(self.con, self.fail_on, self.fail_with,
                          self.column_extra, self.lie_rowcount)

    def commit(self):
        self.con.commit()

    def rollback(self):
        self.con.rollback()

    def close(self):
        self.con.close()


FakeMySQL = FakeMySQLBase


# --------------------------------------------------------------------- fixtures

def _row(i: int, week: str, hashed: bool = False):
    """One fulfilment. `updated_at` naive, `fulfilled_at` ISO+offset — the real shapes, because
    the mixed naive/aware comparison is itself a branch under test."""
    n = 170000 + i
    mm, dd = week[5:7], int(week[8:10])
    return (i, f"#{n}" if hashed else str(n), f"gid{i}", "2026-08-01",
            f"_SHIP_{week}", f"TRK{i:06d}", "FedEx", f"https://x/{i}",
            f"2026-{mm}-{dd:02d}T05:38:04-04:00", f"Cust {i}",
            "CITY", "KY", "41472", f"2026-{mm}-{dd:02d} 20:28:17",
            week, week)


def _make(tmp_path, local_n=10, cloud_n=7, cloud_hashed=False, drop_col=None):
    """Local holds `local_n` rows; cloud holds the first `cloud_n` of them. The tail is the hole."""
    lp, cp = tmp_path / "local.db", tmp_path / "cloud.db"
    weeks = ["2026-08-17", "2026-08-24", "2026-08-31"]
    rows = [_row(i, weeks[i % 3]) for i in range(1, local_n + 1)]

    lc = sqlite3.connect(lp)
    lc.execute(DDL)
    lc.executemany("INSERT INTO fulfillments VALUES (" + ",".join("?" * 16) + ")", rows)
    lc.commit()
    lc.close()

    cc = sqlite3.connect(cp)
    ddl = DDL
    if drop_col:
        ddl = re.sub(rf",?\s*{drop_col} TEXT", "", ddl)
    cc.execute(ddl)
    cols = [c for c in R.COLS if c != drop_col]
    idx = [R.COLS.index(c) for c in cols]
    seed = []
    for r in rows[:cloud_n]:
        v = list(r)
        if cloud_hashed:
            v[1] = "#" + v[1]
        seed.append(tuple(v[i] for i in idx))
    cc.executemany("INSERT INTO fulfillments (" + ",".join(cols) + ") VALUES ("
                   + ",".join("?" * len(cols)) + ")", seed)
    cc.commit()
    cc.close()

    return sqlite3.connect(f"file:{lp.as_posix()}?mode=ro", uri=True), cp


@pytest.fixture(autouse=True)
def _reset_dest():
    R.DEST_TABLE = "fulfillments"
    yield
    R.DEST_TABLE = "fulfillments"


def _manifest(tmp_path) -> Path:
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"pre_write_measurement": {}}), encoding="utf-8")
    return p


def _named(gs, needle):
    return [g for g in gs if needle in g[0]][0]


# --------------------------------------------------------------------- measurement / dry run

def test_dry_run_counts_the_real_hole_and_all_gates_pass(tmp_path):
    lc, cp = _make(tmp_path, local_n=10, cloud_n=7)
    cc = FakeMySQL(cp)
    m = R.measure(lc, cc)

    assert m["missing_key_count"] == 3
    assert m["cloud_newer_count"] == 0
    assert m["local"]["rows"] == 10 and m["cloud"]["rows"] == 7
    gs = R.gates(m)
    assert all(g[1] for g in gs), [g for g in gs if not g[1]]

    # the dry run must emit the ACTUAL rows, and the count must agree with the anti-join
    delta = tmp_path / "delta.json"
    assert R.write_delta_file(lc, m, delta) == 3
    assert len(json.loads(delta.read_text(encoding="utf-8"))) == 3


def test_control_join_positive_and_negative(tmp_path):
    """The zero-is-a-claim control: a known-present key must be FOUND, and its '#'-form must not."""
    lc, cp = _make(tmp_path, local_n=10, cloud_n=7)
    ok, msg = R._control_join(lc, FakeMySQL(cp))
    assert ok, msg


# --------------------------------------------------------------------- refusal branches

def test_cloud_newer_row_makes_the_run_refuse(tmp_path):
    """🔴 THE gate. Fabricate a cloud row newer than its local twin; --apply must refuse."""
    lc, cp = _make(tmp_path, local_n=10, cloud_n=7)
    raw = sqlite3.connect(cp)
    raw.execute("UPDATE fulfillments SET updated_at='2099-01-01 00:00:00' WHERE id=1")
    raw.commit()
    raw.close()

    m = R.measure(lc, FakeMySQL(cp))
    assert m["cloud_newer_count"] == 1
    name, ok, detail = _named(R.gates(m), "cloud-newer")
    assert ok is False, detail
    assert not all(g[1] for g in R.gates(m))          # -> main() returns 2, nothing written


def test_mixed_naive_and_aware_timestamps_are_a_finding_not_a_crash(tmp_path):
    """A naive/aware pair is unorderable in Python. It must be REPORTED, never raise inside the
    gate — a comparison that throws is how a gate silently stops gating."""
    hit, note = R._cloud_is_newer("2026-08-31T05:38:04-04:00", "2026-08-31 20:28:17")
    assert hit is True and "not orderable" in note


def test_key_form_split_refuses_but_normalization_still_matches(tmp_path):
    """🔴 The '#' asymmetry, both directions at once: cloud stores '#172607', local '172607'.

    The anti-join must still see only 3 missing rows (normalization works — a silent format split
    must NOT make all 7 shared rows look absent and get inserted twice), AND the run must still
    REFUSE, because writing a bare key into a '#'-keyed table mints a second identity.
    """
    lc, cp = _make(tmp_path, local_n=10, cloud_n=7, cloud_hashed=True)
    m = R.measure(lc, FakeMySQL(cp))

    assert m["missing_key_count"] == 3, "normalization failed -> would have inserted duplicates"
    assert m["key_form_census"]["cloud"]["form"] == "hash"
    assert m["key_form_census"]["local"]["form"] == "bare"
    name, ok, detail = _named(R.gates(m), "key FORM")
    assert ok is False, detail


def test_missing_cloud_column_refuses_BEFORE_measure_touches_it(tmp_path):
    """🔴 Ordering: `measure()` selects ship_week, so the contract check must run first.

    The first version of this test failed with `OperationalError: no such column: ship_week` —
    `measure()` crashed before `gates()` could report the very thing that was wrong. The gate is
    now a pre-flight, and this asserts both halves: the pre-flight refuses, and the crash it
    prevents is real.
    """
    lc, cp = _make(tmp_path, local_n=10, cloud_n=7, drop_col="ship_week")
    ok, msg = R.column_contract_gate(FakeMySQL(cp))
    assert ok is False
    assert "ship_week" in msg
    assert R._column_contract(FakeMySQL(cp))[0] == ["ship_week"]

    with pytest.raises(sqlite3.OperationalError, match="ship_week"):
        R.measure(lc, FakeMySQL(cp))


def test_column_contract_passes_on_a_healthy_schema(tmp_path):
    _lc, cp = _make(tmp_path)
    ok, msg = R.column_contract_gate(FakeMySQL(cp))
    assert ok is True, msg


def test_no_hole_is_reported_as_nothing_to_do(tmp_path):
    lc, cp = _make(tmp_path, local_n=10, cloud_n=10)
    m = R.measure(lc, FakeMySQL(cp))
    assert m["missing_key_count"] == 0
    name, ok, _ = _named(R.gates(m), "hole to fill")
    assert ok is False


# --------------------------------------------------------------------- the SQL itself

def test_generated_sql_is_insert_only_and_names_every_column():
    sql = R.build_insert_sql("fulfillments")
    R._assert_insert_only(sql)                       # must not raise
    assert sql.upper().startswith("INSERT INTO")
    for c in R.COLS:
        assert f"`{c}`" in sql
    assert sql.count("%s") == len(R.COLS)


@pytest.mark.parametrize("bad", [
    "INSERT INTO `t` (a) VALUES (NOW())",                       # rule 18a: repair clock
    "INSERT INTO `t` (a) VALUES (%s) ON DUPLICATE KEY UPDATE a=1",   # would modify a cloud row
    "REPLACE INTO `t` (a) VALUES (%s)",                         # delete+insert in disguise
    "INSERT IGNORE INTO `t` (a) VALUES (%s)",                   # swallows the errors that matter
    "UPDATE `t` SET a=%s",
    "INSERT INTO `t` (a) VALUES (CURRENT_TIMESTAMP)",
])
def test_mutating_or_clock_stamping_sql_is_refused(bad):
    with pytest.raises(RuntimeError, match="insert-only"):
        R._assert_insert_only(bad)


# --------------------------------------------------------------------- the write

def test_insert_copies_timestamps_byte_identically_and_is_idempotent(tmp_path):
    """Contracts 1 and 3, proven by reading the destination back."""
    lc, cp = _make(tmp_path, local_n=10, cloud_n=7)
    cc = FakeMySQL(cp)
    man_path = _manifest(tmp_path)

    m = R.measure(lc, cc)
    man = R.insert_missing(lc, cc, m, man_path, batch=2)
    assert man["inserted_count"] == 3
    assert man["status"] == "complete"
    assert len(man["inserted_keys"]) == 3
    assert len(man["batches"]) == 2                  # 2 + 1, committed separately

    # byte-identical timestamps, read back from the destination
    raw = sqlite3.connect(cp)
    for on, tn in [tuple(k) for k in man["inserted_keys"]]:
        src = lc.execute("SELECT updated_at, fulfilled_at FROM fulfillments "
                         "WHERE order_number=? AND tracking_number=?", (on, tn)).fetchone()
        got = raw.execute("SELECT updated_at, fulfilled_at FROM fulfillments "
                          "WHERE order_number=? AND tracking_number=?", (on, tn)).fetchone()
        assert got == src, f"timestamp rewritten for {on}/{tn}: {got} != {src}"
    assert raw.execute("SELECT COUNT(*) FROM fulfillments").fetchone()[0] == 10
    raw.close()

    # 🔴 IDEMPOTENT: re-measure, re-run, nothing left to do and no duplicate rows.
    m2 = R.measure(lc, cc)
    assert m2["missing_key_count"] == 0
    man2 = R.insert_missing(lc, cc, m2, _manifest(tmp_path), batch=2)
    assert man2["inserted_count"] == 0
    raw = sqlite3.connect(cp)
    assert raw.execute("SELECT COUNT(*) FROM fulfillments").fetchone()[0] == 10
    assert raw.execute("SELECT COUNT(*) FROM (SELECT order_number, tracking_number "
                       "FROM fulfillments GROUP BY 1,2 HAVING COUNT(*)>1)").fetchone()[0] == 0
    raw.close()


def test_interrupted_run_banks_progress_and_resumes_without_duplicates(tmp_path):
    """🔴 Contract 4, the `delivery_status` lesson: an interrupt must not lose committed work, and
    the resume must not re-insert it."""
    lc, cp = _make(tmp_path, local_n=10, cloud_n=7)
    man_path = tmp_path / "m1.json"
    man_path.write_text("{}", encoding="utf-8")

    dying = FakeMySQL(cp, fail_on=3)                 # batch 1 and 2 commit, batch 3 raises
    m = R.measure(lc, dying)
    with pytest.raises(RuntimeError, match="simulated interrupt"):
        R.insert_missing(lc, dying, m, man_path, batch=1)

    banked = json.loads(man_path.read_text(encoding="utf-8"))
    assert banked["status"] == "FAILED"
    assert banked["inserted_count"] == 2             # progress was BANKED, not rolled back
    assert banked["failed_batch_keys"]               # the undo record is still exact
    raw = sqlite3.connect(cp)
    assert raw.execute("SELECT COUNT(*) FROM fulfillments").fetchone()[0] == 9
    raw.close()

    # resume = just run it again; the anti-join skips what landed.
    # The interrupted connection is CLOSED first — a real interrupt loses its process, and
    # leaving it open here is what let two writers race the same sqlite file.
    dying.close()
    healthy = FakeMySQL(cp)
    m2 = R.measure(lc, healthy)
    assert m2["missing_key_count"] == 1
    man2 = R.insert_missing(lc, healthy, m2, _manifest(tmp_path), batch=1)
    assert man2["inserted_count"] == 1

    raw = sqlite3.connect(cp)
    assert raw.execute("SELECT COUNT(*) FROM fulfillments").fetchone()[0] == 10
    assert raw.execute("SELECT COUNT(*) FROM (SELECT order_number, tracking_number "
                       "FROM fulfillments GROUP BY 1,2 HAVING COUNT(*)>1)").fetchone()[0] == 0
    raw.close()


@pytest.mark.parametrize("exc,expected_status", [
    (KeyboardInterrupt, "INTERRUPTED"),      # Ctrl+C / kill — NOT caught by `except Exception`
    (SystemExit, "INTERRUPTED"),
    (RuntimeError, "FAILED"),                # a genuine SQL/driver error
])
def test_a_real_kill_still_marks_the_manifest(tmp_path, exc, expected_status):
    """🔴 Found against LIVE MySQL: the committed batches were banked correctly but the manifest
    still said `status: running`, because `KeyboardInterrupt` is a BaseException. The durable
    record of a dead run claimed it was still going — the keys were never at risk, the ability to
    TELL was."""
    lc, cp = _make(tmp_path, local_n=10, cloud_n=7)
    man_path = tmp_path / "m.json"
    man_path.write_text("{}", encoding="utf-8")

    dying = FakeMySQL(cp, fail_on=3, fail_with=exc)
    m = R.measure(lc, dying)
    with pytest.raises(exc):
        R.insert_missing(lc, dying, m, man_path, batch=1)

    man = json.loads(man_path.read_text(encoding="utf-8"))
    assert man["status"] == expected_status
    assert man["inserted_count"] == 2          # progress still banked
    assert len(man["failed_batch_keys"]) == 1  # and the undo record is still exact
    dying.close()


def test_existing_cloud_rows_are_never_modified(tmp_path):
    """Contract 2 as an observation, not an intention: snapshot every shared row before the write
    and assert it is byte-identical after."""
    lc, cp = _make(tmp_path, local_n=10, cloud_n=7)
    raw = sqlite3.connect(cp)
    raw.execute("UPDATE fulfillments SET tags='CLOUD-ONLY-EDIT' WHERE id<=7")
    raw.commit()
    before = raw.execute("SELECT * FROM fulfillments ORDER BY id").fetchall()
    raw.close()

    cc = FakeMySQL(cp)
    m = R.measure(lc, cc)
    R.insert_missing(lc, cc, m, _manifest(tmp_path), batch=2)

    raw = sqlite3.connect(cp)
    after = raw.execute("SELECT * FROM fulfillments ORDER BY id LIMIT 7").fetchall()
    raw.close()
    assert after == before, "an existing cloud row was modified — insert-only was violated"


# --------------------------------------------------------------------- guards

def test_scratch_table_refuses_the_live_name(tmp_path):
    _lc, cp = _make(tmp_path)
    cc = FakeMySQL(cp)
    with pytest.raises(RuntimeError, match="REFUSED"):
        R.prepare_scratch(cc, "fulfillments", reset=False)
    with pytest.raises(RuntimeError, match="REFUSED"):
        R.prepare_scratch(cc, "fulfillments_backup", reset=False)   # not clearly scratch


def test_scratch_table_seeds_a_full_copy(tmp_path):
    _lc, cp = _make(tmp_path, local_n=10, cloud_n=7)
    cc = FakeMySQL(cp)
    R.prepare_scratch(cc, "fulfillments_repair_scratch", reset=True)
    raw = sqlite3.connect(cp)
    assert raw.execute("SELECT COUNT(*) FROM fulfillments_repair_scratch").fetchone()[0] == 7
    raw.close()


def test_path_guard_refuses_a_foreign_shipping_db(tmp_path):
    """🔴 Three WAL corruptions: the canonical DB has exactly one home."""
    fake = tmp_path / "shipping.db"
    sqlite3.connect(fake).close()
    with pytest.raises(RuntimeError, match="not in"):
        R.local_con(str(fake))


def test_source_db_accepts_a_snapshot_under_any_other_name(tmp_path):
    snap = tmp_path / "snap.db"
    con = sqlite3.connect(snap)
    con.execute(DDL)
    con.commit()
    con.close()
    ro = R.local_con(str(snap))
    assert ro.execute("SELECT COUNT(*) FROM fulfillments").fetchone()[0] == 0
    with pytest.raises(sqlite3.OperationalError):     # proves mode=ro
        ro.execute("INSERT INTO fulfillments (order_number, tracking_number) VALUES ('1','2')")
    ro.close()


# ============================================================ `--update-stale` (the reship class)
#
# 🔴 WHY THESE EXIST. On 2026-09-08 the insert path ran clean — 2,703 rows in, local rows == cloud
# rows == 124,078, local-only 0, cloud-only 0 — and `--verify` STILL failed per-ship-week parity on
# five weeks. Cause: 11 RESHIPPED boxes whose `ship_date` local had moved and cloud had not,
# because an INSERT structurally cannot touch a key that already exists. The 11 are finite; the
# class is not — every future reship re-opens it. Each refusal below is proven by CONSTRUCTING the
# condition, never by reading the code that would refuse.

def _drift_cloud(cp: Path, ids, **cols):
    """Make cloud's copy of rows `ids` disagree with local — the reship shape, or any other."""
    raw = sqlite3.connect(cp)
    sets = ",".join(f"{c}=?" for c in cols)
    for i in ids:
        raw.execute(f"UPDATE fulfillments SET {sets} WHERE id=?", (*cols.values(), i))
    raw.commit()
    raw.close()


def _reship_drift(tmp_path, n_drifted=2, local_n=10, cloud_n=10):
    """The measured live shape: cloud holds an EARLIER ship_date/ship_week, stale `tags` naming the
    old `_SHIP_` week, and an OLDER `updated_at` — i.e. local is unambiguously the newer copy."""
    lc, cp = _make(tmp_path, local_n=local_n, cloud_n=cloud_n)
    _drift_cloud(cp, range(1, n_drifted + 1),
                 ship_date="2026-07-20", ship_week="2026-07-20",
                 tags="_SHIP_2026-07-20, _SHIP_2026-07-27, RMFG",
                 updated_at="2026-08-12 05:07:22")
    return lc, cp


def test_update_stale_finds_exactly_the_drifted_rows_and_names_the_columns(tmp_path):
    lc, cp = _reship_drift(tmp_path, n_drifted=2)
    sm = R.measure_stale(lc, FakeMySQL(cp))

    assert sm["stale_count"] == 2
    assert sm["cloud_newer_count"] == 0 and sm["immutable_diff_count"] == 0
    for e in sm["stale"]:
        assert e["triggered_by"] == ["ship_date", "ship_week"]
        # 🔴 the WRITE set is every column that ACTUALLY differs, and NEVER an identity column
        assert set(e["update_cols"]) == {"tags", "updated_at", "ship_date", "ship_week"}
        assert not set(e["update_cols"]) & set(R.IMMUTABLE_COLS)
        assert e["diffs"]["ship_date"]["cloud"] == "2026-07-20"
    assert all(g[1] for g in R.update_gates(sm, max_rows=500)), R.update_gates(sm, 500)
    assert R.stale_table(sm).count("~ order=") == 2


def test_a_row_where_only_one_column_differs_updates_only_that_column(tmp_path):
    """🔴 Never a blind whole-row overwrite: the SET list is the diff, and nothing else."""
    lc, cp = _make(tmp_path, local_n=10, cloud_n=10)
    _drift_cloud(cp, [3], ship_date="2026-07-20")     # ship_week, tags, updated_at all still agree
    cc = FakeMySQL(cp)
    sm = R.measure_stale(lc, cc)

    assert sm["stale_count"] == 1
    e = sm["stale"][0]
    assert e["update_cols"] == ["ship_date"], e["update_cols"]
    assert R.build_update_sql("fulfillments", e["update_cols"]).count("%s") == 3

    before = sqlite3.connect(cp).execute(
        "SELECT * FROM fulfillments WHERE id=3").fetchone()
    R.update_stale_rows(cc, sm, _manifest(tmp_path), batch=10)
    after = sqlite3.connect(cp).execute("SELECT * FROM fulfillments WHERE id=3").fetchone()
    changed = [c for i, c in enumerate(R.COLS) if before[i] != after[i]]
    assert changed == ["ship_date"], f"columns nobody compared were rewritten: {changed}"


def test_a_cloud_newer_candidate_refuses_the_whole_run(tmp_path):
    """🔴 A newer cloud row is a cloud writer this repair does not know about. Refuse, never merge —
    the one rule the insert path could enforce for free (it cannot overwrite) and this one cannot."""
    lc, cp = _reship_drift(tmp_path, n_drifted=2)
    _drift_cloud(cp, [1], updated_at="2099-01-01 00:00:00")

    sm = R.measure_stale(lc, FakeMySQL(cp))
    assert sm["cloud_newer_count"] == 1
    assert sm["stale_count"] == 1, "a cloud-newer row must NOT be queued for update"
    name, ok, detail = _named(R.update_gates(sm, max_rows=500), "CLOUD-NEWER")
    assert ok is False, detail
    assert not all(g[1] for g in R.update_gates(sm, max_rows=500))


def test_max_rows_refuses_and_never_truncates(tmp_path):
    """🔴 The ceiling REFUSES. A cap that trimmed the batch to fit would turn 'bigger than you
    thought' into a silent partial write."""
    lc, cp = _reship_drift(tmp_path, n_drifted=5)
    sm = R.measure_stale(lc, FakeMySQL(cp))
    assert sm["stale_count"] == 5

    name, ok, detail = _named(R.update_gates(sm, max_rows=4), "--max-rows")
    assert ok is False and "stale=5" in detail
    assert len(sm["stale"]) == 5, "the plan was truncated instead of refused"
    assert all(g[1] for g in R.update_gates(sm, max_rows=5))      # exactly at the ceiling passes


def test_no_stale_rows_is_a_clean_no_op(tmp_path):
    """Nothing drifted: every gate but 'there is work' passes, which is `main()`'s exit-0 branch —
    the same shape as the insert path's idempotency rule."""
    lc, cp = _make(tmp_path, local_n=10, cloud_n=10)
    cc = FakeMySQL(cp)
    sm = R.measure_stale(lc, cc)
    assert sm["stale_count"] == 0 and sm["cloud_newer_count"] == 0

    gs = R.update_gates(sm, max_rows=500)
    assert all(ok for name, ok, _ in gs if "stale drift" not in name)
    assert _named(gs, "stale drift")[1] is False
    # 🔴 ...and the clean no-op is only allowed to READ clean because the join is proven alive.
    assert _named(gs, "stale join MATCHED")[1] is True
    assert sm["shared_rows"] == 10 and sm["local_rows"] == 10 and sm["cloud_rows"] == 10

    before = sqlite3.connect(cp).execute("SELECT * FROM fulfillments ORDER BY id").fetchall()
    man = R.update_stale_rows(cc, sm, _manifest(tmp_path), batch=10)
    assert man["updated_count"] == 0 and man["status"] == "complete"
    after = sqlite3.connect(cp).execute("SELECT * FROM fulfillments ORDER BY id").fetchall()
    assert after == before


def test_a_dead_stale_join_refuses_instead_of_reporting_zero_drift(tmp_path):
    """🔴 A ZERO IS A CLAIM. This is the failure the gate exists for: the key stops lining up, every
    cloud row misses the dict, and the run reports `stale=0` — indistinguishable from a healthy
    table, on the exact '#'-asymmetry key format that has produced confident zeros three times.

    Without the gate this is a green run that says "no shared row has drifted" and exits 0.
    """
    lc, cp = _reship_drift(tmp_path, n_drifted=2)
    # Break the join the way it actually breaks: cloud's key gains a prefix local does not have.
    con = sqlite3.connect(cp)
    con.execute("UPDATE fulfillments SET order_number = 'X' || order_number")
    con.commit()
    con.close()

    sm = R.measure_stale(lc, FakeMySQL(cp))
    assert sm["stale_count"] == 0, "precondition: a broken join looks exactly like a clean table"
    assert sm["shared_rows"] == 0 and sm["local_rows"] > 0 and sm["cloud_rows"] > 0

    name, ok, detail = _named(R.update_gates(sm, max_rows=500), "stale join MATCHED")
    assert ok is False, detail
    assert "shared keys=0" in detail
    # 🔴 and it must block main()'s idempotent-success branch, which excludes only the work gate
    assert not all(ok_ for n, ok_, _ in R.update_gates(sm, max_rows=500) if "stale drift" not in n)


def test_an_empty_side_refuses_rather_than_measuring_zero_drift(tmp_path):
    """An empty side makes `stale=0` an artifact of having nothing to compare, not a measurement —
    a `--source-db` pointed at a snapshot of the wrong table reaches here."""
    lc, cp = _make(tmp_path, local_n=10, cloud_n=10)
    con = sqlite3.connect(cp)
    con.execute("DELETE FROM fulfillments")
    con.commit()
    con.close()

    sm = R.measure_stale(lc, FakeMySQL(cp))
    assert sm["stale_count"] == 0 and sm["cloud_rows"] == 0
    name, ok, detail = _named(R.update_gates(sm, max_rows=500), "stale join MATCHED")
    assert ok is False and "EMPTY" in detail


def test_an_identity_column_difference_is_a_finding_not_a_repair(tmp_path):
    """`id` disagreeing means the mirror assumption is already broken. Refuse; do not 'fix' it."""
    lc, cp = _reship_drift(tmp_path, n_drifted=1)
    raw = sqlite3.connect(cp)
    raw.execute("UPDATE fulfillments SET id=9001 WHERE id=1")
    raw.commit()
    raw.close()

    sm = R.measure_stale(lc, FakeMySQL(cp))
    assert sm["immutable_diff_count"] == 1
    assert sm["stale_count"] == 0, "an identity-diff row must NOT be queued for update"
    assert _named(R.update_gates(sm, max_rows=500), "identity column")[1] is False


def test_server_side_on_update_clock_refuses_the_run(tmp_path):
    """🔴 Rule 18a's blind spot: `ON UPDATE CURRENT_TIMESTAMP` stamps the repair's own time with
    NOTHING in the statement to inspect, and `fulfillments` has no provenance column to tell the
    difference from a live writer."""
    _lc, cp = _make(tmp_path)
    ok, msg = R._server_clock_gate(FakeMySQL(cp))
    assert ok is True, msg

    dirty = FakeMySQL(cp, column_extra={"updated_at": "on update CURRENT_TIMESTAMP"})
    ok, msg = R._server_clock_gate(dirty)
    assert ok is False and "updated_at" in msg


def test_stale_on_refuses_identity_and_unknown_columns():
    assert R._validate_stale_on(["ship_date"]) == ["ship_date"]
    for bad in R.IMMUTABLE_COLS:
        with pytest.raises(ValueError, match="identity column"):
            R._validate_stale_on([bad])
    with pytest.raises(ValueError, match="not a column"):
        R._validate_stale_on(["nope"])
    with pytest.raises(ValueError):
        R._validate_stale_on([])


# --------------------------------------------------------------------- the UPDATE statement

def test_generated_update_sql_is_scoped_parameterized_and_key_addressed():
    sql = R.build_update_sql("fulfillments", ["ship_date", "ship_week"])
    R._assert_update_only(sql, ["ship_date", "ship_week"])       # must not raise
    assert sql.startswith("UPDATE `fulfillments` SET ")
    assert sql.endswith("WHERE `order_number`=%s AND `tracking_number`=%s")
    assert sql.count("%s") == 4


@pytest.mark.parametrize("sql,cols", [
    ("UPDATE `t` SET `ship_date`=NOW() WHERE `order_number`=%s AND `tracking_number`=%s",
     ["ship_date"]),                                                  # rule 18a: repair clock
    ("UPDATE `t` SET `ship_date`=CURRENT_TIMESTAMP WHERE `order_number`=%s AND "
     "`tracking_number`=%s", ["ship_date"]),
    ("UPDATE `t` SET `ship_date`=%s", ["ship_date"]),                 # no WHERE — unbounded
    ("UPDATE `t` SET `ship_date`=%s WHERE `ship_week`=%s AND `tracking_number`=%s",
     ["ship_date"]),                                                  # WHERE is not the key
    ("UPDATE `t` SET `ship_date`='2026-07-27' WHERE `order_number`=%s AND `tracking_number`=%s",
     ["ship_date"]),                                                  # literal, not a parameter
    ("UPDATE `t` SET `ship_date`=%s WHERE `order_number`=%s AND `tracking_number`=%s LIMIT 1",
     ["ship_date"]),                                                  # silent partial write
    ("DELETE FROM `t` WHERE `order_number`=%s AND `tracking_number`=%s", ["ship_date"]),
    ("UPDATE `t` JOIN `u` SET `ship_date`=%s WHERE `order_number`=%s AND `tracking_number`=%s",
     ["ship_date"]),
])
def test_unsafe_update_sql_is_refused(sql, cols):
    with pytest.raises(RuntimeError, match="REFUSED"):
        R._assert_update_only(sql, cols)


@pytest.mark.parametrize("col", R.IMMUTABLE_COLS)
def test_an_update_that_would_rewrite_the_identity_is_refused(col):
    """Rewriting the key mints a second identity for one fulfilment — the '#'-asymmetry failure in
    a different costume."""
    sql = R.build_update_sql("fulfillments", [col])
    with pytest.raises(RuntimeError, match="identity-preserving"):
        R._assert_update_only(sql, [col])


def test_insert_only_assertion_is_untouched_by_the_update_mode():
    """🔴 The update path does NOT borrow a hole in the insert path's guard. `_assert_insert_only`
    must still reject an UPDATE, even though UPDATE is now a verb this module legitimately emits."""
    R._assert_insert_only(R.build_insert_sql("fulfillments"))
    with pytest.raises(RuntimeError, match="insert-only"):
        R._assert_insert_only(R.build_update_sql("fulfillments", ["ship_date"]))


# --------------------------------------------------------------------- the UPDATE write

def test_update_writes_only_the_diff_leaves_other_rows_alone_and_is_idempotent(tmp_path):
    lc, cp = _reship_drift(tmp_path, n_drifted=2)
    cc = FakeMySQL(cp)
    man_path = _manifest(tmp_path)

    untouched_before = sqlite3.connect(cp).execute(
        "SELECT * FROM fulfillments WHERE id>2 ORDER BY id").fetchall()

    sm = R.measure_stale(lc, cc)
    man = R.update_stale_rows(cc, sm, man_path, batch=1)
    assert man["updated_count"] == 2 and man["status"] == "complete"
    assert len(man["batches"]) == 2                      # commit-per-batch, not one big write
    assert man["updated_keys"] == [e["key"] for e in sm["stale"]]

    raw = sqlite3.connect(cp)
    for e in sm["stale"]:
        got = raw.execute("SELECT " + ",".join(R.COLS) + " FROM fulfillments WHERE "
                          "order_number=? AND tracking_number=?", tuple(e["key"])).fetchone()
        for i, c in enumerate(R.COLS):
            if c in e["update_cols"]:
                assert str(got[i]) == e["diffs"][c]["local"], f"{c} not repaired"
    # 🔴 no repair clock: the value written is LOCAL's string, byte-for-byte, not this run's time
    assert raw.execute("SELECT COUNT(*) FROM fulfillments").fetchone()[0] == 10
    assert raw.execute("SELECT * FROM fulfillments WHERE id>2 ORDER BY id"
                       ).fetchall() == untouched_before
    raw.close()

    sm2 = R.measure_stale(lc, cc)
    assert sm2["stale_count"] == 0, "the repair is not idempotent"
    assert R.update_stale_rows(cc, sm2, _manifest(tmp_path), batch=1)["updated_count"] == 0


def test_a_batch_that_matches_the_wrong_number_of_rows_is_rolled_back(tmp_path):
    """🔴 Each UPDATE must hit exactly one row. A rowcount that disagrees means the natural key is
    not behaving like a key against this table — that has to stop the run BEFORE the commit."""
    lc, cp = _reship_drift(tmp_path, n_drifted=2)
    cc = FakeMySQL(cp, lie_rowcount=7)
    man_path = _manifest(tmp_path)
    sm = R.measure_stale(lc, cc)

    before = sqlite3.connect(cp).execute("SELECT * FROM fulfillments ORDER BY id").fetchall()
    with pytest.raises(RuntimeError, match="affected"):
        R.update_stale_rows(cc, sm, man_path, batch=2)

    after = sqlite3.connect(cp).execute("SELECT * FROM fulfillments ORDER BY id").fetchall()
    assert after == before, "a batch with a bad rowcount was committed anyway"
    man = json.loads(man_path.read_text(encoding="utf-8"))
    assert man["status"] == "FAILED" and man["updated_count"] == 0
    assert man["failed_batch_keys"]


@pytest.mark.parametrize("exc,expected_status", [
    (KeyboardInterrupt, "INTERRUPTED"),
    (SystemExit, "INTERRUPTED"),
    (RuntimeError, "FAILED"),
])
def test_an_interrupted_update_banks_progress_and_marks_the_manifest(tmp_path, exc,
                                                                    expected_status):
    lc, cp = _reship_drift(tmp_path, n_drifted=3)
    man_path = _manifest(tmp_path)
    dying = FakeMySQL(cp, fail_on=3, fail_with=exc)
    sm = R.measure_stale(lc, dying)

    with pytest.raises(exc):
        R.update_stale_rows(dying, sm, man_path, batch=1)
    man = json.loads(man_path.read_text(encoding="utf-8"))
    assert man["status"] == expected_status
    assert man["updated_count"] == 2          # the committed batches were BANKED
    assert len(man["failed_batch_keys"]) == 1
    dying.close()

    # resume = re-run. `measure_stale` re-reads cloud, so a repaired row is no longer stale.
    healthy = FakeMySQL(cp)
    sm2 = R.measure_stale(lc, healthy)
    assert sm2["stale_count"] == 1
    assert R.update_stale_rows(healthy, sm2, _manifest(tmp_path), batch=1)["updated_count"] == 1
    assert R.measure_stale(lc, healthy)["stale_count"] == 0


def test_the_stale_delta_file_names_every_row_before_the_write(tmp_path):
    """A count is not a plan, and this file is also the undo record: it holds CLOUD's prior value
    for every column the write would touch."""
    lc, cp = _reship_drift(tmp_path, n_drifted=2)
    sm = R.measure_stale(lc, FakeMySQL(cp))
    p = tmp_path / "stale_delta.json"
    assert R.write_stale_delta_file(sm, p) == 2
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert len(doc["stale"]) == 2
    for e in doc["stale"]:
        assert e["diffs"]["ship_date"]["cloud"] == "2026-07-20"
        assert e["diffs"]["ship_date"]["local"] != "2026-07-20"


def test_verify_reports_lingering_drift_when_asked(tmp_path):
    """The per-ship-week check is what failed live on 2026-09-08; the stale check is the diagnosis
    that names WHICH rows did it."""
    lc, cp = _reship_drift(tmp_path, n_drifted=2)
    cc = FakeMySQL(cp)
    ok, report = R.verify(lc, cc, stale_on=R.DEFAULT_STALE_ON)
    assert ok is False
    assert "per-ship-week parity" in report and "FAIL" in report
    assert "no shared row drifted" in report

    sm = R.measure_stale(lc, cc)
    R.update_stale_rows(cc, sm, _manifest(tmp_path), batch=5)
    ok2, report2 = R.verify(lc, cc, stale_on=R.DEFAULT_STALE_ON)
    assert ok2 is True, report2


def test_normalization_is_for_matching_only():
    assert R._norm("#172607") == "172607"
    assert R._norm("172607") == "172607"
    assert R._norm(None) == ""
