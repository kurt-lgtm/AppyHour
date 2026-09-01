import json

import pytest

import appyhour_lib.notify as notify_mod
from appyhour_lib.notify import KURT_OPS_CHANNEL, _main, notify


def _isolate(tmp_path, monkeypatch):
    """Detach from the real .env — otherwise notify() finds the live bot token
    and the test suite posts into #kurt-ops for real."""
    monkeypatch.setattr(notify_mod, "_ENV_FILE", tmp_path / "absent.env")
    monkeypatch.delenv("AH_SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("AH_SLACK_CHANNEL", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))


def _log(tmp_path):
    log = tmp_path / "AppyHour" / "notify_fallback.log"
    return json.loads(log.read_text(encoding="utf-8").strip())


def test_notify_missing_token_writes_fallback(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)

    notify("test message", level="error")

    payload = _log(tmp_path)
    assert payload["level"] == "error"
    assert payload["message"] == "test message"
    assert "missing AH_SLACK_BOT_TOKEN" in payload["reason"]


def test_notify_posts_to_kurt_ops(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("AH_SLACK_BOT_TOKEN", "xoxb-test")
    seen = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"ok": True}

    def _post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, body=json)
        return _Resp()

    import requests

    monkeypatch.setattr(requests, "post", _post)
    notify("hello", level="info")

    assert seen["url"] == "https://slack.com/api/chat.postMessage"
    assert seen["headers"]["Authorization"] == "Bearer xoxb-test"
    assert seen["body"]["channel"] == KURT_OPS_CHANNEL
    assert seen["body"]["text"] == "[INFO] hello"


def test_notify_slack_error_body_writes_fallback(tmp_path, monkeypatch):
    """Slack answers a refused post with HTTP 200 + ok:false — a status-code
    check alone would record it as delivered."""
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("AH_SLACK_BOT_TOKEN", "xoxb-test")

    class _Resp:
        status_code = 200

        def json(self):
            return {"ok": False, "error": "not_in_channel"}

    import requests

    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp())
    notify("nope", level="error")

    assert "not_in_channel" in _log(tmp_path)["reason"]


# --- CLI (_main) ----------------------------------------------------------
# The CLI's whole job is delivery, so its contract is the INVERSE of notify()'s
# fail-silent one: a fallback write must exit NON-ZERO. A routine that pins this
# command and ignores the exit code is back to the 2026-08-26 silent outage.


def _msg_file(tmp_path, text="hello from a routine"):
    p = tmp_path / "msg.txt"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_main_exits_nonzero_when_delivery_falls_back(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)  # no token -> notify() writes the fallback log

    rc = _main(["--file", _msg_file(tmp_path)])

    assert rc == 1
    assert "missing AH_SLACK_BOT_TOKEN" in _log(tmp_path)["reason"]


def test_main_exits_zero_and_posts_the_file_body(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("AH_SLACK_BOT_TOKEN", "xoxb-test")
    seen = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"ok": True}

    def _post(url, headers=None, json=None, timeout=None):
        seen.update(body=json)
        return _Resp()

    import requests

    monkeypatch.setattr(requests, "post", _post)

    rc = _main(["--file", _msg_file(tmp_path, "🔴 anomaly first\nthen the table")])

    assert rc == 0
    assert seen["body"]["channel"] == KURT_OPS_CHANNEL      # never a DM id
    assert seen["body"]["text"] == "[INFO] 🔴 anomaly first\nthen the table"


def test_main_refuses_an_empty_or_missing_file(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)

    assert _main(["--file", _msg_file(tmp_path, "   ")]) == 2
    assert _main(["--file", str(tmp_path / "nope.txt")]) == 2


def test_main_has_no_channel_flag(tmp_path, monkeypatch):
    """The destination lives in notify.py/AH_SLACK_CHANNEL and nowhere else."""
    _isolate(tmp_path, monkeypatch)

    with pytest.raises(SystemExit):
        _main(["--file", _msg_file(tmp_path), "--channel", "C0BT47XG8CW"])
