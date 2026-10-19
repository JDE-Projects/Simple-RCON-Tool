import re
import socket

from app import paths
from app.debug_log import DebugLog
from app.services import rcon


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
    paths.app_dir(), "Simple RCON Tool", redact=_redact, on_warning=_debug_warning,
)


def set_debug(enabled, version):
    """Turn the debug log on or off. Returns the active state."""
    _debug_logger.set_enabled(enabled)
    if _debug_logger.is_enabled():
        debug_log(f"Debug log enabled. Simple RCON Tool v{version}.")
    return _debug_logger.is_enabled()


def debug_log(msg):
    _debug_logger.log(msg)


def set_warning_bridge(api):
    global _debug_warning_api
    _debug_warning_api = api


# ----------------------------------------------------------------------------
# Plain-language errors
# ----------------------------------------------------------------------------
# Never surface a raw [Errno N] or exception text to the user. Map to a friendly
# message and send the full detail to the debug log.

def friendly_error(exc, context=""):
    debug_log(f"{context}: {type(exc).__name__}: {exc}")
    if isinstance(exc, rcon.RconError):
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
