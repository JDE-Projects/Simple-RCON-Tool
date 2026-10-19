from app.api import Api
from app import paths
from app.services import diagnostics, prefs, sessions


THEME_ERROR = "Couldn't save the theme choice. It will stay until you close the app."
EXPORT_ERROR = "The console could not be saved to a file."


def test_save_theme_success(monkeypatch, tmp_path):
    pref_path = tmp_path / "simple_rcon_tool.pref"
    monkeypatch.setattr(paths, "app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(paths, "_pref_path", lambda: str(pref_path))

    result = Api("test").save_theme("light")

    assert result == {"ok": True}
    assert prefs.load_prefs() == {"theme": "light"}


def test_save_theme_failure_returns_error_and_logs(monkeypatch, tmp_path):
    messages = []
    monkeypatch.setattr(paths, "app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(paths, "_pref_path", lambda: str(tmp_path / "simple_rcon_tool.pref"))
    monkeypatch.setattr(prefs, "save_prefs", lambda _prefs: False)
    monkeypatch.setattr(diagnostics, "debug_log", messages.append)

    result = Api("test").save_theme("light")

    assert result == {"ok": False, "error": THEME_ERROR}
    assert messages == ["save_theme write failed"]


def test_export_console_success_writes_text(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(sessions.time, "strftime", lambda _format: "2026-10-17_12-00-00")

    result = Api("test").export_console("My Server", "console text")

    assert result["ok"] is True
    assert result["name"] == "My_Server_Console_2026-10-17_12-00-00.txt"
    assert (tmp_path / result["name"]).read_text(encoding="utf-8") == "console text"


def test_export_console_does_not_overwrite_same_second(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(sessions.time, "strftime", lambda _format: "2026-10-17_12-00-00")
    api = Api("test")

    first = api.export_console("My Server", "first export")
    second = api.export_console("My Server", "second export")

    assert first["ok"] is True
    assert second["ok"] is True
    assert first["name"] != second["name"]
    assert (tmp_path / first["name"]).read_text(encoding="utf-8") == "first export"
    assert (tmp_path / second["name"]).read_text(encoding="utf-8") == "second export"


def test_export_console_failure_returns_plain_error_and_logs(monkeypatch, tmp_path):
    messages = []

    def denied(*_args, **_kwargs):
        raise OSError("C:\\secret\\path denied")

    monkeypatch.setattr(paths, "app_dir", lambda: str(tmp_path))
    monkeypatch.setattr("builtins.open", denied)
    monkeypatch.setattr(diagnostics, "debug_log", messages.append)

    result = Api("test").export_console("My Server", "console text")

    assert result == {"ok": False, "error": EXPORT_ERROR}
    assert "secret" not in str(result)
    assert messages == ["Could not export console: OSError: C:\\secret\\path denied"]
