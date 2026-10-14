"""Eco live-test definition."""

from pathlib import Path
import json
import re

ECO_DIR = ("servers", "eco")
# Evidence paths are relative to the run folder, which sits in <root>/runs/.
USERS_CONFIG = "../../servers/eco/Configs/Users.eco"


def _server_dir(root: Path) -> Path:
    return root.joinpath(*ECO_DIR)


def _write_config(configs: Path, name: str, changes: dict) -> None:
    """Write Configs/<name>.eco as the shipped template with only the given keys changed."""
    settings = json.loads((configs / f"{name}.eco.template").read_text(encoding="utf-8-sig"))
    settings.update(changes)
    (configs / f"{name}.eco").write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def configure(root: Path, run_dir: Path, definition: dict, password: str) -> None:
    configs = _server_dir(root) / "Configs"
    # RCON defaults to 0.0.0.0 with UPnP on; pin both to this machine and keep it off the public list.
    _write_config(configs, "Network", {
        "PublicServer": False, "IPAddress": "127.0.0.1", "RconIPAddress": "127.0.0.1",
        "RconServerPort": definition["rcon_port"], "RconPassword": password, "UPnPEnabled": False,
    })
    # A throwaway world per run: the save folder lives inside the run folder.
    _write_config(configs, "Storage", {"StorageDirectory": str(run_dir / "storage")})
    # Eco reads reviewed user textures from the install folder's Storage whatever
    # StorageDirectory says, and stops with an error if the folder is missing.
    (_server_dir(root) / "Storage" / "UserTextures" / "Reviewed").mkdir(parents=True, exist_ok=True)
    _write_config(configs, "Backup", {"Enabled": False})
    # A fresh user list each run, so the ban check starts from an empty ban list.
    _write_config(configs, "Users", {})


def server_version(root: Path) -> str:
    manifest = _server_dir(root) / f"steamapps/appmanifest_{DEFINITION['appid']}.acf"
    if not manifest.exists():
        return "unknown"
    match = re.search(r'"buildid"\s+"([^"]+)"', manifest.read_text(encoding="utf-8", errors="replace"))
    return f"build {match.group(1)}" if match else "unknown"


DEFINITION = {
    "key": "eco", "name": "Eco", "catalog_key": "eco", "appid": 739590,
    "rcon_port": 3002, "ports": [3000, 3001, 3002, 3003], "loopback_supported": True,
    "timeouts": {"update": 1800, "start": 1200, "check": 30, "stop": 120},
    "configure": configure,
    "server_version": server_version,
    "stop_signal": "ctrl_break",
    # The server looks for its files in the folder it is started from.
    "cwd": lambda root, run: _server_dir(root),
    # -offline skips the Strange Loop Games account sign-in the server otherwise requires.
    "launch": lambda root, run, password: [str(_server_dir(root) / "EcoServer.exe"), "-nogui", "-offline"],
    # Two long replies in a row; anything much longer makes Eco drop the connection.
    "probes": ["help manage", "help manage"],
    "checks": [
        {"label": "Online Players", "expect": {"reply": r"^Online:"}},
        {"label": "Save", "expect": {"reply": r"Saving the world", "log_contains": "Saving world"}},
        {"label": "Kick", "arg": "SRTTestPlayer", "player_required": True},
        {"label": "Ban", "arg": "SRTTestPlayer,srt live test",
         "expect": {"file_contains": [USERS_CONFIG, "SRTTestPlayer"]}},
        {"label": "Ban List", "expect": {"reply": r"SRTTestPlayer"}},
        {"label": "Unban", "arg": "SRTTestPlayer",
         "expect": {"reply": r"Removed 'SRTTestPlayer'", "file_not_contains": [USERS_CONFIG, "SRTTestPlayer"]}},
    ],
}
