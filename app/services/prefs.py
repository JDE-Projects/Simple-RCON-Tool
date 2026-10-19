import json
import os

from app import files, paths
from app.services import catalog, diagnostics, servers


# ----------------------------------------------------------------------------
# Local prefs store. One JSON file next to the app holds EVERY persisted
# setting: theme, window geometry, and anything added later. Always read-
# merge-write through load_prefs / save_prefs. Never overwrite the file with
# a single key, or the next setting you add silently wipes the others.
# ----------------------------------------------------------------------------

def load_prefs() -> dict:
    """Load prefs, setting aside unreadable JSON or non-dict data and returning {}."""
    try:
        with open(paths._pref_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except OSError as exc:
        diagnostics.debug_log(f"load_prefs read failed: {type(exc).__name__}: {exc}")
        return {}
    except (ValueError, RecursionError) as exc:
        return _set_aside_unreadable_prefs(f"{type(exc).__name__}: {exc}")
    if isinstance(data, dict):
        return data
    return _set_aside_unreadable_prefs(f"{type(data).__name__}: expected dict")


def save_prefs(prefs: dict) -> bool:
    return files._write_json_atomic(paths._pref_path(), prefs)


def _set_aside_unreadable_prefs(reason):
    backup_path = files._backup_path(paths._pref_path())
    try:
        os.rename(paths._pref_path(), backup_path)
    except OSError as exc:
        diagnostics.debug_log(f"load_prefs unreadable data left in place: {reason}")
        diagnostics.debug_log(f"load_prefs backup rename failed: {type(exc).__name__}: {exc}")
    else:
        diagnostics.debug_log(f"load_prefs set aside unreadable data: {reason}")
    return {}


def get_config(api):
    try:
        saved_servers = servers.load_servers()
        warning = servers._take_server_store_warning()
    except servers.ServerStoreError as exc:
        saved_servers = []
        warning = str(exc)
    with api._debug_warnings_lock:
        debug_warnings = api._debug_warnings
        api._debug_warnings = []
    return {
        "servers": saved_servers,
        "catalog": catalog.GAME_CATALOG,
        "version": api._version,
        "debug": diagnostics._debug_logger.is_enabled(),
        "storeWarning": warning,
        "debugWarnings": debug_warnings,
    }


def get_theme(api):
    theme = load_prefs().get("theme")
    return theme if theme in ("dark", "light") else "dark"


def save_theme(api, theme):
    if theme not in ("dark", "light"):
        return {"ok": False}
    saved_prefs = load_prefs()
    saved_prefs["theme"] = theme
    if save_prefs(saved_prefs):
        return {"ok": True}
    diagnostics.debug_log("save_theme write failed")
    return {
        "ok": False,
        "error": "Couldn't save the theme choice. It will stay until you close the app.",
    }
