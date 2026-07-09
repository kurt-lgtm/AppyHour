"""Tests for appyhour_lib.notify — email + Slack fan-out, fail-silent fallback."""
import json

import appyhour_lib.notify as notify_mod
from appyhour_lib.notify import notify


class _FakeSMTP:
    """Records the last message handed to sendmail()."""

    sent = None

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        self.user = user

    def sendmail(self, from_addr, to_addrs, body):
        _FakeSMTP.sent = {"from": from_addr, "to": to_addrs, "body": body}


def _clear_env(monkeypatch):
    for key in (
        "AH_SLACK_WEBHOOK", "AH_ALERT_EMAIL_TO", "AH_ALERT_EMAIL_FROM",
        "AH_SMTP_USER", "AH_SMTP_PASSWORD", "AH_SMTP_HOST", "AH_SMTP_PORT",
        "AH_SETTINGS_JSON",
    ):
        monkeypatch.delenv(key, raising=False)


def test_email_sent_when_smtp_configured(monkeypatch):
    _clear_env(monkeypatch)
    _FakeSMTP.sent = None
    monkeypatch.setenv("AH_SMTP_USER", "ops@elevatefoods.co")
    monkeypatch.setenv("AH_SMTP_PASSWORD", "app-pw")
    monkeypatch.setenv("AH_ALERT_EMAIL_TO", "kurt@elevatefoods.co")
    monkeypatch.setattr(notify_mod.smtplib, "SMTP", _FakeSMTP)

    notify("backup failed: disk full", level="error")

    assert _FakeSMTP.sent is not None
    assert _FakeSMTP.sent["to"] == ["kurt@elevatefoods.co"]
    assert "[AppyHour ERROR] backup failed: disk full" in _FakeSMTP.sent["body"]


def test_recipient_defaults_to_smtp_user(monkeypatch):
    _clear_env(monkeypatch)
    _FakeSMTP.sent = None
    monkeypatch.setenv("AH_SMTP_USER", "ops@elevatefoods.co")
    monkeypatch.setenv("AH_SMTP_PASSWORD", "app-pw")
    monkeypatch.setattr(notify_mod.smtplib, "SMTP", _FakeSMTP)

    notify("heads up")

    assert _FakeSMTP.sent["to"] == ["ops@elevatefoods.co"]


def test_credentials_reused_from_settings_json(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    _FakeSMTP.sent = None
    settings = tmp_path / "inventory_reorder_settings.json"
    settings.write_text(json.dumps({
        "smtp_user": "ops@elevatefoods.co",
        "smtp_password": "app-pw",
        "depletion_email_to": "kurt@elevatefoods.co",
    }), encoding="utf-8")
    monkeypatch.setenv("AH_SETTINGS_JSON", str(settings))
    monkeypatch.setattr(notify_mod.smtplib, "SMTP", _FakeSMTP)

    notify("reship report FAILED", level="critical")

    assert _FakeSMTP.sent["to"] == ["kurt@elevatefoods.co"]
    assert "[AppyHour CRITICAL]" in _FakeSMTP.sent["body"]


def test_no_channel_configured_writes_fallback(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setattr(notify_mod, "_app_dir", lambda: tmp_path)

    notify("nowhere to go", level="warn")

    log = tmp_path / "notify_fallback.log"
    assert log.exists()
    entry = json.loads(log.read_text(encoding="utf-8").strip())
    assert entry["message"] == "nowhere to go"
    assert "no channel configured" in entry["reason"] or "not configured" in entry["reason"]
