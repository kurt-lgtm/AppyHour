"""Tests for box_type schema migration + helpers in shipping_invoice_db."""

from __future__ import annotations

import os
import sys
import sqlite3
from pathlib import Path

import pytest

GELPACK_DIR = str(Path(__file__).resolve().parents[1] / "GelPackCalculator")
if GELPACK_DIR not in sys.path:
    sys.path.insert(0, GELPACK_DIR)

import shipping_invoice_db as sidb  # noqa: E402


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    """Fresh initialised DB in a tmp dir."""
    conn = sidb.init_db(str(tmp_path))
    yield conn
    conn.close()


def _seed(conn: sqlite3.Connection, rows: list[dict]) -> None:
    sidb.store_shipments(conn, rows)
    conn.commit()


class TestSchema:
    def test_box_type_column_present_on_fresh_db(self, db: sqlite3.Connection) -> None:
        cols = [r[1] for r in db.execute("PRAGMA table_info(shipments)").fetchall()]
        assert "box_type" in cols

    def test_idx_box_type_present(self, db: sqlite3.Connection) -> None:
        idxs = [r[1] for r in db.execute("PRAGMA index_list(shipments)").fetchall()]
        assert "idx_shipments_box_type" in idxs

    def test_migration_idempotent_on_reinit(self, tmp_path) -> None:
        sidb.init_db(str(tmp_path)).close()
        # Second init must not error
        conn = sidb.init_db(str(tmp_path))
        cols = [r[1] for r in conn.execute("PRAGMA table_info(shipments)").fetchall()]
        assert cols.count("box_type") == 1
        conn.close()

    def test_migration_adds_column_to_legacy_db(self, tmp_path) -> None:
        """Simulate a pre-migration DB (full pre-box_type schema) — migration must add the column."""
        path = tmp_path / "shipping.db"
        legacy = sqlite3.connect(path)
        # Mirror the shipments schema from before box_type was added
        legacy.execute("""
            CREATE TABLE shipments (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id      TEXT,
                tracking        TEXT NOT NULL,
                carrier         TEXT NOT NULL,
                service         TEXT,
                hub             TEXT,
                state           TEXT,
                zip_code        TEXT,
                city            TEXT,
                zone            TEXT,
                cost            REAL DEFAULT 0,
                weight          REAL,
                ship_date       TEXT,
                delivery_date   TEXT,
                transit_days    INTEGER,
                ship_dow        TEXT,
                source_file     TEXT
            )
        """)
        legacy.execute("INSERT INTO shipments (tracking, carrier) VALUES ('T1', 'FedEx')")
        legacy.commit()
        legacy.close()

        conn = sidb.init_db(str(tmp_path))
        cols = [r[1] for r in conn.execute("PRAGMA table_info(shipments)").fetchall()]
        assert "box_type" in cols
        row = conn.execute("SELECT tracking, box_type FROM shipments WHERE tracking=?", ("T1",)).fetchone()
        assert row["tracking"] == "T1"
        assert row["box_type"] is None
        conn.close()


class TestSetBoxType:
    def test_updates_existing_row(self, db: sqlite3.Connection) -> None:
        _seed(db, [{"tracking": "T1", "carrier": "FedEx"}])
        n = sidb.set_box_type(db, "T1", "TRAY")
        assert n == 1
        row = db.execute("SELECT box_type FROM shipments WHERE tracking=?", ("T1",)).fetchone()
        assert row["box_type"] == "TRAY"

    def test_noop_when_tracking_missing(self, db: sqlite3.Connection) -> None:
        assert sidb.set_box_type(db, "NOPE", "TRAY") == 0

    def test_bulk_update(self, db: sqlite3.Connection) -> None:
        _seed(db, [
            {"tracking": "T1", "carrier": "FedEx"},
            {"tracking": "T2", "carrier": "UPS"},
            {"tracking": "T3", "carrier": "OnTrac"},
        ])
        sidb.set_box_types_bulk(db, [("T1", "TRAY"), ("T2", "REGULAR_LARGE"), ("T3", "TRAY_LARGE")])
        got = {r["tracking"]: r["box_type"] for r in db.execute(
            "SELECT tracking, box_type FROM shipments ORDER BY tracking"
        )}
        assert got == {"T1": "TRAY", "T2": "REGULAR_LARGE", "T3": "TRAY_LARGE"}


class TestStoreShipmentsPassesBoxType:
    def test_box_type_persisted_on_insert(self, db: sqlite3.Connection) -> None:
        _seed(db, [{"tracking": "T1", "carrier": "FedEx", "box_type": "TRAY"}])
        row = db.execute("SELECT box_type FROM shipments WHERE tracking=?", ("T1",)).fetchone()
        assert row["box_type"] == "TRAY"

    def test_upsert_preserves_box_type_when_absent(self, db: sqlite3.Connection) -> None:
        """Existing classification must not get wiped on re-ingest without box_type."""
        _seed(db, [{"tracking": "T1", "carrier": "FedEx", "box_type": "TRAY"}])
        _seed(db, [{"tracking": "T1", "carrier": "FedEx", "cost": 42.0}])  # no box_type
        row = db.execute("SELECT box_type, cost FROM shipments WHERE tracking=?", ("T1",)).fetchone()
        assert row["box_type"] == "TRAY"
        assert row["cost"] == 42.0


class TestStatsByBoxType:
    def _seed_mixed(self, db: sqlite3.Connection) -> None:
        _seed(db, [
            # TRAY via FedEx — 3 shipments, 1 late
            {"tracking": "T1", "carrier": "FedEx", "box_type": "TRAY",
             "cost": 20.0, "transit_days": 1, "ship_date": "2026-04-01"},
            {"tracking": "T2", "carrier": "FedEx", "box_type": "TRAY",
             "cost": 22.0, "transit_days": 2, "ship_date": "2026-04-02"},
            {"tracking": "T3", "carrier": "FedEx", "box_type": "TRAY",
             "cost": 24.0, "transit_days": 4, "ship_date": "2026-04-03"},
            # REGULAR_LARGE via UPS — 2 shipments, all on time
            {"tracking": "T4", "carrier": "UPS", "box_type": "REGULAR_LARGE",
             "cost": 15.0, "transit_days": 2, "ship_date": "2026-04-01"},
            {"tracking": "T5", "carrier": "UPS", "box_type": "REGULAR_LARGE",
             "cost": 18.0, "transit_days": 1, "ship_date": "2026-04-02"},
        ])

    def test_groups_and_counts(self, db: sqlite3.Connection) -> None:
        self._seed_mixed(db)
        rows = sidb.stats_by_box_type(db)
        by_key = {(r["box_type"], r["carrier"]): r for r in rows}
        assert by_key[("TRAY", "FedEx")]["count"] == 3
        assert by_key[("REGULAR_LARGE", "UPS")]["count"] == 2

    def test_on_time_pct(self, db: sqlite3.Connection) -> None:
        self._seed_mixed(db)
        rows = sidb.stats_by_box_type(db)
        by_key = {(r["box_type"], r["carrier"]): r for r in rows}
        # TRAY/FedEx: 2 of 3 <= 2 days = 66.67%
        assert round(by_key[("TRAY", "FedEx")]["on_time_pct"], 1) == pytest.approx(66.7, abs=0.1)
        # REGULAR_LARGE/UPS: 2 of 2 = 100%
        assert by_key[("REGULAR_LARGE", "UPS")]["on_time_pct"] == pytest.approx(100.0)

    def test_since_filter(self, db: sqlite3.Connection) -> None:
        self._seed_mixed(db)
        rows = sidb.stats_by_box_type(db, since="2026-04-02")
        by_key = {(r["box_type"], r["carrier"]): r for r in rows}
        # Only 2 TRAY ship_date >= 2026-04-02 (T2 and T3)
        assert by_key[("TRAY", "FedEx")]["count"] == 2

    def test_unclassified_bucket(self, db: sqlite3.Connection) -> None:
        _seed(db, [{"tracking": "T1", "carrier": "FedEx", "cost": 10.0, "transit_days": 1}])
        rows = sidb.stats_by_box_type(db)
        assert rows[0]["box_type"] == "UNCLASSIFIED"


class TestViewIncludesBoxType:
    def test_shipment_details_view_exposes_box_type(self, db: sqlite3.Connection) -> None:
        cols = [r[1] for r in db.execute("PRAGMA table_info(shipment_details)").fetchall()]
        assert "box_type" in cols
