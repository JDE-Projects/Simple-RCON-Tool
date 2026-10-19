import threading

from app.services import (
    diagnostics, frontend, links, prefs, servers, sessions, updates,
)


class Api:
    """The bridge object the page calls. It holds live state in underscore
    attributes only; every method hands off to a service function."""

    def __init__(self, version):
        self._version = version
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
        return frontend._mark_frontend_ready(self)

    def _handle_debug_warning(self, message):
        return frontend._handle_debug_warning(self, message)

    def _emit(self, channel, payload):
        return frontend._emit(self, channel, payload)

    def _log(self, server_id, text, level="out"):
        return frontend._log(self, server_id, text, level)

    def open_url(self, url):
        return links.open_url(self, url)

    # ---- config -----------------------------------------------------------

    def get_config(self):
        return prefs.get_config(self)

    # ---- theme persistence --------------------------------------------------

    def get_theme(self):
        return prefs.get_theme(self)

    def save_theme(self, theme):
        return prefs.save_theme(self, theme)

    # ---- debug log toggle -------------------------------------------------

    def set_debug(self, enabled):
        return {"ok": True, "debug": diagnostics.set_debug(enabled, self._version)}

    # ---- update check -----------------------------------------------------

    def check_update(self):
        return updates.check_update(self)

    # ---- server CRUD ------------------------------------------------------

    def save_server(self, server):
        return servers.save_server(self, server)

    def delete_server(self, server_id):
        return servers.delete_server(self, server_id)

    # ---- connection -------------------------------------------------------

    def _finish_connect_attempt(self, server_id, attempt):
        return sessions._finish_connect_attempt(self, server_id, attempt)

    def _pop_session_if_current(self, server_id, sess):
        return sessions._pop_session_if_current(self, server_id, sess)

    def _session_is_current(self, server_id, sess):
        return sessions._session_is_current(self, server_id, sess)

    def connect(self, server_id, password):
        return sessions.connect(self, server_id, password)

    def disconnect(self, server_id):
        return sessions.disconnect(self, server_id)

    def disconnect_all(self):
        return sessions.disconnect_all(self)

    # ---- sending commands -------------------------------------------------

    def send(self, server_id, command, label=None):
        return sessions.send(self, server_id, command, label)

    def _exec(self, server_id, sess, command, label):
        return sessions._exec(self, server_id, sess, command, label)

    # ---- export -----------------------------------------------------------

    def export_console(self, server_name, text):
        return sessions.export_console(self, server_name, text)
