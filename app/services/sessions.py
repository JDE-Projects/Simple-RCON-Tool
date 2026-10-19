import os
import re
import threading
import time

from app import paths
from app.services import catalog, diagnostics, rcon, servers


def _finish_connect_attempt(api, server_id, attempt):
    """Clear an attempt token without disturbing a newer attempt."""
    with api._sessions_lock:
        if api._connect_attempts.get(server_id) is attempt:
            api._connect_attempts.pop(server_id, None)


def _pop_session_if_current(api, server_id, sess):
    """Detach *sess* only when it is still this server's live session."""
    with api._sessions_lock:
        if api._sessions.get(server_id) is sess:
            return api._sessions.pop(server_id)
    return None


def _session_is_current(api, server_id, sess):
    with api._sessions_lock:
        return api._sessions.get(server_id) is sess


def connect(api, server_id, password):
    attempt = object()
    with api._sessions_lock:
        api._connect_attempts[server_id] = attempt
    try:
        server = next((s for s in servers.load_servers() if s["id"] == server_id), None)
    except servers.ServerStoreError as exc:
        api._finish_connect_attempt(server_id, attempt)
        return {"ok": False, "error": str(exc)}
    warning = servers._take_server_store_warning()
    if not server:
        api._finish_connect_attempt(server_id, attempt)
        return servers._with_warning({"ok": False, "error": "Server not found."}, warning)

    if server.get("game") not in catalog.CATALOG_BY_KEY:
        api._finish_connect_attempt(server_id, attempt)
        return servers._with_warning({"ok": False, "error":
                              "This server's game is no longer supported. Edit the server to "
                              "pick another game, or delete it."}, warning)

    if not password:
        api._finish_connect_attempt(server_id, attempt)
        return servers._with_warning({"ok": False, "error": "RCON password is required."}, warning)

    with api._sessions_lock:
        if api._connect_attempts.get(server_id) is not attempt:
            cancelled = True
            old_session = None
        else:
            cancelled = False
            old_session = api._sessions.pop(server_id, None)
    if cancelled:
        return servers._with_warning({"ok": False, "error": "Connection was cancelled."}, warning)
    if old_session:
        old_session.close()
        api._log(server_id, "Disconnected. Password cleared from memory.", "muted")
        api._emit("status", {"serverId": server_id, "state": "idle"})

    sess = rcon.Session(server, password)
    try:
        sess.connect()
    except Exception as e:
        api._finish_connect_attempt(server_id, attempt)
        return servers._with_warning({"ok": False, "error": diagnostics.friendly_error(e, "connect")}, warning)

    with api._sessions_lock:
        if api._connect_attempts.get(server_id) is attempt:
            api._sessions[server_id] = sess
            api._connect_attempts.pop(server_id, None)
            installed = True
        else:
            installed = False
    if not installed:
        sess.close()
        return servers._with_warning({"ok": False, "error": "Connection was cancelled."}, warning)
    api._log(server_id, f"Connected to {server['host']}:{server['port']}.", "ok")
    diagnostics.debug_log(f"Connected: {server.get('game')} {server['host']}:{server['port']}")
    return servers._with_warning({"ok": True}, warning)


def disconnect(api, server_id):
    with api._sessions_lock:
        api._connect_attempts.pop(server_id, None)
        sess = api._sessions.pop(server_id, None)
    if sess:
        sess.close()
        api._log(server_id, "Disconnected. Password cleared from memory.", "muted")
    api._emit("status", {"serverId": server_id, "state": "idle"})
    return {"ok": True}


def disconnect_all(api):
    with api._sessions_lock:
        api._connect_attempts.clear()
        sessions = list(api._sessions.items())
        api._sessions.clear()
    for server_id, sess in sessions:
        sess.close()
        api._log(server_id, "Disconnected. Password cleared from memory.", "muted")
        api._emit("status", {"serverId": server_id, "state": "idle"})
    return {"ok": True}


def send(api, server_id, command, label=None):
    with api._sessions_lock:
        sess = api._sessions.get(server_id)
    if not sess:
        return {"ok": False, "error": "Not connected."}
    command = (command or "").strip()
    if not command:
        return {"ok": False, "error": "Empty command."}
    threading.Thread(
        target=api._exec,
        args=(server_id, sess, command, label or command),
        daemon=True,
    ).start()
    return {"ok": True}


def _exec(api, server_id, sess, command, label):
    if not api._session_is_current(server_id, sess):
        diagnostics.debug_log(f"Command skipped for replaced or closed session: {server_id}")
        return
    api._emit("status", {"serverId": server_id, "state": "running"})
    api._log(server_id, f"$ {label}", "cmd")
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

    if not api._session_is_current(server_id, sess):
        if error is not None:
            sess.close()
        diagnostics.debug_log(f"Command outcome from replaced or closed session: {server_id}")
        return

    if rcon.is_stop_command(game_key, command):
        outcome = rcon.stop_outcome(resp, error)
        if outcome == "expected_disconnect":
            diagnostics.debug_log(f"command: {type(error).__name__}: {error}")
            api._log(server_id, "Server closed the connection, as expected after a stop command.", "muted")
            stopped_session = api._pop_session_if_current(server_id, sess)
            if stopped_session:
                stopped_session.close()
                api._log(server_id, "Disconnected. Password cleared from memory.", "muted")
                api._emit("status", {"serverId": server_id, "state": "idle"})
            return
        if outcome == "replied":
            if resp:
                for line in resp.splitlines():
                    api._log(server_id, line, "out")
            else:
                api._log(server_id, "(no output)", "muted")
            stopped_session = api._pop_session_if_current(server_id, sess)
            if stopped_session:
                stopped_session.close()
                api._log(server_id, "Stop command sent. Disconnected. Password cleared from memory.", "muted")
                api._emit("status", {"serverId": server_id, "state": "idle"})
            return

    if error is None:
        if resp is None:
            api._log(server_id, "No response from server. The command may not have run.", "warn")
        elif resp:
            for line in resp.splitlines():
                api._log(server_id, line, "out")
        else:
            api._log(server_id, "(no output)", "muted")
        api._emit("status", {"serverId": server_id, "state": "connected"})
        return

    api._log(server_id, diagnostics.friendly_error(error, "command"), "err")
    failed_session = api._pop_session_if_current(server_id, sess)
    if failed_session:
        failed_session.close()
        api._log(server_id, "Disconnected. Password cleared from memory.", "muted")
        api._emit("status", {"serverId": server_id, "state": "idle"})


def export_console(api, server_name, text):
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
