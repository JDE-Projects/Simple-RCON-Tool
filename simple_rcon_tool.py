"""
Simple RCON Tool
A standalone desktop tool to send game-server RCON commands.

Backend: pywebview window host + a small Source RCON client (standard library).
Frontend: simple_rcon_tool-UI.html (web-style interface).

RCON passwords are held in memory only. They are never written to disk.
Only the server name / host / port / game key / custom buttons are saved in
servers.json. The built-in command buttons for each game come from the catalog
in app/services/catalog.py, so updating that file updates everyone's buttons
without touching their saved servers.

Project: Simple RCON Tool
Author:  JDE-Projects  (https://github.com/JDE-Projects)
"""

import os
import sys
import ctypes
import shlex

# Bind to PySide6 (LGPL), never PyQt6 (GPL). Set before webview imports Qt so
# qtpy resolves to PySide6 even if PyQt6 happens to be present.
os.environ.setdefault("QT_API", "pyside6")

import webview

from app import paths
from app.api import Api
from app.services import diagnostics, window


# ----------------------------------------------------------------------------
# Version
# ----------------------------------------------------------------------------
# APP_VERSION is the version of record; it equals the release tag without the
# leading "v".
APP_VERSION = "1.5.3"


# ----------------------------------------------------------------------------
# Qt remote-debugging switches
# ----------------------------------------------------------------------------
# The built exe strips Qt's remote-debugging switches at startup so it never
# opens Qt's remote-control port, which another local program could use to read
# the page and call the Python bridge. Source runs keep them for real-window
# smoke checks.

def _is_remote_debugging_switch(token):
    # Chromium on Windows accepts "--", "-" or "/" before a switch name and
    # ignores its case, so every spelling is matched.
    for prefix in ("--", "-", "/"):
        if token.startswith(prefix):
            return token[len(prefix):].lower().startswith("remote-debugging-")
    return False


def _drop_remote_debugging(tokens):
    """Return tokens without remote-debugging switches, including a value
    given as the following token (--remote-debugging-port 9222)."""
    kept = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if _is_remote_debugging_switch(token):
            if "=" not in token and index < len(tokens) and not tokens[index].startswith(("-", "/")):
                index += 1
        else:
            kept.append(token)
    return kept


def strip_remote_debugging(environ, argv, frozen):
    """Remove Qt remote-debugging controls from frozen application launches,
    so the built app never opens Qt's remote-control port. Source runs are
    left alone: the real-window smoke check depends on that port."""
    if not frozen:
        return

    environ.pop("QTWEBENGINE_REMOTE_DEBUGGING", None)

    flags = environ.get("QTWEBENGINE_CHROMIUM_FLAGS")
    if flags is not None:
        try:
            lexer = shlex.shlex(flags, posix=False)
            lexer.whitespace_split = True
            tokens = list(lexer)
        except ValueError:
            # Unbalanced quote: split on spaces rather than fail at startup.
            tokens = flags.split()
        kept = _drop_remote_debugging(tokens)
        if kept:
            environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(kept)
        else:
            del environ["QTWEBENGINE_CHROMIUM_FLAGS"]

    argv[1:] = _drop_remote_debugging(argv[1:])


# ----------------------------------------------------------------------------
# Boot
# ----------------------------------------------------------------------------

_mutex_handle = None   # module-level: must live for the process lifetime
IS_SECOND_INSTANCE = False

def _acquire_single_instance(mutex_name: str) -> bool:
    # Name convention: "JDE_Simple{Thing}Tool_SingleInstance"
    # Session-local (no "Global\" prefix): each Windows session (e.g. RDP,
    # fast user switching) gets its own instance instead of colliding across users.
    global _mutex_handle
    try:
        # use_last_error=True: ctypes.windll's GetLastError() can be clobbered
        # by ctypes-internal calls, so read the error via ctypes.get_last_error() instead.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _mutex_handle = kernel32.CreateMutexW(None, False, mutex_name)
        return ctypes.get_last_error() != 183   # ERROR_ALREADY_EXISTS
    except Exception:
        return True   # fail open: never block launch over a mutex error

def _prompt_second_instance(app_title: str) -> bool:
    # Native message box only: runs before pywebview/Qt exists, so no Qt dialog is available yet.
    try:
        text = f"{app_title} is already running.\n\nOpen a second instance?"
        MB_YESNO_ICONQUESTION = 0x00000024
        result = ctypes.windll.user32.MessageBoxW(None, text, app_title, MB_YESNO_ICONQUESTION)
        return result == 6   # IDYES
    except Exception:
        return True   # fail open: if the box can't be shown, launch proceeds

def main():
    # Stops Qt leaving an empty %LOCALAPPDATA%\<exe name>\cache\qtpipelinecache-*
    # folder the uninstaller never removes. Must run before the window is created.
    os.environ.setdefault("QT_DISABLE_SHADER_DISK_CACHE", "1")
    strip_remote_debugging(os.environ, sys.argv, getattr(sys, "frozen", False))

    # Use the Windows certificate store for TLS instead of the bundled CA list,
    # so antivirus/network filters that inject their own root cert (common on
    # managed laptops) don't break the GitHub update check. Runs before the
    # Api object exists, so there's no logger yet to record a fallback; if
    # truststore is missing or fails, urllib silently keeps using its default
    # bundled CA list instead.
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception:
        pass

    global IS_SECOND_INSTANCE
    if not _acquire_single_instance("JDE_SimpleRCONTool_SingleInstance"):
        if not _prompt_second_instance("Simple RCON Tool"):
            sys.exit(0)
        IS_SECOND_INSTANCE = True

    # Windows app identity so the taskbar shows our icon, not the generic Qt one
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "JDEProjects.SimpleRconTool"
            )
        except Exception:
            pass

    api = Api(APP_VERSION)
    diagnostics._debug_logger.prune()
    html_path = paths.resource_path("simple_rcon_tool-UI.html")
    main_window = webview.create_window(
        "Simple RCON Tool",
        url=html_path,
        js_api=api,
        width=1240,
        height=860,
        min_size=(980, 660),
        background_color="#0a0e14",
    )
    api._set_window(main_window)

    main_window.events.shown += lambda: (
        None if IS_SECOND_INSTANCE
        else window._restore_geometry(main_window, IS_SECOND_INSTANCE)
    )

    def _on_closing():
        if not IS_SECOND_INSTANCE:
            window._save_geometry(main_window, IS_SECOND_INSTANCE)
        return True
    main_window.events.closing += _on_closing

    def on_loaded():
        api._mark_frontend_ready()

    try:
        main_window.events.loaded += on_loaded
    except Exception:
        api._mark_frontend_ready()

    # Qt backend (PySide6 + WebEngine). Pass the PNG so the live taskbar icon
    # is ours; guarded so an older pywebview without the icon arg still launches.
    try:
        webview.start(gui="qt", icon=paths.resource_path("simple_rcon_tool.png"))
    except TypeError:
        webview.start(gui="qt")


if __name__ == "__main__":
    main()
