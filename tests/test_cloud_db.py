"""Tests for THE cloud credential resolver. No driver, no server, no creds needed.

🔴 The point of these: the five prior implementations disagreed on the FALLBACK, and nothing
tested the fallback because every author had DATABASE_URL set in their own shell.
"""
import pytest

from appyhour_lib import cloud_db

URL = "mysql://u:pw@db.example.com:25060/appyhourbox"


def test_env_wins(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", URL)
    assert cloud_db.database_url() == URL


def test_falls_back_to_credential_file(monkeypatch, tmp_path):
    """🔴 The axis manual_ingest._mysql() gets wrong — env-only, no file."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    (tmp_path / "AppyHour").mkdir()
    (tmp_path / "AppyHour" / "database_url.txt").write_text(URL + "\n", encoding="utf-8")
    assert cloud_db.database_url() == URL


def test_none_when_neither(monkeypatch, tmp_path):
    """None, not an exception — reporting must tell 'no credential' from 'bad credential'."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert cloud_db.database_url() is None


def test_empty_file_is_none(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    (tmp_path / "AppyHour").mkdir()
    (tmp_path / "AppyHour" / "database_url.txt").write_text("   \n", encoding="utf-8")
    assert cloud_db.database_url() is None


def test_parse_url():
    kw = cloud_db.parse_url(URL)
    assert kw["host"] == "db.example.com" and kw["port"] == 25060
    assert kw["user"] == "u" and kw["password"] == "pw"
    assert kw["database"] == "appyhourbox" and kw["ssl"] == {"ssl": {}}
    assert isinstance(kw["port"], int), "port must be int — pymysql silently misbehaves on str"


def test_parse_accepts_driver_suffix():
    assert cloud_db.parse_url("mysql+pymysql://u:pw@h:3306/d")["host"] == "h"


@pytest.mark.parametrize("bad", ["", "postgres://u:pw@h:5432/d", "mysql://u@h/d", "garbage"])
def test_parse_rejects_bad_url(bad):
    with pytest.raises(ValueError):
        cloud_db.parse_url(bad)


def test_connect_raises_actionable_when_unconfigured(monkeypatch, tmp_path):
    """The message must name the fix, not just the failure."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    with pytest.raises(RuntimeError) as e:
        cloud_db.connect()
    assert "database_url.txt" in str(e.value) and "REAL terminal" in str(e.value)


def test_no_import_time_io(monkeypatch):
    """🔴 Import must not touch the filesystem — the cloud-build mkdir bug class."""
    import importlib
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("APPDATA", "Z:/does/not/exist")
    importlib.reload(cloud_db)      # would raise if module scope read the file
