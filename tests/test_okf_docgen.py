"""Tests for the OKF doc-gen (docgen/okf_docgen.py). Isolated — no real shipping.db."""
from __future__ import annotations

import sqlite3

from docgen.okf_docgen import Concept, ShippingSchemaSource, Source, write_bundle


class _FakeSource(Source):
    name = "fake"

    def list_concepts(self):
        return ["a", "sub/b"]

    def read_concept(self, cid):
        return Concept(concept_id=cid, type="thing", title=cid.upper(),
                       description=f"desc {cid}", body="body text",
                       links=[("other", "a")] if cid == "sub/b" else [])


def test_write_bundle_emits_okf(tmp_path):
    res = write_bundle(_FakeSource(), str(tmp_path))
    assert res["concepts"] == 2
    # every concept doc + index exists with OKF frontmatter
    idx = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert idx.startswith("---\ntype: ")
    assert "okf_version:" in idx                       # root carries version
    a = (tmp_path / "a.md").read_text(encoding="utf-8")
    assert 'type: "thing"' in a and 'title: "A"' in a
    b = (tmp_path / "sub" / "b.md").read_text(encoding="utf-8")   # nested concept path
    assert "[other](a.md)" in b                        # markdown link, not wikilink


def test_schema_source_is_readonly_and_factual(tmp_path):
    # build a throwaway sqlite db; the Source must READ it and invent nothing
    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY, sku TEXT NOT NULL)")
    con.execute("INSERT INTO widgets (sku) VALUES ('CH-FONT'), ('CH-FONTAL')")
    con.commit()
    con.close()

    src = ShippingSchemaSource(db_path=str(db))
    ids = src.list_concepts()
    assert ids == ["tables/widgets"]
    c = src.read_concept("tables/widgets")
    assert c.type == "table" and c.title == "widgets"
    assert "`sku`" in c.body and "`id`" in c.body       # real columns only
    assert "2 rows" in c.body                           # factual count
    assert "🔑" in c.body                                # PK surfaced from PRAGMA


def test_schema_source_never_writes(tmp_path):
    """Opening + reading must not create -wal/-shm churn or mutate the db mtime meaningfully."""
    db = tmp_path / "ro.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t (x INTEGER)")
    con.commit()
    con.close()
    before = db.stat().st_mtime
    src = ShippingSchemaSource(db_path=str(db))
    src.list_concepts()
    src.read_concept("tables/t")
    # a read-only (mode=ro) open leaves the main db file unmodified
    assert db.stat().st_mtime == before
