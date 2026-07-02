import json

from appyhour_lib.notify import notify


def test_notify_missing_webhook_writes_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("AH_SLACK_WEBHOOK", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))

    notify("test message", level="error")

    log = tmp_path / "AppyHour" / "notify_fallback.log"
    payload = json.loads(log.read_text(encoding="utf-8").strip())
    assert payload["level"] == "error"
    assert payload["message"] == "test message"
    assert "missing AH_SLACK_WEBHOOK" in payload["reason"]
