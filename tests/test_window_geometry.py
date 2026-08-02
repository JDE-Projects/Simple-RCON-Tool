from types import SimpleNamespace

import pytest

import simple_rcon_tool as app


class _Call:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


def _window_api(windows):
    def enum_windows(callback, lparam):
        for hwnd in windows:
            if callback(hwnd, lparam) is False:
                break
        return True

    def process_id(hwnd, pid):
        pid._obj.value = windows[hwnd]["pid"]
        return 0

    def text_length(hwnd):
        return len(windows[hwnd]["title"])

    def text(hwnd, buffer, length):
        buffer.value = windows[hwnd]["title"]
        return len(buffer.value)

    return SimpleNamespace(
        EnumWindows=_Call(enum_windows),
        GetWindowThreadProcessId=_Call(process_id),
        GetWindowTextLengthW=_Call(text_length),
        GetWindowTextW=_Call(text),
        IsWindowVisible=_Call(lambda hwnd: windows[hwnd]["visible"]),
    )


def test_own_window_handle_filters_process_visibility_and_exact_title(monkeypatch):
    own_pid = 4242
    windows = {
        1: {"pid": 9999, "title": "Simple RCON Tool", "visible": True},
        2: {"pid": own_pid, "title": "Simple RCON Tool - other", "visible": True},
        3: {"pid": own_pid, "title": "Simple RCON Tool", "visible": False},
        4: {"pid": own_pid, "title": "Simple RCON Tool", "visible": True},
    }
    monkeypatch.setattr(app.ctypes, "windll", SimpleNamespace(user32=_window_api(windows)), raising=False)
    monkeypatch.setattr(app.ctypes, "WINFUNCTYPE", lambda *_args: lambda callback: callback)
    monkeypatch.setattr(app.os, "getpid", lambda: own_pid)

    assert app._own_window_handle("Simple RCON Tool") == 4


def test_own_window_handle_returns_none_when_enumeration_fails(monkeypatch):
    user32 = _window_api({})
    user32.EnumWindows = _Call(lambda *_args: (_ for _ in ()).throw(OSError("blocked")))
    monkeypatch.setattr(app.ctypes, "windll", SimpleNamespace(user32=user32), raising=False)
    monkeypatch.setattr(app.ctypes, "WINFUNCTYPE", lambda *_args: lambda callback: callback)

    assert app._own_window_handle("Simple RCON Tool") is None


@pytest.mark.parametrize("function_name", ["_save_geometry", "_restore_geometry"])
def test_second_instance_skips_geometry(monkeypatch, function_name):
    monkeypatch.setattr(app, "IS_SECOND_INSTANCE", True)
    monkeypatch.setattr(app, "_win32", lambda: pytest.fail("Win32 must not be used"))
    monkeypatch.setattr(app, "_own_window_handle", lambda _title: pytest.fail("lookup must not run"))

    getattr(app, function_name)(SimpleNamespace(title="Simple RCON Tool"))


def test_main_marks_second_instance_and_wires_geometry_suppression(monkeypatch):
    class Event:
        def __init__(self):
            self.callbacks = []

        def __iadd__(self, callback):
            self.callbacks.append(callback)
            return self

    window = SimpleNamespace(events=SimpleNamespace(shown=Event(), closing=Event(), loaded=Event()))
    monkeypatch.setattr(app, "IS_SECOND_INSTANCE", False)
    monkeypatch.setattr(app, "_acquire_single_instance", lambda _name: False)
    monkeypatch.setattr(app, "_prompt_second_instance", lambda _title: True)
    monkeypatch.setattr(app, "_splash_watchdog", lambda: None)
    monkeypatch.setattr(app.threading, "Thread", lambda *args, **kwargs: SimpleNamespace(start=lambda: None))
    monkeypatch.setattr(app.webview, "create_window", lambda *args, **kwargs: window)
    monkeypatch.setattr(app.webview, "start", lambda **kwargs: None)
    monkeypatch.setattr(app, "_restore_geometry", lambda _window: pytest.fail("restore must be skipped"))
    monkeypatch.setattr(app, "_save_geometry", lambda _window: pytest.fail("save must be skipped"))

    app.main()

    assert app.IS_SECOND_INSTANCE is True
    window.events.shown.callbacks[0]()
    assert window.events.closing.callbacks[0]() is True
