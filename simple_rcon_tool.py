"""
Simple RCON Tool
A standalone desktop tool to send game-server RCON commands.

Backend: pywebview window host + a small Source RCON client (standard library).
Frontend: simple_rcon_tool-UI.html (web-style interface).

RCON passwords are held in memory only. They are never written to disk.
Only the server name / host / port / game key / custom buttons are saved in
servers.json. The built-in command buttons for each game come from the catalog
below, so updating this file updates everyone's buttons without touching their
saved servers.

Project: Simple RCON Tool
Author:  JDE-Projects  (https://github.com/JDE-Projects)
"""

import os
import re
import sys
import json
import time
import ctypes
import socket
import struct
import threading
import urllib.request
import urllib.error

# Bind to PySide6 (LGPL), never PyQt6 (GPL). Set before webview imports Qt so
# qtpy resolves to PySide6 even if PyQt6 happens to be present.
os.environ.setdefault("QT_API", "pyside6")

import webview


# ----------------------------------------------------------------------------
# Version & update source
# ----------------------------------------------------------------------------
# APP_VERSION is the version of record; it equals the latest published release
# tag (without the leading "v"). Held at 1.0.0 while in pre-release testing.
APP_VERSION = "1.0.0"

# Update check hits this repo's GitHub Releases. Returns 404 while the repo is
# private (pre-release), which the check treats as "no update" and stays quiet.
GITHUB_OWNER = "JDE-Projects"
GITHUB_REPO = "Simple-RCON-Tool"


# ----------------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------------

def app_dir():
    """Folder for servers.json. Sits next to the .exe when frozen."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(name):
    """Locate bundled files whether running as a script or a PyInstaller exe."""
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, name)


SERVERS_FILE = os.path.join(app_dir(), "servers.json")


# ----------------------------------------------------------------------------
# Optional debug log (off by default)
# ----------------------------------------------------------------------------
# One in-app toggle, off by default. When on, it writes one file per run,
# Debug_Log_MMDDYYYY_HHMMSS.txt next to the exe. Credentials are never passed
# to the log by construction (the RCON password lives only in the AUTH packet,
# which is not logged); a guard still scrubs any "password" token just in case.
# When off, nothing is written. Gitignore pattern: Debug_Log_*.txt.

_debug_enabled = False
_debug_path = None
_debug_lock = threading.Lock()
_SECRET_RE = re.compile(r'(password["\']?\s*[:=]\s*)(\S+)', re.IGNORECASE)


def _redact(text):
    return _SECRET_RE.sub(r"\1***", str(text))


def set_debug(enabled):
    """Turn the debug log on or off. Returns the active state."""
    global _debug_enabled
    _debug_enabled = bool(enabled)
    if _debug_enabled:
        debug_log(f"Debug log enabled. Simple RCON Tool v{APP_VERSION}.")
    return _debug_enabled


def debug_log(msg):
    if not _debug_enabled:
        return
    global _debug_path
    try:
        with _debug_lock:
            if _debug_path is None:
                stamp = time.strftime("%m%d%Y_%H%M%S")
                _debug_path = os.path.join(app_dir(), f"Debug_Log_{stamp}.txt")
            line = time.strftime("%H:%M:%S") + "  " + _redact(msg) + "\n"
            with open(_debug_path, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass  # logging must never crash the app


# ----------------------------------------------------------------------------
# Plain-language errors
# ----------------------------------------------------------------------------
# Never surface a raw [Errno N] or exception text to the user. Map to a friendly
# message and send the full detail to the debug log.

def friendly_error(exc, context=""):
    debug_log(f"{context}: {type(exc).__name__}: {exc}")
    if isinstance(exc, RconError):
        return str(exc)  # already plain language
    if isinstance(exc, socket.timeout):
        return "Timed out. Check the host, port, and that RCON is enabled."
    if isinstance(exc, ConnectionRefusedError):
        return "Connection refused. Wrong port, or RCON is not enabled on this server."
    if isinstance(exc, socket.gaierror):
        return "Host not found. Check the address."
    if isinstance(exc, PermissionError):
        return "Permission denied."
    if isinstance(exc, OSError):
        return "Network error. Check the host and your connection."
    return "Something went wrong. Turn on the debug log for details."


# ----------------------------------------------------------------------------
# Version compare (semantic, numeric)
# ----------------------------------------------------------------------------

def _version_parts(v):
    nums = []
    for part in str(v).strip().lstrip("v").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        nums.append(int(digits) if digits else 0)
    return nums


def _version_gt(a, b):
    """True if version a is newer than version b."""
    pa, pb = _version_parts(a), _version_parts(b)
    n = max(len(pa), len(pb))
    pa += [0] * (n - len(pa))
    pb += [0] * (n - len(pb))
    return pa > pb


# ----------------------------------------------------------------------------
# Game catalog
# ----------------------------------------------------------------------------
# Each game carries:
#   key       stable identifier stored in servers.json
#   name      display name shown in the picker
#   protocol  "source" connects today; anything else is gated at connect time
#   note      shown in the editor (mod requirements, quirks, status)
#   buttons   locked command buttons rendered for every server of this game
#
# Button fields:
#   label     button text
#   command   RCON command; "{arg}" pops an input box when clicked
#   confirm   optional confirmation message string for destructive actions
#   argmode   optional "underscore" to swap spaces for underscores in the arg
#
# Commands were verified per game. Only games with genuinely useful command
# sets are included. Non-source games appear but are gated until their client
# is added in a later update.

GAME_CATALOG = [
    {
        "key": "ark_se", "name": "ARK: Survival Evolved", "protocol": "source",
        "note": "Commands run as admin over RCON; no admincheat prefix needed.",
        "buttons": [
            {"label": "List Players", "command": "ListPlayers"},
            {"label": "Save World", "command": "SaveWorld"},
            {"label": "Get Chat", "command": "GetChat"},
            {"label": "Broadcast", "command": "Broadcast {arg}"},
            {"label": "Server Message", "command": "ServerChat {arg}"},
            {"label": "Destroy Wild Dinos", "command": "DestroyWildDinos",
             "confirm": "Destroy all wild creatures on the map now?"},
            {"label": "Kick Player", "command": "KickPlayer {arg}"},
            {"label": "Shutdown", "command": "DoExit",
             "confirm": "Shut the server down now?"},
        ],
    },
    {
        "key": "ark_asa", "name": "ARK: Survival Ascended", "protocol": "source",
        "note": "Same command set as Survival Evolved.",
        "buttons": [
            {"label": "List Players", "command": "ListPlayers"},
            {"label": "Save World", "command": "SaveWorld"},
            {"label": "Get Chat", "command": "GetChat"},
            {"label": "Broadcast", "command": "Broadcast {arg}"},
            {"label": "Server Message", "command": "ServerChat {arg}"},
            {"label": "Destroy Wild Dinos", "command": "DestroyWildDinos",
             "confirm": "Destroy all wild creatures on the map now?"},
            {"label": "Kick Player", "command": "KickPlayer {arg}"},
            {"label": "Shutdown", "command": "DoExit",
             "confirm": "Shut the server down now?"},
        ],
    },
    {
        "key": "conan", "name": "Conan Exiles", "protocol": "source",
        "note": "kick/ban take a type+id+message format; use the custom box for those.",
        "buttons": [
            {"label": "List Players", "command": "listplayers"},
            {"label": "List Bans", "command": "listbans"},
            {"label": "Broadcast", "command": "broadcast {arg}"},
            {"label": "Help", "command": "help"},
        ],
    },
    {
        "key": "palworld", "name": "Palworld", "protocol": "source",
        "note": "Commands are case-sensitive. Broadcast cannot contain spaces "
                "(spaces are auto-converted to underscores).",
        "buttons": [
            {"label": "Show Players", "command": "ShowPlayers"},
            {"label": "Info", "command": "Info"},
            {"label": "Save", "command": "Save"},
            {"label": "Broadcast", "command": "Broadcast {arg}", "argmode": "underscore"},
            {"label": "Kick Player", "command": "KickPlayer {arg}"},
            {"label": "Ban Player", "command": "BanPlayer {arg}",
             "confirm": "Ban this player (SteamID)?"},
            {"label": "Shutdown 60s", "command": "Shutdown 60 Restarting",
             "confirm": "Begin a 60-second shutdown?"},
            {"label": "Force Stop", "command": "DoExit",
             "confirm": "Force the server to stop now?"},
        ],
    },
    {
        "key": "minecraft", "name": "Minecraft (Java)", "protocol": "source",
        "note": "Standard Java-edition console commands.",
        "buttons": [
            {"label": "List", "command": "list"},
            {"label": "Say", "command": "say {arg}"},
            {"label": "Save All", "command": "save-all"},
            {"label": "Kick", "command": "kick {arg}"},
            {"label": "Ban", "command": "ban {arg}", "confirm": "Ban this player?"},
            {"label": "Ban List", "command": "banlist"},
            {"label": "Weather Clear", "command": "weather clear"},
            {"label": "Set Day", "command": "time set day"},
            {"label": "Stop Server", "command": "stop", "confirm": "Stop the server?"},
        ],
    },
    {
        "key": "cs2", "name": "Counter-Strike 2", "protocol": "source",
        "note": "Source engine console commands.",
        "buttons": [
            {"label": "Status", "command": "status"},
            {"label": "Say", "command": "say {arg}"},
            {"label": "Kick", "command": "kick {arg}"},
            {"label": "Change Level", "command": "changelevel {arg}"},
            {"label": "List Bans", "command": "listid"},
        ],
    },
    {
        "key": "pz", "name": "Project Zomboid", "protocol": "source",
        "note": "No leading slash over RCON. Quotes around multi-word names.",
        "buttons": [
            {"label": "Players", "command": "players"},
            {"label": "Save", "command": "save"},
            {"label": "Server Message", "command": "servermsg {arg}"},
            {"label": "Kick User", "command": "kickuser {arg}"},
            {"label": "Ban User", "command": "banuser {arg}", "confirm": "Ban this user?"},
            {"label": "Show Options", "command": "showoptions"},
            {"label": "Save & Quit", "command": "quit", "confirm": "Save and stop the server?"},
        ],
    },
    {
        "key": "factorio", "name": "Factorio", "protocol": "source",
        "note": "Commands use a leading slash. /c runs Lua and disables achievements.",
        "buttons": [
            {"label": "Players", "command": "/players"},
            {"label": "Admins", "command": "/admins"},
            {"label": "Save", "command": "/save"},
            {"label": "Bans", "command": "/bans"},
            {"label": "Kick", "command": "/kick {arg}"},
            {"label": "Ban", "command": "/ban {arg}", "confirm": "Ban this player?"},
            {"label": "Quit", "command": "/quit", "confirm": "Stop the server?"},
        ],
    },
    {
        "key": "squad", "name": "Squad", "protocol": "source",
        "note": "RCON port is usually separate from the game port (often 21114).",
        "buttons": [
            {"label": "List Players", "command": "ListPlayers"},
            {"label": "List Squads", "command": "ListSquads"},
            {"label": "Show Next Map", "command": "ShowNextMap"},
            {"label": "Broadcast", "command": "AdminBroadcast {arg}"},
            {"label": "Kick by ID", "command": "AdminKickById {arg}"},
            {"label": "Restart Match", "command": "AdminRestartMatch",
             "confirm": "Restart the current match?"},
            {"label": "End Match", "command": "AdminEndMatch",
             "confirm": "End the current match?"},
        ],
    },
    {
        "key": "gmod", "name": "Garry's Mod", "protocol": "source",
        "note": "Source engine console commands.",
        "buttons": [
            {"label": "Status", "command": "status"},
            {"label": "Say", "command": "say {arg}"},
            {"label": "Kick", "command": "kick {arg}"},
            {"label": "Change Level", "command": "changelevel {arg}"},
        ],
    },
    {
        "key": "eco", "name": "Eco", "protocol": "source",
        "note": "Loosely Source-compatible (default port 3002). Kick/Ban use a "
                "Name,Reason format. Test entry; verify against your build.",
        "buttons": [
            {"label": "Who Is", "command": "whois {arg}"},
            {"label": "Kick", "command": "kick {arg}"},
            {"label": "Ban", "command": "ban {arg}",
             "confirm": "Ban this player? (format: Name,Reason)"},
        ],
    },
    {
        "key": "windrose", "name": "Windrose", "protocol": "source",
        "note": "Requires the community WindroseRCON mod on the server.",
        "buttons": [
            {"label": "Info", "command": "info"},
            {"label": "Show Players", "command": "showplayers"},
            {"label": "Kick", "command": "kick {arg}"},
            {"label": "Ban", "command": "ban {arg}", "confirm": "Ban this player?"},
            {"label": "Unban", "command": "unban {arg}"},
            {"label": "Ban List", "command": "banlist"},
        ],
    },
    {
        "key": "valheim", "name": "Valheim", "protocol": "source",
        "note": "Requires the ValheimRcon mod. Crossplay must be off "
                "(BepInEx mods do not load with crossplay on).",
        "buttons": [
            {"label": "Save", "command": "save"},
            {"label": "Kick", "command": "kick {arg}"},
            {"label": "Ban", "command": "ban {arg}", "confirm": "Ban this player?"},
            {"label": "Unban", "command": "unban {arg}"},
            {"label": "List Bans", "command": "banned"},
        ],
    },

    # --- staged: present in the picker, gated at connect until a client lands -
    {
        "key": "rust", "name": "Rust", "protocol": "websocket",
        "note": "Rust uses WebSocket RCON. A client is coming in a later update.",
        "buttons": [],
    },
    {
        "key": "7dtd", "name": "7 Days to Die", "protocol": "telnet",
        "note": "7 Days to Die uses Telnet. A client is coming in a later update.",
        "buttons": [],
    },
    {
        "key": "soulmask", "name": "Soulmask", "protocol": "telnet",
        "note": "Soulmask uses a Telnet console. A client is coming in a later update.",
        "buttons": [],
    },
    {
        "key": "dayz", "name": "DayZ", "protocol": "battleye",
        "note": "DayZ uses BattlEye RCON. A client is coming in a later update.",
        "buttons": [],
    },
    {
        "key": "satisfactory", "name": "Satisfactory", "protocol": "https",
        "note": "Satisfactory uses an HTTPS API. A client is coming in a later update.",
        "buttons": [],
    },
]

CATALOG_BY_KEY = {g["key"]: g for g in GAME_CATALOG}

PROTOCOL_LABEL = {
    "websocket": "WebSocket RCON",
    "telnet": "Telnet",
    "battleye": "BattlEye RCON",
    "https": "HTTPS API",
}


def game_protocol(game_key):
    g = CATALOG_BY_KEY.get(game_key)
    return g["protocol"] if g else "source"


# ----------------------------------------------------------------------------
# Source RCON client (standard library only)
# ----------------------------------------------------------------------------

class RconError(Exception):
    pass


class RconClient:
    SERVERDATA_AUTH = 3
    SERVERDATA_AUTH_RESPONSE = 2
    SERVERDATA_EXECCOMMAND = 2
    SERVERDATA_RESPONSE_VALUE = 0

    def __init__(self, host, port, password, timeout=8):
        self.host = host
        self.port = int(port)
        self.password = password  # memory only
        self.timeout = timeout
        self.sock = None
        self._id = 0

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        self._authenticate()

    def _next_id(self):
        self._id += 1
        return self._id

    def _send(self, type_, body):
        req_id = self._next_id()
        payload = struct.pack("<ii", req_id, type_) + body.encode("utf-8") + b"\x00\x00"
        packet = struct.pack("<i", len(payload)) + payload
        self.sock.sendall(packet)
        return req_id

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise RconError("Connection closed by server.")
            buf += chunk
        return buf

    def _recv_packet(self):
        (length,) = struct.unpack("<i", self._recv_exact(4))
        data = self._recv_exact(length)
        req_id, type_ = struct.unpack("<ii", data[:8])
        body = data[8:-2]  # strip the two trailing null bytes
        return req_id, type_, body.decode("utf-8", errors="replace")

    def _authenticate(self):
        self._send(self.SERVERDATA_AUTH, self.password)
        while True:
            req_id, type_, _ = self._recv_packet()
            if type_ == self.SERVERDATA_AUTH_RESPONSE:
                if req_id == -1:
                    raise RconError("Authentication failed (wrong RCON password).")
                return
            # ignore the mirrored empty packet some servers send first

    def command(self, cmd):
        cmd_id = self._send(self.SERVERDATA_EXECCOMMAND, cmd)
        sentinel_id = self._send(self.SERVERDATA_RESPONSE_VALUE, "")
        parts = []
        try:
            while True:
                req_id, _, body = self._recv_packet()
                if req_id == sentinel_id:
                    break
                parts.append(body)
        except socket.timeout:
            pass  # some servers do not echo the sentinel; return what we have
        return "".join(parts).strip()

    def close(self):
        self.password = None  # wipe from memory
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None


# ----------------------------------------------------------------------------
# A single live RCON session (one per connected server)
# ----------------------------------------------------------------------------

class Session:
    def __init__(self, server, password):
        self.server = server
        self.client = RconClient(server["host"], server["port"], password)
        self.lock = threading.Lock()  # one command at a time per socket

    def connect(self):
        self.client.connect()

    def command(self, cmd):
        with self.lock:
            return self.client.command(cmd)

    def close(self):
        try:
            self.client.close()
        except Exception:
            pass


# ----------------------------------------------------------------------------
# Server storage (no defaults, no baked IP/port; empty on first run)
# ----------------------------------------------------------------------------

def load_servers():
    if not os.path.exists(SERVERS_FILE):
        return []
    try:
        with open(SERVERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            for s in data:
                if not isinstance(s.get("buttons"), list):
                    s["buttons"] = []
            return data
    except Exception:
        pass
    return []


def save_servers(servers):
    try:
        with open(SERVERS_FILE, "w", encoding="utf-8") as f:
            json.dump(servers, f, indent=2)
        return True
    except Exception:
        return False


# ----------------------------------------------------------------------------
# API exposed to the JavaScript frontend
# ----------------------------------------------------------------------------

class Api:
    def __init__(self):
        self.sessions = {}  # server_id -> Session
        self.window = None
        self.frontend_ready = False

    # ---- frontend helpers -------------------------------------------------

    def _emit(self, channel, payload):
        if not self.window or not self.frontend_ready:
            return
        data = json.dumps(payload)
        js = f"window.__onPyEvent && window.__onPyEvent({json.dumps(channel)}, {data});"
        try:
            self.window.evaluate_js(js)
        except Exception:
            pass

    def _log(self, server_id, text, level="out"):
        self._emit("log", {"serverId": server_id, "text": text, "level": level})

    # ---- config -----------------------------------------------------------

    def get_config(self):
        return {
            "servers": load_servers(),
            "catalog": GAME_CATALOG,
            "version": APP_VERSION,
            "debug": _debug_enabled,
        }

    # ---- debug log toggle -------------------------------------------------

    def set_debug(self, enabled):
        return {"ok": True, "debug": set_debug(enabled)}

    def get_debug(self):
        return {"ok": True, "debug": _debug_enabled}

    # ---- update check -----------------------------------------------------

    def check_update(self):
        """Compare the latest GitHub release tag to APP_VERSION. Quiet when
        offline or while the repo is private (404). Never raises to the UI."""
        url = (f"https://api.github.com/repos/{GITHUB_OWNER}/"
               f"{GITHUB_REPO}/releases/latest")
        try:
            req = urllib.request.Request(url, headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "Simple-RCON-Tool",
            })
            with urllib.request.urlopen(req, timeout=6) as r:
                data = json.loads(r.read().decode("utf-8"))
            tag = (data.get("tag_name") or "").lstrip("v")
            if tag and _version_gt(tag, APP_VERSION):
                return {
                    "ok": True, "update": True, "version": tag,
                    "current": APP_VERSION,
                    "url": data.get("html_url") or
                           f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases",
                }
            return {"ok": True, "update": False, "current": APP_VERSION}
        except urllib.error.HTTPError as e:
            debug_log(f"update check HTTP {e.code} (private repo returns 404)")
            return {"ok": True, "update": False, "current": APP_VERSION}
        except Exception as e:
            debug_log(f"update check failed (offline?): {e}")
            return {"ok": True, "update": False, "current": APP_VERSION,
                    "offline": True}

    # ---- server CRUD ------------------------------------------------------

    def save_server(self, server):
        # never persist a password, even if one is somehow supplied
        server.pop("password", None)
        if not isinstance(server.get("buttons"), list):
            server["buttons"] = []
        try:
            server["port"] = int(server.get("port"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "Port must be a number."}
        if not server.get("game") or server["game"] not in CATALOG_BY_KEY:
            return {"ok": False, "error": "Pick a game from the list."}

        servers = load_servers()
        if server.get("id"):
            for i, s in enumerate(servers):
                if s["id"] == server["id"]:
                    servers[i] = server
                    break
            else:
                servers.append(server)
        else:
            server["id"] = "srv" + str(int(threading.get_ident()))[:6] + str(len(servers))
            servers.append(server)
        save_servers(servers)
        return {"ok": True, "servers": servers}

    def delete_server(self, server_id):
        servers = [s for s in load_servers() if s["id"] != server_id]
        save_servers(servers)
        self.disconnect(server_id)
        return {"ok": True, "servers": servers}

    # ---- connection -------------------------------------------------------

    def connect(self, server_id, password):
        server = next((s for s in load_servers() if s["id"] == server_id), None)
        if not server:
            return {"ok": False, "error": "Server not found."}

        proto = game_protocol(server.get("game"))
        if proto != "source":
            label = PROTOCOL_LABEL.get(proto, proto)
            return {"ok": False, "error":
                    f"This game uses {label}, which this build does not speak yet. "
                    f"A client for it is coming in a later update."}

        if not password:
            return {"ok": False, "error": "RCON password is required."}

        if server_id in self.sessions:
            self.disconnect(server_id)

        sess = Session(server, password)
        try:
            sess.connect()
        except Exception as e:
            return {"ok": False, "error": friendly_error(e, "connect")}

        self.sessions[server_id] = sess
        self._log(server_id, f"Connected to {server['host']}:{server['port']}.", "ok")
        debug_log(f"Connected: {server.get('game')} {server['host']}:{server['port']}")
        return {"ok": True}

    def disconnect(self, server_id):
        sess = self.sessions.pop(server_id, None)
        if sess:
            sess.close()
            self._log(server_id, "Disconnected. Password cleared from memory.", "muted")
        self._emit("status", {"serverId": server_id, "state": "idle"})
        return {"ok": True}

    def disconnect_all(self):
        for server_id in list(self.sessions.keys()):
            self.disconnect(server_id)
        return {"ok": True}

    # ---- sending commands -------------------------------------------------

    def send(self, server_id, command, label=None):
        sess = self.sessions.get(server_id)
        if not sess:
            return {"ok": False, "error": "Not connected."}
        command = (command or "").strip()
        if not command:
            return {"ok": False, "error": "Empty command."}
        threading.Thread(
            target=self._exec,
            args=(server_id, command, label or command),
            daemon=True,
        ).start()
        return {"ok": True}

    def _exec(self, server_id, command, label):
        sess = self.sessions.get(server_id)
        if not sess:
            self._log(server_id, "Not connected.", "err")
            return
        self._emit("status", {"serverId": server_id, "state": "running"})
        self._log(server_id, f"$ {label}", "cmd")
        debug_log(f"send: {label}")
        ok = True
        try:
            resp = sess.command(command)
            if resp:
                for line in resp.splitlines():
                    self._log(server_id, line, "out")
            else:
                self._log(server_id, "(no output)", "muted")
        except Exception as e:
            ok = False
            self._log(server_id, friendly_error(e, "command"), "err")
            self.disconnect(server_id)
        finally:
            if ok:
                self._emit("status", {"serverId": server_id, "state": "connected"})

    # ---- export -----------------------------------------------------------

    def export_console(self, server_name, text):
        safe = (server_name or "RCON").replace(" ", "_")
        safe = re.sub(r'[\\/:*?"<>|]', "", safe)
        stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        fname = f"{safe}_Console_{stamp}.txt"
        path = os.path.join(app_dir(), fname)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text or "")
            return {"ok": True, "path": path, "name": fname}
        except Exception as e:
            return {"ok": False, "error": str(e)}


# ----------------------------------------------------------------------------
# Splash (PyInstaller only)
# ----------------------------------------------------------------------------
# The splash exists only inside the frozen .exe. Quick-loading apps keep it up
# for a minimum floor so it does not just flash; slow apps keep it up until the
# window reports ready; a watchdog closes it by the ceiling no matter what.

SPLASH_MIN_SECONDS = 5.0    # quick apps: stay visible at least this long
SPLASH_MAX_SECONDS = 30.0   # watchdog ceiling: always close by here

_app_start = time.monotonic()
_splash_closed = threading.Event()


def _close_splash():
    if _splash_closed.is_set():
        return
    _splash_closed.set()
    try:
        import pyi_splash  # only present inside the frozen exe
        pyi_splash.close()
    except Exception:
        pass


def _close_splash_when_ready():
    remaining = SPLASH_MIN_SECONDS - (time.monotonic() - _app_start)
    if remaining > 0:
        time.sleep(remaining)
    _close_splash()


def _splash_watchdog():
    time.sleep(SPLASH_MAX_SECONDS)
    _close_splash()


# ----------------------------------------------------------------------------
# Boot
# ----------------------------------------------------------------------------

def main():
    # Windows app identity so the taskbar shows our icon, not the generic Qt one
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "JDEProjects.SimpleRconTool"
            )
        except Exception:
            pass

    # Watchdog guarantees the splash always clears, even if 'loaded' never fires
    threading.Thread(target=_splash_watchdog, daemon=True).start()

    api = Api()
    html_path = resource_path("simple_rcon_tool-UI.html")
    window = webview.create_window(
        "Simple RCON Tool",
        url=html_path,
        js_api=api,
        width=1240,
        height=860,
        min_size=(980, 660),
        background_color="#0a0e14",
    )
    api.window = window

    def on_loaded():
        api.frontend_ready = True
        # honor the minimum floor, then close the splash
        threading.Thread(target=_close_splash_when_ready, daemon=True).start()

    try:
        window.events.loaded += on_loaded
    except Exception:
        api.frontend_ready = True
        threading.Thread(target=_close_splash_when_ready, daemon=True).start()

    # Qt backend (PySide6 + WebEngine). Pass the PNG so the live taskbar icon
    # is ours; guarded so an older pywebview without the icon arg still launches.
    try:
        webview.start(gui="qt", icon=resource_path("simple_rcon_tool.png"))
    except TypeError:
        webview.start(gui="qt")


if __name__ == "__main__":
    main()
