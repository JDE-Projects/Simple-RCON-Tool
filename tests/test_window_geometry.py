from types import SimpleNamespace

import pytest

import simple_rcon_tool as app
from app.services import window as window_service


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
    monkeypatch.setattr(window_service.ctypes, "windll", SimpleNamespace(user32=_window_api(windows)), raising=False)
    monkeypatch.setattr(window_service.ctypes, "WINFUNCTYPE", lambda *_args: lambda callback: callback)
    monkeypatch.setattr(window_service.os, "getpid", lambda: own_pid)

    assert window_service._own_window_handle("Simple RCON Tool") == 4


def test_own_window_handle_returns_none_when_enumeration_fails(monkeypatch):
    user32 = _window_api({})
    user32.EnumWindows = _Call(lambda *_args: (_ for _ in ()).throw(OSError("blocked")))
    monkeypatch.setattr(window_service.ctypes, "windll", SimpleNamespace(user32=user32), raising=False)
    monkeypatch.setattr(window_service.ctypes, "WINFUNCTYPE", lambda *_args: lambda callback: callback)

    assert window_service._own_window_handle("Simple RCON Tool") is None


@pytest.mark.parametrize("function_name", ["_save_geometry", "_restore_geometry"])
def test_second_instance_skips_geometry(monkeypatch, function_name):
    monkeypatch.setattr(window_service, "_win32", lambda: pytest.fail("Win32 must not be used"))
    monkeypatch.setattr(window_service, "_own_window_handle", lambda _title: pytest.fail("lookup must not run"))

    getattr(window_service, function_name)(SimpleNamespace(title="Simple RCON Tool"), True)


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
    monkeypatch.setattr(app.threading, "Thread", lambda *args, **kwargs: SimpleNamespace(start=lambda: None))
    monkeypatch.setattr(app.webview, "create_window", lambda *args, **kwargs: window)
    monkeypatch.setattr(app.webview, "start", lambda **kwargs: None)
    monkeypatch.setattr(window_service, "_restore_geometry", lambda _window, _second: pytest.fail("restore must be skipped"))
    monkeypatch.setattr(window_service, "_save_geometry", lambda _window, _second: pytest.fail("save must be skipped"))

    app.main()

    assert app.IS_SECOND_INSTANCE is True
    window.events.shown.callbacks[0]()
    assert window.events.closing.callbacks[0]() is True


def _restore_api(current_rect=(10, 20, 110, 120)):
    calls = []

    def get_window_rect(_hwnd, rect):
        rect._obj.left, rect._obj.top, rect._obj.right, rect._obj.bottom = current_rect
        return True

    def set_window_pos(*args):
        calls.append(args)
        return True

    return SimpleNamespace(
        GetWindowRect=_Call(get_window_rect),
        SetWindowPos=_Call(set_window_pos),
    ), calls


def _restore_user32(point_monitor=1, window_monitor=1):
    return SimpleNamespace(
        MonitorFromPoint=_Call(lambda *_args: point_monitor),
        MonitorFromWindow=_Call(lambda *_args: window_monitor),
    )


def _patch_restore_environment(monkeypatch, saved_prefs, *, current_rect=(10, 20, 110, 120), point_monitor=1,
                               window_monitor=1, work=(0, 0, 100, 100)):
    user32 = _restore_user32(point_monitor, window_monitor)
    window_api, calls = _restore_api(current_rect)
    monkeypatch.setattr(window_service.prefs, "load_prefs", lambda: saved_prefs)
    monkeypatch.setattr(window_service.ctypes, "windll", SimpleNamespace(user32=user32), raising=False)
    monkeypatch.setattr(window_service, "_win32", lambda: window_api)
    monkeypatch.setattr(window_service, "_own_window_handle", lambda _title: 42)
    monkeypatch.setattr(window_service, "_monitor_work_area", lambda _monitor: work)
    monkeypatch.setattr(window_service, "_frame_insets", lambda _hwnd: (0, 0, 0, 0))
    monkeypatch.setattr(window_service.time, "sleep", lambda _seconds: None)
    return calls


def test_fit_rect_to_work_area_shrinks_width():
    assert window_service.fit_rect_to_work_area(0, 20, 140, 60, (0, 0, 100, 100), (0, 0, 0, 0)) == (0, 20, 100, 60)


def test_fit_rect_to_work_area_shrinks_height():
    assert window_service.fit_rect_to_work_area(20, 0, 60, 140, (0, 0, 100, 100), (0, 0, 0, 0)) == (20, 0, 60, 100)


def test_fit_rect_to_work_area_moves_past_right_and_bottom():
    assert window_service.fit_rect_to_work_area(70, 80, 40, 30, (0, 0, 100, 100), (0, 0, 0, 0)) == (60, 70, 40, 30)


def test_fit_rect_to_work_area_moves_past_left_and_top():
    assert window_service.fit_rect_to_work_area(-10, -20, 40, 30, (0, 0, 100, 100), (0, 0, 0, 0)) == (0, 0, 40, 30)


def test_fit_rect_to_work_area_keeps_snapped_visible_edge():
    assert window_service.fit_rect_to_work_area(-7, 0, 114, 107, (0, 0, 100, 100), (7, 0, 7, 7)) == (-7, 0, 114, 107)


def test_fit_rect_to_work_area_keeps_already_fitting_rect():
    assert window_service.fit_rect_to_work_area(10, 20, 60, 50, (0, 0, 100, 100), (2, 3, 4, 5)) == (10, 20, 60, 50)


def test_fit_rect_to_work_area_handles_negative_origin():
    assert window_service.fit_rect_to_work_area(-180, 10, 80, 40, (-200, 0, 0, 100), (0, 0, 0, 0)) == (-180, 10, 80, 40)


def test_fit_rect_to_work_area_keeps_top_left_when_larger_in_both_directions():
    assert window_service.fit_rect_to_work_area(-20, -20, 140, 140, (0, 0, 100, 100), (0, 0, 0, 0)) == (0, 0, 100, 100)


def test_fit_rect_to_work_area_moves_past_left_edge_only():
    assert window_service.fit_rect_to_work_area(-10, 20, 40, 30, (0, 0, 100, 100), (0, 0, 0, 0)) == (0, 20, 40, 30)


def test_fit_rect_to_work_area_moves_past_top_edge_only():
    assert window_service.fit_rect_to_work_area(20, -10, 40, 30, (0, 0, 100, 100), (0, 0, 0, 0)) == (20, 0, 40, 30)


def test_fit_rect_to_work_area_moves_past_right_edge_only():
    assert window_service.fit_rect_to_work_area(80, 20, 30, 40, (0, 0, 100, 100), (0, 0, 0, 0)) == (70, 20, 30, 40)


def test_fit_rect_to_work_area_moves_past_bottom_edge_only():
    assert window_service.fit_rect_to_work_area(20, 80, 40, 30, (0, 0, 100, 100), (0, 0, 0, 0)) == (20, 70, 40, 30)


def test_fit_rect_to_work_area_respects_nonzero_top_left_work_area():
    assert window_service.fit_rect_to_work_area(0, 0, 40, 30, (10, 20, 110, 120), (0, 0, 0, 0)) == (10, 20, 40, 30)


def test_restore_geometry_fits_oversized_saved_rect(monkeypatch):
    calls = _patch_restore_environment(
        monkeypatch, {"window": {"x": 10, "y": 20, "width": 200, "height": 150}}
    )

    window_service._restore_geometry(SimpleNamespace(title="Simple RCON Tool"), False)

    assert calls[0] == (42, None, 0, 0, 100, 100, 0x0004 | 0x0010)


def test_restore_geometry_applies_fitting_saved_rect_once(monkeypatch):
    calls = _patch_restore_environment(
        monkeypatch,
        {"window": {"x": 10, "y": 20, "width": 50, "height": 40}},
        current_rect=(10, 20, 60, 60),
    )

    window_service._restore_geometry(SimpleNamespace(title="Simple RCON Tool"), False)

    assert calls == [(42, None, 10, 20, 50, 40, 0x0004 | 0x0010)]


def test_restore_geometry_refits_after_delay(monkeypatch):
    calls = _patch_restore_environment(
        monkeypatch,
        {"window": {"x": 10, "y": 20, "width": 50, "height": 40}},
        current_rect=(80, 70, 130, 110),
    )

    window_service._restore_geometry(SimpleNamespace(title="Simple RCON Tool"), False)

    assert calls == [
        (42, None, 10, 20, 50, 40, 0x0004 | 0x0010),
        (42, None, 50, 60, 50, 40, 0x0004 | 0x0010),
    ]


def test_restore_geometry_skips_disconnected_monitor(monkeypatch):
    calls = _patch_restore_environment(
        monkeypatch, {"window": {"x": 10, "y": 20, "width": 50, "height": 40}}, point_monitor=0
    )

    window_service._restore_geometry(SimpleNamespace(title="Simple RCON Tool"), False)

    assert calls == []


def test_restore_geometry_skips_missing_work_area(monkeypatch):
    calls = _patch_restore_environment(monkeypatch, {"window": {"x": 10, "y": 20, "width": 50, "height": 40}})
    monkeypatch.setattr(window_service, "_monitor_work_area", lambda _monitor: None)

    window_service._restore_geometry(SimpleNamespace(title="Simple RCON Tool"), False)

    assert calls == []


@pytest.mark.parametrize(
    "prefs",
    [
        {},
        {"window": "not a dictionary"},
        {"window": {"x": 10, "y": 20, "width": 50}},
        {"window": {"x": "10", "y": 20, "width": 50, "height": 40}},
        {"window": {"x": 10.0, "y": 20, "width": 50, "height": 40}},
        {"window": {"x": True, "y": 20, "width": 50, "height": 40}},
        {"window": {"x": 10, "y": 20, "width": 0, "height": 40}},
        {"window": {"x": 10, "y": 20, "width": 50, "height": -40}},
    ],
)
def test_restore_geometry_skips_malformed_preferences(monkeypatch, prefs):
    calls = _patch_restore_environment(monkeypatch, prefs)

    window_service._restore_geometry(SimpleNamespace(title="Simple RCON Tool"), False)

    assert calls == []


def test_restore_geometry_swallows_win32_exception(monkeypatch):
    calls = _patch_restore_environment(monkeypatch, {"window": {"x": 10, "y": 20, "width": 50, "height": 40}})
    window_service.ctypes.windll.user32.MonitorFromPoint = _Call(lambda *_args: (_ for _ in ()).throw(OSError("blocked")))

    window_service._restore_geometry(SimpleNamespace(title="Simple RCON Tool"), False)

    assert calls == []


def test_monitor_work_area_returns_none_when_get_monitor_info_fails(monkeypatch):
    user32 = SimpleNamespace(GetMonitorInfoW=_Call(lambda *_args: 0))
    monkeypatch.setattr(window_service.ctypes, "windll", SimpleNamespace(user32=user32), raising=False)

    assert window_service._monitor_work_area(1) is None


@pytest.mark.parametrize("dwm_result", [1, OSError("blocked")])
def test_frame_insets_returns_zero_when_dwm_is_unavailable(monkeypatch, dwm_result):
    window_api = SimpleNamespace(GetWindowRect=_Call(lambda *_args: True))
    dwmapi = SimpleNamespace(
        DwmGetWindowAttribute=_Call(
            lambda *_args: (_ for _ in ()).throw(dwm_result)
            if isinstance(dwm_result, Exception) else dwm_result
        )
    )
    monkeypatch.setattr(window_service, "_win32", lambda: window_api)
    monkeypatch.setattr(window_service.ctypes, "windll", SimpleNamespace(dwmapi=dwmapi), raising=False)

    assert window_service._frame_insets(42) == (0, 0, 0, 0)
