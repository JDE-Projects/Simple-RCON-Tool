import json
import os
import re
import shutil
import uuid

from app import files, paths
from app.services import catalog, diagnostics


_server_store_warning = None


class ServerStoreError(Exception):
    """A plain-language error reading or preserving saved server data."""


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
    if require_catalog_game and game not in catalog.CATALOG_BY_KEY:
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
    backup_path = files._backup_path(paths.SERVERS_FILE)
    try:
        os.rename(paths.SERVERS_FILE, backup_path)
    except OSError as exc:
        diagnostics.debug_log(f"load_servers backup rename failed: {type(exc).__name__}: {exc}")
        raise ServerStoreError("Your saved server list could not be recovered. It was left unchanged.") from exc
    backup_name = os.path.basename(backup_path)
    _set_server_store_warning(
        f"Your saved server list could not be read, so it was set aside as {backup_name} "
        "and the list starts empty."
    )
    return []


def load_servers():
    try:
        with open(paths.SERVERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    except OSError as exc:
        diagnostics.debug_log(f"load_servers read failed: {type(exc).__name__}: {exc}")
        raise ServerStoreError("Your saved server list could not be read. It was left unchanged.") from exc
    except (ValueError, RecursionError) as exc:
        # JSONDecodeError and UnicodeDecodeError are both ValueError subclasses.
        diagnostics.debug_log(f"load_servers decode failed: {type(exc).__name__}: {exc}")
        return _set_aside_unreadable_servers()

    if not isinstance(data, list):
        diagnostics.debug_log(f"load_servers root invalid: expected list, got {type(data).__name__}")
        return _set_aside_unreadable_servers()

    servers = []
    seen_ids = set()
    skipped = 0
    repaired_ids = 0
    for index, record in enumerate(data):
        cleaned = _validate_server(record, require_catalog_game=False)
        if isinstance(cleaned, str):
            skipped += 1
            diagnostics.debug_log(f"load_servers record {index} skipped: {cleaned}")
            continue
        if cleaned["id"] in seen_ids:
            repaired_ids += 1
            cleaned["id"] = _new_server_id()
            diagnostics.debug_log(f"load_servers record {index} had a duplicate ID and was repaired")
        seen_ids.add(cleaned["id"])
        servers.append(cleaned)

    if not skipped and not repaired_ids:
        return servers

    backup_path = files._backup_path(paths.SERVERS_FILE)
    try:
        shutil.copy2(paths.SERVERS_FILE, backup_path)
    except OSError as exc:
        diagnostics.debug_log(f"load_servers backup copy failed: {type(exc).__name__}: {exc}")
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
        diagnostics.debug_log("load_servers cleanup write failed")
        warning += " The cleanup could not be saved."
    _set_server_store_warning(warning)
    return servers


def save_servers(servers):
    return files._write_json_atomic(paths.SERVERS_FILE, servers, indent=2)


def save_server(api, server):
    cleaned = _validate_server(server, require_catalog_game=True)
    if isinstance(cleaned, str):
        return {"ok": False, "error": cleaned}
    server = cleaned
    try:
        saved_servers = load_servers()
    except ServerStoreError as exc:
        return {"ok": False, "error": str(exc)}
    warning = _take_server_store_warning()
    if server.get("id"):
        for i, s in enumerate(saved_servers):
            if s["id"] == server["id"]:
                saved_servers[i] = server
                break
        else:
            saved_servers.append(server)
    else:
        server["id"] = _new_server_id()
        saved_servers.append(server)
    if not save_servers(saved_servers):
        diagnostics.debug_log(f"save_server write failed: {server['id']}")
        return _with_warning({
            "ok": False,
            "error": "The server could not be saved. Your saved list was not changed.",
        }, warning)
    return _with_warning({"ok": True, "servers": saved_servers}, warning)


def delete_server(api, server_id):
    try:
        saved_servers = [s for s in load_servers() if s["id"] != server_id]
    except ServerStoreError as exc:
        return {"ok": False, "error": str(exc)}
    warning = _take_server_store_warning()
    if not save_servers(saved_servers):
        diagnostics.debug_log(f"delete_server write failed: {server_id}")
        return _with_warning({
            "ok": False,
            "error": "The server could not be deleted. Your saved list was not changed.",
        }, warning)
    api.disconnect(server_id)
    return _with_warning({"ok": True, "servers": saved_servers}, warning)
