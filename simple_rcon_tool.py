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
import errno
import ctypes
import shlex
import shutil
import tempfile
import uuid
from ctypes import wintypes
import socket
import ssl
import struct
import threading
import urllib.request
import urllib.error
import urllib.parse
import webbrowser

# Bind to PySide6 (LGPL), never PyQt6 (GPL). Set before webview imports Qt so
# qtpy resolves to PySide6 even if PyQt6 happens to be present.
os.environ.setdefault("QT_API", "pyside6")

import webview

from debug_log import DebugLog


# ----------------------------------------------------------------------------
# Version & update source
# ----------------------------------------------------------------------------
# APP_VERSION is the version of record; it equals the release tag without the
# leading "v".
APP_VERSION = "1.5.0"

# Update check hits this repo's GitHub Releases. Returns 404 while the repo is
# private (pre-release), which the check treats as "no update" and stays quiet.
GITHUB_OWNER = "JDE-Projects"
GITHUB_REPO = "Simple-RCON-Tool"
JDE_PROJECTS_URL = "https://jde-projects.com"
ALLOWED_URL_HOST = "jde-projects.com"


def _is_allowed_url(url):
    """True only for a plain https address on the JDE-Projects website: no
    other host, scheme, port, login part, or whitespace/control characters."""
    if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
        return False
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    # Comparing the whole netloc rules out ports and user@host tricks.
    return parts.scheme == "https" and parts.netloc == ALLOWED_URL_HOST


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
_server_store_warning = None


class ServerStoreError(Exception):
    """A plain-language error reading or preserving saved server data."""


# ----------------------------------------------------------------------------
# Local prefs store. One JSON file next to the app holds EVERY persisted
# setting: theme, window geometry, and anything added later. Always read-
# merge-write through load_prefs / save_prefs. Never overwrite the file with
# a single key, or the next setting you add silently wipes the others.
# ----------------------------------------------------------------------------

def _pref_path() -> str:
    return os.path.join(app_dir(), "simple_rcon_tool.pref")


def _write_json_atomic(path, data, **dump_kwargs) -> bool:
    """Write JSON data without changing the destination unless replacement succeeds."""
    try:
        contents = json.dumps(data, **dump_kwargs)
    except Exception as exc:
        debug_log(f"_write_json_atomic serialize failed: {type(exc).__name__}: {exc}")
        return False

    temp_path = None
    file_handle = None
    file_descriptor = None
    step = "create"
    try:
        destination_folder = os.path.dirname(os.path.abspath(path))
        destination_name = os.path.basename(path)
        file_descriptor, temp_path = tempfile.mkstemp(
            dir=destination_folder,
            prefix=f".{destination_name}.",
            suffix=".tmp",
        )
        step = "open"
        file_handle = os.fdopen(file_descriptor, "w", encoding="utf-8")
        file_descriptor = None
        step = "write"
        file_handle.write(contents)
        step = "flush"
        file_handle.flush()
        step = "fsync"
        os.fsync(file_handle.fileno())
        step = "close"
        file_handle.close()
        file_handle = None
        step = "replace"
        os.replace(temp_path, path)
        temp_path = None
        return True
    except Exception as exc:
        debug_log(f"_write_json_atomic {step} failed: {type(exc).__name__}: {exc}")
        return False
    finally:
        if file_handle is not None:
            try:
                file_handle.close()
            except Exception as exc:
                debug_log(f"_write_json_atomic cleanup close failed: {type(exc).__name__}: {exc}")
        elif file_descriptor is not None:
            try:
                os.close(file_descriptor)
            except Exception as exc:
                debug_log(f"_write_json_atomic cleanup close failed: {type(exc).__name__}: {exc}")
        if temp_path is not None:
            try:
                os.remove(temp_path)
            except FileNotFoundError:
                pass
            except Exception as exc:
                debug_log(f"_write_json_atomic cleanup remove failed: {type(exc).__name__}: {exc}")


def load_prefs() -> dict:
    """Load prefs, setting aside unreadable JSON or non-dict data and returning {}."""
    try:
        with open(_pref_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except OSError as exc:
        debug_log(f"load_prefs read failed: {type(exc).__name__}: {exc}")
        return {}
    except (ValueError, RecursionError) as exc:
        return _set_aside_unreadable_prefs(f"{type(exc).__name__}: {exc}")
    if isinstance(data, dict):
        return data
    return _set_aside_unreadable_prefs(f"{type(data).__name__}: expected dict")


def save_prefs(prefs: dict) -> bool:
    return _write_json_atomic(_pref_path(), prefs)


def _win32():
    u = ctypes.windll.user32
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, wintypes.UINT]
    return u


def _own_window_handle(title):
    """Return this process's visible top-level window with an exact title."""
    try:
        u = ctypes.windll.user32
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        u.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
        u.EnumWindows.restype = wintypes.BOOL
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        u.GetWindowTextLengthW.restype = ctypes.c_int
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetWindowTextW.restype = ctypes.c_int
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.IsWindowVisible.restype = wintypes.BOOL

        own_pid = os.getpid()
        found = {"hwnd": None}

        def _callback(hwnd, lparam):
            if not u.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value != own_pid:
                return True
            length = u.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            u.GetWindowTextW(hwnd, buf, length + 1)
            if buf.value != title:
                return True
            found["hwnd"] = hwnd
            return False

        proc = WNDENUMPROC(_callback)
        u.EnumWindows(proc, 0)
        return found["hwnd"]
    except Exception:
        return None


def _save_geometry(win) -> None:
    if IS_SECOND_INSTANCE:
        return
    try:
        u = _win32()
        hwnd = _own_window_handle(win.title)
        if not hwnd:
            return
        r = wintypes.RECT()
        if not u.GetWindowRect(hwnd, ctypes.byref(r)):
            return
        x, y, w, h = r.left, r.top, r.right - r.left, r.bottom - r.top
        if x <= -30000 or y <= -30000:   # minimized sentinel, not a real spot
            return
        if w <= 0 or h <= 0:
            return
        prefs = load_prefs()
        prefs["window"] = {"x": x, "y": y, "width": w, "height": h}
        save_prefs(prefs)
    except Exception:
        pass


def _restore_geometry(win) -> None:
    if IS_SECOND_INSTANCE:
        return
    try:
        geo = load_prefs().get("window")
        if not isinstance(geo, dict):
            return
        x, y, w, h = geo.get("x"), geo.get("y"), geo.get("width"), geo.get("height")
        for v in (x, y, w, h):
            if not isinstance(v, int) or isinstance(v, bool):
                return
        if w <= 0 or h <= 0:
            return
        # Is a point in the title bar still on a connected monitor?
        point = wintypes.POINT(x + 100, y + 30)
        user32 = ctypes.windll.user32
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HMONITOR
        if not user32.MonitorFromPoint(point, 0):   # MONITOR_DEFAULTTONULL
            return
        u = _win32()
        hwnd = _own_window_handle(win.title)
        if not hwnd:
            return
        SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
        u.SetWindowPos(hwnd, None, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE)
    except Exception:
        pass


# ----------------------------------------------------------------------------
# Optional debug log (off by default)
# ----------------------------------------------------------------------------
# One in-app toggle, off by default. When on, it writes
# Debug_Log_MMDDYYYY_HHMMSS.txt next to the exe through debug_log.DebugLog:
# 5 MiB per file, the active file plus at most three older ones, 20 MiB total.
# Pruning runs at launch, when logging starts, and at rollover, and deletes only
# files with that exact name pattern. A failed write turns logging off and the
# warning is shown in the console. Command text and user-typed labels are never
# written; _redact still hides any password-like value as a second guard.
# When off, nothing is written. Gitignore pattern: Debug_Log_*.txt.

_SECRET_RE = re.compile(
    r'((?:["\']?)[\w-]*password[\w-]*(?:["\']?)\s*(?::|=|\s)\s*)'
    r'("[^"]*"|\'[^\']*\'|\S+)',
    re.IGNORECASE,
)


def _redact(text):
    return _SECRET_RE.sub(r"\1***", str(text))


_debug_warning_api = None


def _debug_warning(message):
    if _debug_warning_api is not None:
        _debug_warning_api._handle_debug_warning(message)


_debug_logger = DebugLog(
    app_dir(), "Simple RCON Tool", redact=_redact, on_warning=_debug_warning,
)


def set_debug(enabled):
    """Turn the debug log on or off. Returns the active state."""
    _debug_logger.set_enabled(enabled)
    if _debug_logger.is_enabled():
        debug_log(f"Debug log enabled. Simple RCON Tool v{APP_VERSION}.")
    return _debug_logger.is_enabled()


def debug_log(msg):
    _debug_logger.log(msg)


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
#   protocol  "source" for the Source RCON client
#   note      shown in the editor (mod requirements, quirks, status)
#   buttons   locked command buttons rendered for every server of this game
#   sentinel  whether the client sends an empty marker packet after each command to find the reply's end
#   reply_ids_lag  whether command replies may carry the previous request's ID
#   ignore_event_packets  skips unsolicited type-1 event packets that are never command output
#
# Button fields:
#   label     button text
#   command   RCON command; "{arg}" pops an input box when clicked
#   confirm   optional confirmation message string for destructive actions
#   stops_server  marks a command that stops the server and its RCON connection
#   argmode   optional "underscore" to swap spaces for underscores in the arg
#
# Commands were verified per game. Only games with genuinely useful command
# sets are included.

GAME_CATALOG = [
    {
        "key": "ark_se", "name": "ARK: Survival Evolved", "protocol": "source",
        "sentinel": False,
        "note": "RCON needs RCONEnabled=True, RCONPort (default 27020) and "
                "ServerAdminPassword. Commands take no cheat prefix. Get Chat shows "
                "chat since the last Get Chat. The server can take several minutes to "
                "start. RCON listens on the MultiHome address when one is set, "
                "otherwise on every address. The server writes the admin password "
                "into its log.",
        "buttons": [
            {"label": "List Players", "command": "ListPlayers"},
            {"label": "Save World", "command": "SaveWorld"},
            {"label": "Get Chat", "command": "GetChat"},
            {"label": "Broadcast", "command": "Broadcast {arg}"},
            {"label": "Server Message", "command": "ServerChat {arg}"},
            {"label": "Destroy Wild Dinos", "command": "DestroyWildDinos",
             "confirm": "Destroy all wild creatures on the map now?"},
            {"label": "Kick Player", "command": "KickPlayer {arg}"},
            {"label": "Ban Player", "command": "BanPlayer {arg}",
             "confirm": "Ban this player?"},
            {"label": "Unban Player", "command": "UnbanPlayer {arg}"},
            {"label": "Shutdown", "command": "DoExit",
             "confirm": "Shut the server down now?", "stops_server": True},
        ],
    },
    {
        "key": "ark_asa", "name": "ARK: Survival Ascended", "protocol": "source",
        "sentinel": False,
        "note": "RCON needs RCONEnabled=True, RCONPort (default 27020) and "
                "ServerAdminPassword. Commands take no cheat prefix. Get Chat shows "
                "chat since the last Get Chat. For about 30 seconds after the port "
                "opens, Connect times out. RCON listens on every network address and "
                "the server cannot limit it, so keep the port closed to the internet.",
        "buttons": [
            {"label": "List Players", "command": "ListPlayers"},
            {"label": "Save World", "command": "SaveWorld"},
            {"label": "Get Chat", "command": "GetChat"},
            {"label": "Broadcast", "command": "Broadcast {arg}"},
            {"label": "Server Message", "command": "ServerChat {arg}"},
            {"label": "Destroy Wild Dinos", "command": "DestroyWildDinos",
             "confirm": "Destroy all wild creatures on the map now?"},
            {"label": "Kick Player", "command": "KickPlayer {arg}"},
            {"label": "Ban Player", "command": "BanPlayer {arg}",
             "confirm": "Ban this player?"},
            {"label": "Unban Player", "command": "UnbanPlayer {arg}"},
            {"label": "Shutdown", "command": "DoExit",
             "confirm": "Shut the server down now?", "stops_server": True},
        ],
    },
    {
        "key": "conan", "name": "Conan Exiles", "protocol": "source",
        "sentinel": False,
        "reply_ids_lag": True,
        "note": "Kick takes a player name then a reason. Ban takes a platform ID "
                "(from List Players) then a reason. Unban takes the ID. Kick and "
                "Ban work only on players who are online. For about 20 seconds "
                "after the server starts, commands fail while the map loads.",
        "buttons": [
            {"label": "List Players", "command": "listplayers"},
            {"label": "Kick", "command": "kickplayer name {arg}"},
            {"label": "Ban", "command": "banplayer platformid {arg}", "confirm": "Ban this player?"},
            {"label": "Unban", "command": "unbanplayer {arg}"},
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
             "confirm": "Force the server to stop now?", "stops_server": True},
        ],
    },
    {
        "key": "minecraft", "name": "Minecraft (Java)", "protocol": "source",
        "sentinel": False,
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
            {"label": "Stop Server", "command": "stop", "confirm": "Stop the server?",
             "stops_server": True},
        ],
    },
    {
        "key": "cs2", "name": "Counter-Strike 2", "protocol": "source",
        "sentinel": False,
        "note": "RCON stays off unless the server starts with -usercon. Set the "
                "password with rcon_password. Default port 27015, the same as the "
                "game port. Kick takes a player's name as Status shows it.",
        "buttons": [
            {"label": "Status", "command": "status"},
            {"label": "Say", "command": "say {arg}"},
            {"label": "Kick", "command": "kick {arg}"},
            {"label": "Change Level", "command": "changelevel {arg}"},
        ],
    },
    {
        "key": "pz", "name": "Project Zomboid", "protocol": "source",
        "note": ("No leading slash over RCON. Put quotes around multi-word names. RCON stays off "
                 "until RCONPassword is set in the server's .ini (port 27015 by default). "
                 "Kick needs the player online."),
        "buttons": [
            {"label": "Players", "command": "players"},
            {"label": "Save", "command": "save"},
            {"label": "Server Message", "command": 'servermsg "{arg}"'},
            {"label": "Kick User", "command": "kickuser {arg}"},
            {"label": "Ban User", "command": "banuser {arg}", "confirm": "Ban this user?"},
            {"label": "Unban User", "command": "unbanuser {arg}"},
            {"label": "Show Options", "command": "showoptions"},
            {"label": "Save & Quit", "command": "quit", "confirm": "Save and stop the server?",
             "stops_server": True},
        ],
    },
    {
        "key": "squad", "name": "Squad", "protocol": "source",
        "sentinel": False,
        "ignore_event_packets": True,
        "note": "Default RCON port 21114. RCON stays off until Password is set "
                "in ServerConfig/Rcon.cfg; it listens on every address unless IP "
                "is set there. For about 10 s after a start, commands get no "
                "reply. Kick by ID takes the player's ID from List Players, then "
                "a reason (for example: 12 Breaking rules). A mistyped command "
                "gets no reply.",
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
        "note": "Default port 27015, the same as the game port. Set the RCON "
                "password with rcon_password. Kick takes a player's name as "
                "Status shows it.",
        "buttons": [
            {"label": "Status", "command": "status"},
            {"label": "Say", "command": "say {arg}"},
            {"label": "Kick", "command": "kick {arg}"},
            {"label": "Change Level", "command": "changelevel {arg}"},
        ],
    },
    {
        "key": "eco", "name": "Eco", "protocol": "source",
        "sentinel": False,
        "note": "Default port 3002. RCON stays off until RconPassword is set in "
                "Configs/Network.eco. Kick/Ban use a Name,Reason format; banning "
                "a player who never joined needs their Steam or SLG ID. Very "
                "long replies (such as the full help list) make Eco drop the "
                "connection.",
        "buttons": [
            {"label": "Online Players", "command": "manage players"},
            {"label": "Save", "command": "manage save"},
            {"label": "Kick", "command": "kick {arg}"},
            {"label": "Ban", "command": "ban {arg}",
             "confirm": "Ban this player? (format: Name,Reason)"},
            {"label": "Ban List", "command": "ban"},
            {"label": "Unban", "command": "unban {arg}"},
        ],
    },
    {
        "key": "valheim", "name": "Valheim", "protocol": "source",
        "sentinel": False,
        "note": "Requires the ValheimRcon mod. Crossplay must be off "
                "(BepInEx mods do not load with crossplay on).",
        "buttons": [
            {"label": "Save", "command": "save"},
            {"label": "Kick", "command": "kick {arg}"},
            {"label": "Ban", "command": "ban {arg}", "confirm": "Ban this player?"},
            {"label": "Unban", "command": "unban {arg}"},
            {"label": "List Bans", "command": "banlist"},
        ],
    },
]

CATALOG_BY_KEY = {g["key"]: g for g in GAME_CATALOG}


def game_uses_sentinel(game_key):
    g = CATALOG_BY_KEY.get(game_key)
    return g.get("sentinel", True) if g else True


def game_reply_ids_lag(game_key):
    g = CATALOG_BY_KEY.get(game_key)
    return g.get("reply_ids_lag", False) if g else False


def game_ignores_event_packets(game_key):
    g = CATALOG_BY_KEY.get(game_key)
    return g.get("ignore_event_packets", False) if g else False


# ----------------------------------------------------------------------------
# Source RCON client (standard library only)
# ----------------------------------------------------------------------------

RCON_REPLY_IDLE_GAP = 0.3


class RconError(Exception):
    pass


class RconConnectionClosed(RconError):
    pass


def is_stop_command(game_key, command):
    # Matches on command text, so a typed stop command counts the same as the button.
    game = CATALOG_BY_KEY.get(game_key)
    if not game:
        return False
    command = command.strip().casefold()
    return any(
        button.get("stops_server") and button["command"].strip().casefold() == command
        for button in game["buttons"]
    )


def stop_outcome(reply, error):
    if isinstance(error, (RconConnectionClosed, ConnectionResetError, ConnectionAbortedError)):
        return "expected_disconnect"
    if error is not None:
        return "failed"
    if reply is None:
        return "no_reply"
    return "replied"


class _RconIdleTimeout(Exception):
    pass


class RconClient:
    SERVERDATA_EVENT = 1
    SERVERDATA_AUTH = 3
    SERVERDATA_AUTH_RESPONSE = 2
    SERVERDATA_EXECCOMMAND = 2
    SERVERDATA_RESPONSE_VALUE = 0
    # 9 is ID + type + one null terminator; 65536 is far above Minecraft's 4096-byte replies and stops absurd waits.
    MIN_PACKET_LENGTH = 9
    MAX_PACKET_LENGTH = 65536

    def __init__(
        self, host, port, password, timeout=8, *, sentinel=True, reply_ids_lag=False,
        ignore_event_packets=False,
    ):
        self.host = host
        self.port = int(port)
        self.password = password  # memory only
        self.timeout = timeout
        self.sentinel = sentinel
        self.reply_ids_lag = reply_ids_lag
        self.ignore_event_packets = ignore_event_packets
        self.sock = None
        self._id = 0
        self._last_sent_id = None
        self._sentinel_ids = set()

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
        self._last_sent_id = req_id
        return req_id

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise RconConnectionClosed("Connection closed by server.")
            buf += chunk
        return buf

    def _recv_packet(self):
        (length,) = struct.unpack("<i", self._recv_exact(4))
        return self._read_packet_body(length)

    def _read_packet_body(self, length):
        if not self.MIN_PACKET_LENGTH <= length <= self.MAX_PACKET_LENGTH:
            raise RconError("The server sent an invalid packet.")
        data = self._recv_exact(length)
        req_id, type_ = struct.unpack("<ii", data[:8])
        tail = data[8:]
        if tail.endswith(b"\x00\x00"):
            body = tail[:-2]
        elif tail.endswith(b"\x00"):
            body = tail[:-1]
        else:
            raise RconError("The server sent an invalid packet.")
        return req_id, type_, body.decode("utf-8", errors="replace")

    def _recv_command_packet(self):
        header = self.sock.recv(4)
        if not header:
            raise RconConnectionClosed("Connection closed by server.")
        return self._recv_packet_from_header(header)

    def _recv_packet_from_header(self, header):
        try:
            header += self._recv_exact(4 - len(header))
            (length,) = struct.unpack("<i", header)
            return self._read_packet_body(length)
        except socket.timeout as exc:
            raise RconError("Timed out while receiving packet.") from exc

    def _recv_packet_after_idle_gap(self, idle_timeout, normal_timeout):
        self.sock.settimeout(idle_timeout)
        try:
            try:
                header = self.sock.recv(4)
            except socket.timeout as exc:
                raise _RconIdleTimeout from exc
        finally:
            self.sock.settimeout(normal_timeout)
        if not header:
            raise RconConnectionClosed("Connection closed by server.")
        return self._recv_packet_from_header(header)

    def _authenticate(self):
        self._send(self.SERVERDATA_AUTH, self.password)
        try:
            while True:
                req_id, type_, _ = self._recv_packet()
                if type_ == self.SERVERDATA_AUTH_RESPONSE:
                    if req_id == -1:
                        raise RconError("Authentication failed (wrong RCON password).")
                    return
                # ignore the mirrored empty packet some servers send first
        except RconConnectionClosed as exc:
            raise RconError(
                "The server closed the connection during login. The RCON password may be wrong."
            ) from exc

    def command(self, cmd):
        previous_id = self._last_sent_id
        command_id = self._send(self.SERVERDATA_EXECCOMMAND, cmd)
        parts = []
        received_reply = False
        normal_timeout = self.sock.gettimeout()
        try:
            if self.sentinel:
                sentinel_id = self._send(self.SERVERDATA_RESPONSE_VALUE, "")
                self._sentinel_ids.add(sentinel_id)
                try:
                    while True:
                        req_id, type_, body = self._recv_command_packet()
                        if self.ignore_event_packets and type_ == self.SERVERDATA_EVENT:
                            continue
                        if req_id == sentinel_id:
                            break
                        if req_id in self._sentinel_ids:
                            continue
                        # Any other id counts as reply text: not every server
                        # echoes the command's id.
                        received_reply = True
                        parts.append(body)
                except socket.timeout:
                    if not received_reply:
                        return None
            else:
                accepted_ids = {command_id}
                if self.reply_ids_lag and previous_id is not None:
                    accepted_ids.add(previous_id)
                try:
                    while True:
                        req_id, type_, body = self._recv_command_packet()
                        if self.ignore_event_packets and type_ == self.SERVERDATA_EVENT:
                            continue
                        if req_id in accepted_ids:
                            received_reply = True
                            parts.append(body)
                            break
                except socket.timeout:
                    return "".join(parts).strip() if received_reply else None

                idle_deadline = time.monotonic() + RCON_REPLY_IDLE_GAP
                while True:
                    idle_timeout = idle_deadline - time.monotonic()
                    if idle_timeout <= 0:
                        break
                    try:
                        req_id, type_, body = self._recv_packet_after_idle_gap(
                            idle_timeout, normal_timeout
                        )
                    except _RconIdleTimeout:
                        break
                    if self.ignore_event_packets and type_ == self.SERVERDATA_EVENT:
                        continue
                    if req_id in accepted_ids:
                        parts.append(body)
                        idle_deadline = time.monotonic() + RCON_REPLY_IDLE_GAP
        finally:
            self.sock.settimeout(normal_timeout)
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
        self.client = RconClient(
            server["host"], server["port"], password,
            sentinel=game_uses_sentinel(server.get("game")),
            reply_ids_lag=game_reply_ids_lag(server.get("game")),
            ignore_event_packets=game_ignores_event_packets(server.get("game")),
        )
        self.lock = threading.Lock()  # one command at a time per socket

    def connect(self):
        try:
            self.client.connect()
        except Exception:
            self.close()
            raise

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


_SERVER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _validate_server(record, *, require_catalog_game):
    """Return a cleaned saved-server record, or a plain-language error."""
    if not isinstance(record, dict):
        return "Server details are invalid."

    cleaned = {}
    server_id = record.get("id")
    if require_catalog_game and server_id in (None, ""):
        pass
    elif not isinstance(server_id, str) or not _SERVER_ID_RE.fullmatch(server_id):
        return "Server ID is invalid."
    else:
        cleaned["id"] = server_id

    for field, label in (("name", "Name"), ("host", "Host")):
        value = record.get(field)
        if not isinstance(value, str) or not value.strip():
            return f"{label} is required."
        cleaned[field] = value.strip()

    port = record.get("port")
    if require_catalog_game and isinstance(port, str):
        try:
            port = int(port)
        except ValueError:
            return "Port must be a number from 1 to 65535."
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        return "Port must be a number from 1 to 65535."
    cleaned["port"] = port

    game = record.get("game")
    if not isinstance(game, str) or not game:
        return "Game is required."
    if require_catalog_game and game not in CATALOG_BY_KEY:
        return "Pick a game from the list."
    cleaned["game"] = game

    buttons = record.get("buttons", [])
    if not isinstance(buttons, list):
        return "Buttons must be a list."
    cleaned_buttons = []
    for button in buttons:
        if not isinstance(button, dict):
            return "Each button must include a label and command."
        label = button.get("label")
        command = button.get("command")
        confirm = button.get("confirm", False)
        if not isinstance(label, str) or not label.strip():
            return "Button label is required."
        if not isinstance(command, str) or not command.strip():
            return "Button command is required."
        if not isinstance(confirm, bool):
            return "Button confirmation must be true or false."
        cleaned_buttons.append({
            "label": label.strip(),
            "command": command.strip(),
            "confirm": confirm,
        })
    cleaned["buttons"] = cleaned_buttons
    return cleaned


def _backup_path(path):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = f"{path}.bad-{stamp}"
    candidate = base
    suffix = 2
    while os.path.exists(candidate):
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


def _set_server_store_warning(message):
    global _server_store_warning
    _server_store_warning = message


def _take_server_store_warning():
    global _server_store_warning
    warning = _server_store_warning
    _server_store_warning = None
    return warning


def _new_server_id():
    """A random server ID, so a deleted entry's ID is never handed out again."""
    return "srv-" + uuid.uuid4().hex


def _with_warning(result, warning):
    """Attach a pending storage warning to an API result, if there is one."""
    if warning:
        result["warning"] = warning
    return result


def _set_aside_unreadable_servers():
    """Rename an unusable servers file aside and start with an empty list."""
    backup_path = _backup_path(SERVERS_FILE)
    try:
        os.rename(SERVERS_FILE, backup_path)
    except OSError as exc:
        debug_log(f"load_servers backup rename failed: {type(exc).__name__}: {exc}")
        raise ServerStoreError("Your saved server list could not be recovered. It was left unchanged.") from exc
    backup_name = os.path.basename(backup_path)
    _set_server_store_warning(
        f"Your saved server list could not be read, so it was set aside as {backup_name} "
        "and the list starts empty."
    )
    return []


def _set_aside_unreadable_prefs(reason):
    backup_path = _backup_path(_pref_path())
    try:
        os.rename(_pref_path(), backup_path)
    except OSError as exc:
        debug_log(f"load_prefs unreadable data left in place: {reason}")
        debug_log(f"load_prefs backup rename failed: {type(exc).__name__}: {exc}")
    else:
        debug_log(f"load_prefs set aside unreadable data: {reason}")
    return {}


def load_servers():
    try:
        with open(SERVERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    except OSError as exc:
        debug_log(f"load_servers read failed: {type(exc).__name__}: {exc}")
        raise ServerStoreError("Your saved server list could not be read. It was left unchanged.") from exc
    except (ValueError, RecursionError) as exc:
        # JSONDecodeError and UnicodeDecodeError are both ValueError subclasses.
        debug_log(f"load_servers decode failed: {type(exc).__name__}: {exc}")
        return _set_aside_unreadable_servers()

    if not isinstance(data, list):
        debug_log(f"load_servers root invalid: expected list, got {type(data).__name__}")
        return _set_aside_unreadable_servers()

    servers = []
    seen_ids = set()
    skipped = 0
    repaired_ids = 0
    for index, record in enumerate(data):
        cleaned = _validate_server(record, require_catalog_game=False)
        if isinstance(cleaned, str):
            skipped += 1
            debug_log(f"load_servers record {index} skipped: {cleaned}")
            continue
        if cleaned["id"] in seen_ids:
            repaired_ids += 1
            cleaned["id"] = _new_server_id()
            debug_log(f"load_servers record {index} had a duplicate ID and was repaired")
        seen_ids.add(cleaned["id"])
        servers.append(cleaned)

    if not skipped and not repaired_ids:
        return servers

    backup_path = _backup_path(SERVERS_FILE)
    try:
        shutil.copy2(SERVERS_FILE, backup_path)
    except OSError as exc:
        debug_log(f"load_servers backup copy failed: {type(exc).__name__}: {exc}")
        raise ServerStoreError("Your saved server list could not be preserved. It was left unchanged.") from exc

    backup_name = os.path.basename(backup_path)
    cleanup_saved = save_servers(servers)
    details = []
    if skipped == 1:
        details.append("1 saved server could not be read and was skipped.")
    elif skipped:
        details.append(f"{skipped} saved servers could not be read and were skipped.")
    if repaired_ids == 1:
        details.append("1 saved server shared an ID with another and was given a new one.")
    elif repaired_ids:
        details.append(
            f"{repaired_ids} saved servers shared an ID with another and were given new ones."
        )
    details.append(f"The original file was kept as {backup_name}.")
    warning = " ".join(details)
    if not cleanup_saved:
        debug_log("load_servers cleanup write failed")
        warning += " The cleanup could not be saved."
    _set_server_store_warning(warning)
    return servers


def save_servers(servers):
    return _write_json_atomic(SERVERS_FILE, servers, indent=2)


def _update_error_reason(exc: BaseException) -> str:
    """Turn a check_update exception into a short, plain-language reason to
    show in the UI. Pure and network-free: takes the already-raised exception,
    never touches the network itself.

    Each branch is specific to a failure that can actually cause it, and
    names a next step where there is a sensible one. Subclasses are checked
    before their parents: SSLCertVerificationError and SSLEOFError/
    SSLZeroReturnError before the generic ssl.SSLError, and the specific
    ConnectionError subclasses and socket.gaierror before the generic OSError
    branch (socket.timeout is an alias of TimeoutError, and both are OSError
    subclasses)."""
    # HTTPError is a URLError subclass but carries its own .code, so classify
    # it before unwrapping anything.
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 403:
            return (
                "GitHub is rate-limiting update checks from this network. "
                "Try again later."
            )
        if exc.code == 404:
            return "No published release was found."
        if 500 <= exc.code < 600:
            return f"GitHub is having trouble on its end (HTTP {exc.code})."
        return f"GitHub returned an error (HTTP {exc.code})."

    if isinstance(exc, json.JSONDecodeError):
        return (
            "GitHub returned something unexpected. This often means a proxy "
            "or a guest wifi sign-in page answered instead."
        )

    # A plain URLError wraps the underlying cause (ssl.SSLError, socket.timeout,
    # a DNS/socket OSError, ...) in its .reason; unwrap it to classify the
    # actual cause, but remember it came from a URLError for the fallback below.
    is_url_error = isinstance(exc, urllib.error.URLError)
    cause = exc.reason if is_url_error and exc.reason is not None else exc

    if isinstance(cause, ssl.SSLCertVerificationError):
        return (
            "GitHub's certificate could not be verified. This usually means "
            "antivirus or a network filter is inspecting HTTPS traffic."
        )
    if isinstance(cause, (ssl.SSLEOFError, ssl.SSLZeroReturnError)):
        return "The secure connection was cut off during the handshake with GitHub."
    if isinstance(cause, ssl.SSLError):
        return "The secure connection to GitHub failed."
    if isinstance(cause, socket.gaierror):
        return (
            "The address for api.github.com could not be looked up. Check "
            "DNS or the internet connection."
        )
    if isinstance(cause, (socket.timeout, TimeoutError)):
        return "GitHub didn't respond in time."
    if isinstance(cause, (ConnectionRefusedError, ConnectionResetError)):
        return (
            "The connection was refused or reset. A firewall or proxy may "
            "be blocking it."
        )
    if isinstance(cause, OSError) and getattr(cause, "errno", None) == errno.ENETUNREACH:
        return "No network connection."
    if is_url_error:
        return "Couldn't reach GitHub. Check the internet connection."

    return "Something unexpected went wrong while checking for updates."


# ----------------------------------------------------------------------------
# API exposed to the JavaScript frontend
# ----------------------------------------------------------------------------

class Api:
    def __init__(self):
        global _debug_warning_api
        self._sessions = {}  # server_id -> Session
        self._sessions_lock = threading.Lock()
        self._connect_attempts = {}  # server_id -> current connection attempt token
        self._window = None
        self._frontend_ready = False
        self._debug_warnings = []
        self._debug_warnings_lock = threading.Lock()
        _debug_warning_api = self

    # ---- frontend helpers -------------------------------------------------

    def _set_window(self, window):
        self._window = window

    def _mark_frontend_ready(self):
        self._frontend_ready = True
        with self._debug_warnings_lock:
            warnings = self._debug_warnings
            self._debug_warnings = []
        for message in warnings:
            self._emit("debugWarning", {
                "text": message,
                "debug": _debug_logger.is_enabled(),
            })

    def _handle_debug_warning(self, message):
        with self._debug_warnings_lock:
            if not self._frontend_ready or not self._window:
                self._debug_warnings.append(message)
                return
        self._emit("debugWarning", {
            "text": message,
            "debug": _debug_logger.is_enabled(),
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
        if not _is_allowed_url(url):
            debug_log("open_url refused an address outside the allowed site")
            return {"ok": False, "error": "URL is not allowed."}
        try:
            if webbrowser.open(url):
                return {"ok": True}
        except Exception as e:
            debug_log(f"Could not open project URL: {e}")
            return {"ok": False, "error": "Could not open the browser."}
        return {"ok": False, "error": "Could not open the browser."}

    # ---- config -----------------------------------------------------------

    def get_config(self):
        try:
            servers = load_servers()
            warning = _take_server_store_warning()
        except ServerStoreError as exc:
            servers = []
            warning = str(exc)
        with self._debug_warnings_lock:
            debug_warnings = self._debug_warnings
            self._debug_warnings = []
        return {
            "servers": servers,
            "catalog": GAME_CATALOG,
            "version": APP_VERSION,
            "debug": _debug_logger.is_enabled(),
            "storeWarning": warning,
            "debugWarnings": debug_warnings,
        }

    # ---- theme persistence --------------------------------------------------

    def get_theme(self):
        theme = load_prefs().get("theme")
        return theme if theme in ("dark", "light") else "dark"

    def save_theme(self, theme):
        if theme not in ("dark", "light"):
            return {"ok": False}
        prefs = load_prefs()
        prefs["theme"] = theme
        if save_prefs(prefs):
            return {"ok": True}
        debug_log("save_theme write failed")
        return {
            "ok": False,
            "error": "Couldn't save the theme choice. It will stay until you close the app.",
        }

    # ---- debug log toggle -------------------------------------------------

    def set_debug(self, enabled):
        return {"ok": True, "debug": set_debug(enabled)}

    # ---- update check -----------------------------------------------------

    def check_update(self):
        """Compare the latest published release to APP_VERSION. Quiet in the UI
        on failure (see _update_error_reason), but always logged when debug is
        on."""
        result = {"ok": True, "current": APP_VERSION, "version": None,
                  "update": False, "offline": False}
        try:
            url = (f"https://api.github.com/repos/{GITHUB_OWNER}/"
                   f"{GITHUB_REPO}/releases/latest")
            req = urllib.request.Request(url, headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "Simple-RCON-Tool",
            })
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode("utf-8"))
            tag = (data.get("tag_name") or "").lstrip("v")
            result["version"] = tag
            if tag and _version_gt(tag, APP_VERSION):
                result["update"] = True
                result["url"] = (data.get("html_url") or
                                  f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases")
            debug_log(f"check_update: found v{tag}, current v{APP_VERSION}")
        except Exception as e:
            result["offline"] = True
            result["reason"] = _update_error_reason(e)
            debug_log(f"check_update failed: {type(e).__name__}: {e}")
        return result

    # ---- server CRUD ------------------------------------------------------

    def save_server(self, server):
        cleaned = _validate_server(server, require_catalog_game=True)
        if isinstance(cleaned, str):
            return {"ok": False, "error": cleaned}
        server = cleaned
        try:
            servers = load_servers()
        except ServerStoreError as exc:
            return {"ok": False, "error": str(exc)}
        warning = _take_server_store_warning()
        if server.get("id"):
            for i, s in enumerate(servers):
                if s["id"] == server["id"]:
                    servers[i] = server
                    break
            else:
                servers.append(server)
        else:
            server["id"] = _new_server_id()
            servers.append(server)
        if not save_servers(servers):
            debug_log(f"save_server write failed: {server['id']}")
            return _with_warning({
                "ok": False,
                "error": "The server could not be saved. Your saved list was not changed.",
            }, warning)
        return _with_warning({"ok": True, "servers": servers}, warning)

    def delete_server(self, server_id):
        try:
            servers = [s for s in load_servers() if s["id"] != server_id]
        except ServerStoreError as exc:
            return {"ok": False, "error": str(exc)}
        warning = _take_server_store_warning()
        if not save_servers(servers):
            debug_log(f"delete_server write failed: {server_id}")
            return _with_warning({
                "ok": False,
                "error": "The server could not be deleted. Your saved list was not changed.",
            }, warning)
        self.disconnect(server_id)
        return _with_warning({"ok": True, "servers": servers}, warning)

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
            server = next((s for s in load_servers() if s["id"] == server_id), None)
        except ServerStoreError as exc:
            self._finish_connect_attempt(server_id, attempt)
            return {"ok": False, "error": str(exc)}
        warning = _take_server_store_warning()
        if not server:
            self._finish_connect_attempt(server_id, attempt)
            return _with_warning({"ok": False, "error": "Server not found."}, warning)

        if server.get("game") not in CATALOG_BY_KEY:
            self._finish_connect_attempt(server_id, attempt)
            return _with_warning({"ok": False, "error":
                                  "This server's game is no longer supported. Edit the server to "
                                  "pick another game, or delete it."}, warning)

        if not password:
            self._finish_connect_attempt(server_id, attempt)
            return _with_warning({"ok": False, "error": "RCON password is required."}, warning)

        with self._sessions_lock:
            if self._connect_attempts.get(server_id) is not attempt:
                cancelled = True
                old_session = None
            else:
                cancelled = False
                old_session = self._sessions.pop(server_id, None)
        if cancelled:
            return _with_warning({"ok": False, "error": "Connection was cancelled."}, warning)
        if old_session:
            old_session.close()
            self._log(server_id, "Disconnected. Password cleared from memory.", "muted")
            self._emit("status", {"serverId": server_id, "state": "idle"})

        sess = Session(server, password)
        try:
            sess.connect()
        except Exception as e:
            self._finish_connect_attempt(server_id, attempt)
            return _with_warning({"ok": False, "error": friendly_error(e, "connect")}, warning)

        with self._sessions_lock:
            if self._connect_attempts.get(server_id) is attempt:
                self._sessions[server_id] = sess
                self._connect_attempts.pop(server_id, None)
                installed = True
            else:
                installed = False
        if not installed:
            sess.close()
            return _with_warning({"ok": False, "error": "Connection was cancelled."}, warning)
        self._log(server_id, f"Connected to {server['host']}:{server['port']}.", "ok")
        debug_log(f"Connected: {server.get('game')} {server['host']}:{server['port']}")
        return _with_warning({"ok": True}, warning)

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
            debug_log(f"Command skipped for replaced or closed session: {server_id}")
            return
        self._emit("status", {"serverId": server_id, "state": "running"})
        self._log(server_id, f"$ {label}", "cmd")
        game_key = getattr(sess, "server", {}).get("game")
        game = CATALOG_BY_KEY.get(game_key)
        if game and any(button["label"] == label for button in game["buttons"]):
            debug_log(f'send: quick button "{label}" (server {server_id})')
        else:
            debug_log(f"send: custom command (server {server_id}, {len(command)} characters)")

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
            debug_log(f"Command outcome from replaced or closed session: {server_id}")
            return

        if is_stop_command(game_key, command):
            outcome = stop_outcome(resp, error)
            if outcome == "expected_disconnect":
                debug_log(f"command: {type(error).__name__}: {error}")
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

        self._log(server_id, friendly_error(error, "command"), "err")
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
                path = os.path.join(app_dir(), fname)
                try:
                    with open(path, "x", encoding="utf-8") as f:
                        f.write(text or "")
                    return {"ok": True, "path": path, "name": fname}
                except FileExistsError:
                    if suffix == 99:
                        raise
        except Exception as e:
            debug_log(f"Could not export console: {type(e).__name__}: {e}")
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
    _debug_logger.prune()
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
    api._set_window(window)

    window.events.shown += lambda: None if IS_SECOND_INSTANCE else _restore_geometry(window)

    def _on_closing():
        if not IS_SECOND_INSTANCE:
            _save_geometry(window)
        return True
    window.events.closing += _on_closing

    def on_loaded():
        api._mark_frontend_ready()

    try:
        window.events.loaded += on_loaded
    except Exception:
        api._mark_frontend_ready()

    # Qt backend (PySide6 + WebEngine). Pass the PNG so the live taskbar icon
    # is ours; guarded so an older pywebview without the icon arg still launches.
    try:
        webview.start(gui="qt", icon=resource_path("simple_rcon_tool.png"))
    except TypeError:
        webview.start(gui="qt")


if __name__ == "__main__":
    main()
