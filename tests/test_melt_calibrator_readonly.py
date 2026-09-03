"""melt_efficiency_calibrator must be a READ-ONLY shipping.db consumer.

Regression for the 2026-09-03 finding (Migration Triage, handoff migplan-track-20260831 item 3):
a weekly pure-analytics report held a write-capable handle — raw ``sqlite3.connect(DB_PATH)``
plus ``db_snapshots.init_schema(DB_PATH, force=True)`` (DDL + ALTER TABLE) — bypassing both the
advisory single-writer lock and the canonical-path guard. That is the "surplus write-capable
connection" class behind the three WAL corruptions. Scratch DB ONLY — never the live file.
"""
from __future__ import annotations

import ast
import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "ShippingReports" / "melt_efficiency_calibrator.py"


def _load():
    spec = importlib.util.spec_from_file_location("melt_efficiency_calibrator", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class StaticShape(unittest.TestCase):
    """The writer surface must not exist in the source at all — a runtime test can only prove the
    path it exercised; the AST proves every path."""

    def setUp(self):
        self.tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        self.src = MODULE.read_text(encoding="utf-8")

    def test_no_raw_sqlite3_connect_call(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                is_sqlite = isinstance(node.func.value, ast.Name) and node.func.value.id == "sqlite3"
                self.assertFalse(is_sqlite and node.func.attr == "connect",
                                 "raw sqlite3.connect() is a write-capable handle — use connect_ro")

    def test_no_schema_bootstrap_import_or_call(self):
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names]
                self.assertNotIn("init_schema", names)
                self.assertNotEqual(getattr(node, "module", None), "db_snapshots")
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
                self.assertNotEqual(name, "init_schema", "a report that migrates the schema is a writer")

    def test_no_hand_rolled_db_path(self):
        self.assertNotIn("AppData/Roaming/AppyHour/shipping.db", self.src)
        assigned = {t.id for n in self.tree.body if isinstance(n, ast.Assign)
                    for t in n.targets if isinstance(t, ast.Name)}
        self.assertNotIn("DB_PATH", assigned, "module-level DB path constant = hand-rolled resolution")

    def test_opens_via_connect_ro_from_appyhour_lib(self):
        imported = {(getattr(n, "module", None), a.name)
                    for n in ast.walk(self.tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        self.assertIn(("appyhour_lib.db", "connect_ro"), imported)
        self.assertIn(("appyhour_lib.paths", "db_path"), imported)


class ScratchRun(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "scratch.db"
        con = sqlite3.connect(self.db)
        con.executescript("""
            CREATE TABLE kori_snapshots(snapshot_id INTEGER, fulfilled_at TEXT);
            CREATE TABLE kori_snapshot_orders(snapshot_id INTEGER, order_number TEXT,
                predicted_risk REAL, predicted_config TEXT, effective_btu REAL, margin_btu REAL,
                transit_type TEXT);
            CREATE TABLE fulfillments(order_number TEXT, tracking_number TEXT);
            CREATE TABLE delivery_status(tracking_number TEXT, transit_days INTEGER);
            CREATE TABLE feedback(order_number TEXT, issue_type TEXT);
            INSERT INTO kori_snapshots VALUES (1, '2026-08-31');
            INSERT INTO kori_snapshot_orders VALUES (1, '170001', 0.1, 'G48', 100.0, 20.0, 'ground');
            INSERT INTO kori_snapshot_orders VALUES (1, '170002', 0.4, 'G48', 100.0, -10.0, 'ground');
            INSERT INTO fulfillments VALUES ('170002', 'T2');
            INSERT INTO delivery_status VALUES ('T2', 2);
            INSERT INTO delivery_status VALUES ('T2', 3);
            INSERT INTO feedback VALUES ('#170002', 'Arrived_Warm');
        """)
        con.commit()
        con.close()
        self.mod = _load()

    def tearDown(self):
        self._tmp.cleanup()

    def test_collect_dataset_reads_through_a_mode_ro_connection(self):
        from appyhour_lib.db import connect_ro
        con = connect_ro(self.db)
        con.row_factory = sqlite3.Row
        try:
            rows, n_cohorts = self.mod.collect_dataset(con)
            # mode=ro really is read-only: any write must be refused by SQLite itself
            with self.assertRaises(sqlite3.OperationalError):
                con.execute("INSERT INTO feedback VALUES ('#1', 'x')")
        finally:
            con.close()
        self.assertEqual(n_cohorts, 1)
        self.assertEqual(sorted(r["order_number"] for r in rows), ["170001", "170002"])
        self.assertEqual(sum(r["warm"] for r in rows), 1)
        # per-leg join collapsed to one row per order (join-grain class)
        self.assertEqual(len(rows), 2)

    def test_missing_thermal_columns_fail_loud_not_migrate(self):
        con = sqlite3.connect(self.db)
        con.executescript("DROP TABLE kori_snapshot_orders; "
                          "CREATE TABLE kori_snapshot_orders(snapshot_id INTEGER, order_number TEXT);")
        con.commit()
        con.close()
        from appyhour_lib.db import connect_ro
        ro = connect_ro(self.db)
        ro.row_factory = sqlite3.Row
        try:
            with self.assertRaises(sqlite3.OperationalError):
                self.mod.collect_dataset(ro)
        finally:
            ro.close()
        # and nothing "auto-migrated" the schema behind the reader's back
        chk = sqlite3.connect(self.db)
        cols = [r[1] for r in chk.execute("PRAGMA table_info(kori_snapshot_orders)")]
        chk.close()
        self.assertEqual(cols, ["snapshot_id", "order_number"])

    def test_insufficient_data_report_is_the_default(self):
        md = self.mod.build_report([{"warm": 0, "margin_btu": 1.0, "effective_btu": 2.0}], 1)
        self.assertIn("INSUFFICIENT DATA", md)


if __name__ == "__main__":
    sys.exit(unittest.main())
