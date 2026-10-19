import pytest

import simple_rcon_tool as app
from app import debug_log
from app.services import diagnostics


@pytest.mark.parametrize("key", ["password", "rcon_password", "rconpassword", "AdminPassword", "ServerPassword"])
@pytest.mark.parametrize("separator", [":", "=", " "])
@pytest.mark.parametrize("value", ["secret", '"secret with spaces"', "'secret with spaces'"])
def test_redact_hides_every_password_token_form_in_written_log(tmp_path, key, separator, value):
    secret = "secret with spaces" if " " in value else "secret"
    dbg = debug_log.DebugLog(str(tmp_path), "Test App", redact=diagnostics._redact)
    assert dbg.set_enabled(True)
    dbg.log(f'{key}{separator}{value}')
    contents = open(dbg._path, encoding="utf-8").read()
    assert secret not in contents
    assert "***" in contents


def test_redact_hides_json_password_value_in_written_log(tmp_path):
    dbg = debug_log.DebugLog(str(tmp_path), "Test App", redact=diagnostics._redact)
    assert dbg.set_enabled(True)
    dbg.log('{"password": "secret with spaces"}')
    contents = open(dbg._path, encoding="utf-8").read()
    assert "secret with spaces" not in contents
    assert '"password": ***' in contents


class FakeSession:
    def __init__(self, server):
        self.server = server

    def command(self, _command):
        return ""

    def close(self):
        pass


def _configured_logger(monkeypatch, tmp_path):
    logger = debug_log.DebugLog(str(tmp_path), "Test App", redact=diagnostics._redact, on_warning=diagnostics._debug_warning)
    monkeypatch.setattr(diagnostics, "_debug_logger", logger)
    assert diagnostics.set_debug(True, app.APP_VERSION)
    return logger


def test_custom_command_text_and_label_never_enter_debug_log(monkeypatch, tmp_path):
    logger = _configured_logger(monkeypatch, tmp_path)
    api = app.Api()
    session = FakeSession({"game": "gmod"})
    api._sessions["srv-one"] = session
    api._exec("srv-one", session, "rcon_password secret", "rcon_password secret")
    contents = open(logger._path, encoding="utf-8").read()
    assert "secret" not in contents
    assert "rcon_password" not in contents
    assert "custom command (server srv-one, 20 characters)" in contents


def test_catalog_quick_button_logs_its_name(monkeypatch, tmp_path):
    logger = _configured_logger(monkeypatch, tmp_path)
    api = app.Api()
    session = FakeSession({"game": "gmod"})
    api._sessions["srv-one"] = session
    api._exec("srv-one", session, "status", "Status")
    assert 'send: quick button "Status" (server srv-one)' in open(logger._path, encoding="utf-8").read()


def test_non_catalog_label_never_enters_debug_log(monkeypatch, tmp_path):
    logger = _configured_logger(monkeypatch, tmp_path)
    api = app.Api()
    session = FakeSession({"game": "gmod"})
    api._sessions["srv-one"] = session
    api._exec("srv-one", session, "status", "Private label")
    contents = open(logger._path, encoding="utf-8").read()
    assert "Private label" not in contents
    assert "status" not in contents
    assert "custom command (server srv-one, 6 characters)" in contents


def test_warning_before_page_ready_is_returned_once(monkeypatch, tmp_path):
    _configured_logger(monkeypatch, tmp_path)
    api = app.Api()
    api._handle_debug_warning("Debug log: test warning")
    assert api.get_config()["debugWarnings"] == ["Debug log: test warning"]
    assert api.get_config()["debugWarnings"] == []


def test_warning_after_ready_is_emitted_with_disabled_state(monkeypatch, tmp_path):
    logger = _configured_logger(monkeypatch, tmp_path)
    api = app.Api()
    events = []
    monkeypatch.setattr(api, "_emit", lambda channel, payload: events.append((channel, payload)))
    api._set_window(object())
    api._mark_frontend_ready()
    monkeypatch.setattr(debug_log, "open", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(13, "Permission denied")), raising=False)
    logger.log("write failure")
    assert events[-1][0] == "debugWarning"
    assert events[-1][1]["debug"] is False
    assert "write failed" in events[-1][1]["text"]


def test_mark_frontend_ready_flushes_warnings_queued_after_config(monkeypatch, tmp_path):
    _configured_logger(monkeypatch, tmp_path)
    api = app.Api()
    assert api.get_config()["debugWarnings"] == []
    api._handle_debug_warning("late warning")
    events = []
    monkeypatch.setattr(api, "_emit", lambda channel, payload: events.append((channel, payload)))
    api._set_window(object())
    api._mark_frontend_ready()
    assert events == [("debugWarning", {"text": "late warning", "debug": True})]


class _Event:
    def __iadd__(self, _callback):
        return self


class _Window:
    def __init__(self):
        self.events = type("Events", (), {"shown": _Event(), "closing": _Event(), "loaded": _Event()})()


def test_main_prunes_only_matching_logs(monkeypatch, tmp_path):
    for stamp in ("01012026_010000", "01022026_010000", "01032026_010000", "01042026_010000"):
        (tmp_path / f"Debug_Log_{stamp}.txt").write_text("old", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("keep", encoding="utf-8")
    folder = tmp_path / "Debug_Log_01052026_010000.txt"
    folder.mkdir()
    logger = debug_log.DebugLog(str(tmp_path), "Test App", redact=diagnostics._redact, on_warning=diagnostics._debug_warning)
    monkeypatch.setattr(diagnostics, "_debug_logger", logger)
    monkeypatch.setattr(app, "_acquire_single_instance", lambda _name: True)
    monkeypatch.setattr(app.webview, "create_window", lambda *_args, **_kwargs: _Window())
    monkeypatch.setattr(app.webview, "start", lambda **_kwargs: None)
    app.main()
    assert {path.name for path in tmp_path.glob("Debug_Log_*.txt") if path.is_file()} == {
        "Debug_Log_01022026_010000.txt", "Debug_Log_01032026_010000.txt", "Debug_Log_01042026_010000.txt",
    }
    assert (tmp_path / "notes.txt").exists()
    assert folder.is_dir()
