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
import re
import sys
import json
import time
import ctypes
import shlex
import threading
import urllib.request
import webbrowser

# Bind to PySide6 (LGPL), never PyQt6 (GPL). Set before webview imports Qt so
# qtpy resolves to PySide6 even if PyQt6 happens to be present.
os.environ.setdefault("QT_API", "pyside6")

import webview

from app import paths
from app.services import (
    catalog, diagnostics, links, prefs, rcon, servers, updates, window,
)


# ----------------------------------------------------------------------------
# Version
# ----------------------------------------------------------------------------
# APP_VERSION is the version of record; it equals the release tag without the
# leading "v".
APP_VERSION = "1.5.2"


# ----------------------------------------------------------------------------
# API exposed to the JavaScript frontend
# ----------------------------------------------------------------------------

class Api:
    def __init__(self):
        self._sessions = {}  # server_id -> Session
        self._sessions_lock = threading.Lock()
        self._connect_attempts = {}  # server_id -> current connection attempt token
        self._window = None
        self._frontend_ready = False
        self._debug_warnings = []
        self._debug_warnings_lock = threading.Lock()
        diagnostics.set_warning_bridge(self)

    # ---- frontend helpers -------------------------------------------------

    def _set_window(self, main_window):
        self._window = main_window

    def _mark_frontend_ready(self):
        self._frontend_ready = True
        with self._debug_warnings_lock:
            warnings = self._debug_warnings
            self._debug_warnings = []
        for message in warnings:
            self._emit("debugWarning", {
                "text": message,
                "debug": diagnostics._debug_logger.is_enabled(),
            })

    def _handle_debug_warning(self, message):
        with self._debug_warnings_lock:
            if not self._frontend_ready or not self._window:
                self._debug_warnings.append(message)
                return
        self._emit("debugWarning", {
            "text": message,
            "debug": diagnostics._debug_logger.is_enabled(),
        })

    def _emit(self, channel, payload):
        if not self._window or not self._frontend_ready:
            return
        data = json.dumps(payload)
        js = f"window.__onPyEvent && window.__onPyEvent({json.dumps(channel)}, {data});"
        try:
            self._window.evaluate_js(js)
        except Exception:
            pass

    def _log(self, server_id, text, level="out"):
        self._emit("log", {"serverId": server_id, "text": text, "level": level})

    def open_url(self, url):
        """Open a JDE-Projects website page in the user's default browser.
        Any other address is refused (see _is_allowed_url)."""
        if not links._is_allowed_url(url):
            diagnostics.debug_log("open_url refused an address outside the allowed site")
            return {"ok": False, "error": "URL is not allowed."}
        try:
            if webbrowser.open(url):
                return {"ok": True}
        except Exception as e:
            diagnostics.debug_log(f"Could not open project URL: {e}")
            return {"ok": False, "error": "Could not open the browser."}
        return {"ok": False, "error": "Could not open the browser."}

    # ---- config -----------------------------------------------------------

    def get_config(self):
        try:
            saved_servers = servers.load_servers()
            warning = servers._take_server_store_warning()
        except servers.ServerStoreError as exc:
            saved_servers = []
            warning = str(exc)
        with self._debug_warnings_lock:
            debug_warnings = self._debug_warnings
            self._debug_warnings = []
        return {
            "servers": saved_servers,
            "catalog": catalog.GAME_CATALOG,
            "version": APP_VERSION,
            "debug": diagnostics._debug_logger.is_enabled(),
            "storeWarning": warning,
            "debugWarnings": debug_warnings,
        }

    # ---- theme persistence --------------------------------------------------

    def get_theme(self):
        theme = prefs.load_prefs().get("theme")
        return theme if theme in ("dark", "light") else "dark"

    def save_theme(self, theme):
        if theme not in ("dark", "light"):
            return {"ok": False}
        saved_prefs = prefs.load_prefs()
        saved_prefs["theme"] = theme
        if prefs.save_prefs(saved_prefs):
            return {"ok": True}
        diagnostics.debug_log("save_theme write failed")
        return {
            "ok": False,
            "error": "Couldn't save the theme choice. It will stay until you close the app.",
        }

    # ---- debug log toggle -------------------------------------------------

    def set_debug(self, enabled):
        return {"ok": True, "debug": diagnostics.set_debug(enabled, APP_VERSION)}

    # ---- update check -----------------------------------------------------

    def check_update(self):
        """Compare the latest published release to APP_VERSION. Quiet in the UI
        on failure (see _update_error_reason), but always logged when debug is
        on."""
        result = {"ok": True, "current": APP_VERSION, "version": None,
                  "update": False, "offline": False}
        try:
            url = (f"https://api.github.com/repos/{updates.GITHUB_OWNER}/"
                   f"{updates.GITHUB_REPO}/releases/latest")
            req = urllib.request.Request(url, headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "Simple-RCON-Tool",
            })
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode("utf-8"))
            tag = (data.get("tag_name") or "").lstrip("v")
            result["version"] = tag
            if tag and updates._version_gt(tag, APP_VERSION):
                result["update"] = True
                result["url"] = (data.get("html_url") or
                                  f"https://github.com/{updates.GITHUB_OWNER}/{updates.GITHUB_REPO}/releases")
            diagnostics.debug_log(f"check_update: found v{tag}, current v{APP_VERSION}")
        except Exception as e:
            result["offline"] = True
            result["reason"] = updates._update_error_reason(e)
            diagnostics.debug_log(f"check_update failed: {type(e).__name__}: {e}")
        return result

    # ---- server CRUD ------------------------------------------------------

    def save_server(self, server):
        cleaned = servers._validate_server(server, require_catalog_game=True)
        if isinstance(cleaned, str):
            return {"ok": False, "error": cleaned}
        server = cleaned
        try:
            saved_servers = servers.load_servers()
        except servers.ServerStoreError as exc:
            return {"ok": False, "error": str(exc)}
        warning = servers._take_server_store_warning()
        if server.get("id"):
            for i, s in enumerate(saved_servers):
                if s["id"] == server["id"]:
                    saved_servers[i] = server
                    break
            else:
                saved_servers.append(server)
        else:
            server["id"] = servers._new_server_id()
            saved_servers.append(server)
        if not servers.save_servers(saved_servers):
            diagnostics.debug_log(f"save_server write failed: {server['id']}")
            return servers._with_warning({
                "ok": False,
                "error": "The server could not be saved. Your saved list was not changed.",
            }, warning)
        return servers._with_warning({"ok": True, "servers": saved_servers}, warning)

    def delete_server(self, server_id):
        try:
            saved_servers = [s for s in servers.load_servers() if s["id"] != server_id]
        except servers.ServerStoreError as exc:
            return {"ok": False, "error": str(exc)}
        warning = servers._take_server_store_warning()
        if not servers.save_servers(saved_servers):
            diagnostics.debug_log(f"delete_server write failed: {server_id}")
            return servers._with_warning({
                "ok": False,
                "error": "The server could not be deleted. Your saved list was not changed.",
            }, warning)
        self.disconnect(server_id)
        return servers._with_warning({"ok": True, "servers": saved_servers}, warning)

    # ---- connection -------------------------------------------------------

    def _finish_connect_attempt(self, server_id, attempt):
        """Clear an attempt token without disturbing a newer attempt."""
        with self._sessions_lock:
            if self._connect_attempts.get(server_id) is attempt:
                self._connect_attempts.pop(server_id, None)

    def _pop_session_if_current(self, server_id, sess):
        """Detach *sess* only when it is still this server's live session."""
        with self._sessions_lock:
            if self._sessions.get(server_id) is sess:
                return self._sessions.pop(server_id)
        return None

    def _session_is_current(self, server_id, sess):
        with self._sessions_lock:
            return self._sessions.get(server_id) is sess

    def connect(self, server_id, password):
        attempt = object()
        with self._sessions_lock:
            self._connect_attempts[server_id] = attempt
        try:
            server = next((s for s in servers.load_servers() if s["id"] == server_id), None)
        except servers.ServerStoreError as exc:
            self._finish_connect_attempt(server_id, attempt)
            return {"ok": False, "error": str(exc)}
        warning = servers._take_server_store_warning()
        if not server:
            self._finish_connect_attempt(server_id, attempt)
            return servers._with_warning({"ok": False, "error": "Server not found."}, warning)

        if server.get("game") not in catalog.CATALOG_BY_KEY:
            self._finish_connect_attempt(server_id, attempt)
            return servers._with_warning({"ok": False, "error":
                                  "This server's game is no longer supported. Edit the server to "
                                  "pick another game, or delete it."}, warning)

        if not password:
            self._finish_connect_attempt(server_id, attempt)
            return servers._with_warning({"ok": False, "error": "RCON password is required."}, warning)

        with self._sessions_lock:
            if self._connect_attempts.get(server_id) is not attempt:
                cancelled = True
                old_session = None
            else:
                cancelled = False
                old_session = self._sessions.pop(server_id, None)
        if cancelled:
            return servers._with_warning({"ok": False, "error": "Connection was cancelled."}, warning)
        if old_session:
            old_session.close()
            self._log(server_id, "Disconnected. Password cleared from memory.", "muted")
            self._emit("status", {"serverId": server_id, "state": "idle"})

        sess = rcon.Session(server, password)
        try:
            sess.connect()
        except Exception as e:
            self._finish_connect_attempt(server_id, attempt)
            return servers._with_warning({"ok": False, "error": diagnostics.friendly_error(e, "connect")}, warning)

        with self._sessions_lock:
            if self._connect_attempts.get(server_id) is attempt:
                self._sessions[server_id] = sess
                self._connect_attempts.pop(server_id, None)
                installed = True
            else:
                installed = False
        if not installed:
            sess.close()
            return servers._with_warning({"ok": False, "error": "Connection was cancelled."}, warning)
        self._log(server_id, f"Connected to {server['host']}:{server['port']}.", "ok")
        diagnostics.debug_log(f"Connected: {server.get('game')} {server['host']}:{server['port']}")
        return servers._with_warning({"ok": True}, warning)

    def disconnect(self, server_id):
        with self._sessions_lock:
            self._connect_attempts.pop(server_id, None)
            sess = self._sessions.pop(server_id, None)
        if sess:
            sess.close()
            self._log(server_id, "Disconnected. Password cleared from memory.", "muted")
        self._emit("status", {"serverId": server_id, "state": "idle"})
        return {"ok": True}

    def disconnect_all(self):
        with self._sessions_lock:
            self._connect_attempts.clear()
            sessions = list(self._sessions.items())
            self._sessions.clear()
        for server_id, sess in sessions:
            sess.close()
            self._log(server_id, "Disconnected. Password cleared from memory.", "muted")
            self._emit("status", {"serverId": server_id, "state": "idle"})
        return {"ok": True}

    # ---- sending commands -------------------------------------------------

    def send(self, server_id, command, label=None):
        with self._sessions_lock:
            sess = self._sessions.get(server_id)
        if not sess:
            return {"ok": False, "error": "Not connected."}
        command = (command or "").strip()
        if not command:
            return {"ok": False, "error": "Empty command."}
        threading.Thread(
            target=self._exec,
            args=(server_id, sess, command, label or command),
            daemon=True,
        ).start()
        return {"ok": True}

    def _exec(self, server_id, sess, command, label):
        if not self._session_is_current(server_id, sess):
            diagnostics.debug_log(f"Command skipped for replaced or closed session: {server_id}")
            return
        self._emit("status", {"serverId": server_id, "state": "running"})
        self._log(server_id, f"$ {label}", "cmd")
        game_key = getattr(sess, "server", {}).get("game")
        game = catalog.CATALOG_BY_KEY.get(game_key)
        if game and any(button["label"] == label for button in game["buttons"]):
            diagnostics.debug_log(f'send: quick button "{label}" (server {server_id})')
        else:
            diagnostics.debug_log(f"send: custom command (server {server_id}, {len(command)} characters)")

        try:
            resp = sess.command(command)
        except Exception as e:
            error = e
            resp = None
        else:
            error = None

        if not self._session_is_current(server_id, sess):
            if error is not None:
                sess.close()
            diagnostics.debug_log(f"Command outcome from replaced or closed session: {server_id}")
            return

        if rcon.is_stop_command(game_key, command):
            outcome = rcon.stop_outcome(resp, error)
            if outcome == "expected_disconnect":
                diagnostics.debug_log(f"command: {type(error).__name__}: {error}")
                self._log(server_id, "Server closed the connection, as expected after a stop command.", "muted")
                stopped_session = self._pop_session_if_current(server_id, sess)
                if stopped_session:
                    stopped_session.close()
                    self._log(server_id, "Disconnected. Password cleared from memory.", "muted")
                    self._emit("status", {"serverId": server_id, "state": "idle"})
                return
            if outcome == "replied":
                if resp:
                    for line in resp.splitlines():
                        self._log(server_id, line, "out")
                else:
                    self._log(server_id, "(no output)", "muted")
                stopped_session = self._pop_session_if_current(server_id, sess)
                if stopped_session:
                    stopped_session.close()
                    self._log(server_id, "Stop command sent. Disconnected. Password cleared from memory.", "muted")
                    self._emit("status", {"serverId": server_id, "state": "idle"})
                return

        if error is None:
            if resp is None:
                self._log(server_id, "No response from server. The command may not have run.", "warn")
            elif resp:
                for line in resp.splitlines():
                    self._log(server_id, line, "out")
            else:
                self._log(server_id, "(no output)", "muted")
            self._emit("status", {"serverId": server_id, "state": "connected"})
            return

        self._log(server_id, diagnostics.friendly_error(error, "command"), "err")
        failed_session = self._pop_session_if_current(server_id, sess)
        if failed_session:
            failed_session.close()
            self._log(server_id, "Disconnected. Password cleared from memory.", "muted")
            self._emit("status", {"serverId": server_id, "state": "idle"})

    # ---- export -----------------------------------------------------------

    def export_console(self, server_name, text):
        safe = (server_name or "RCON").replace(" ", "_")
        safe = re.sub(r'[\\/:*?"<>|]', "", safe)
        stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        base = f"{safe}_Console_{stamp}"
        try:
            for suffix in range(1, 100):
                fname = f"{base}.txt" if suffix == 1 else f"{base}_{suffix}.txt"
                path = os.path.join(paths.app_dir(), fname)
                try:
                    with open(path, "x", encoding="utf-8") as f:
                        f.write(text or "")
                    return {"ok": True, "path": path, "name": fname}
                except FileExistsError:
                    if suffix == 99:
                        raise
        except Exception as e:
            diagnostics.debug_log(f"Could not export console: {type(e).__name__}: {e}")
            return {"ok": False, "error": "The console could not be saved to a file."}



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

    api = Api()
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
