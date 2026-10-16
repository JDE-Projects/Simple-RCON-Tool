"""Palworld live-test definition."""

from pathlib import Path
import re

PALWORLD_DIR = ("servers", "palworld")
RCON_PORT = 25575
GAME_PORT = 8211
TEST_ID = "76561190000000001"


def _server_dir(root: Path) -> Path:
    return root.joinpath(*PALWORLD_DIR)


def _settings_file(root: Path) -> Path:
    return _server_dir(root) / "Pal" / "Saved" / "Config" / "WindowsServer" / "PalWorldSettings.ini"


def configure(root: Path, run_dir: Path, definition: dict, password: str) -> None:
    # Pocketpair requires a copy of the default file here; editing the default
    # itself has no effect. This writes a fresh private configuration every run.
    server_dir = _server_dir(root)
    settings = _settings_file(root)
    default = server_dir / "DefaultPalWorldSettings.ini"
    option_settings = (f'OptionSettings=(ServerPlayerMaxNum=1,ServerName="SRT live test",'
                       f'AdminPassword="{password}",ServerPassword="{password}",'
                       f'PublicPort={GAME_PORT},PublicIP="",RCONEnabled=True,RCONPort={RCON_PORT})')
    settings.parent.mkdir(parents=True, exist_ok=True)
    content = default.read_text(encoding="utf-8")
    settings.write_text(re.sub(r"^OptionSettings=.*$", option_settings, content, flags=re.MULTILINE), encoding="utf-8")


def server_version(root: Path) -> str:
    manifest = _server_dir(root) / f"steamapps/appmanifest_{DEFINITION['appid']}.acf"
    if not manifest.exists():
        return "unknown"
    match = re.search(r'"buildid"\s+"([^"]+)"', manifest.read_text(encoding="utf-8", errors="replace"))
    return f"build {match.group(1)}" if match else "unknown"


def launch(root: Path, run: Path, password: str) -> list[str]:
    # -publiclobby is the documented opt-in for a community-server listing, so
    # it is deliberately absent. Palworld documents no local-address switch.
    # PalServer.exe only checks for XINPUT1_3.DLL (DirectX, not on Windows 11)
    # and then starts this exe, which does not need it.
    server = _server_dir(root) / "Pal" / "Binaries" / "Win64" / "PalServer-Win64-Shipping-Cmd.exe"
    return [str(server), f"-port={GAME_PORT}", "-logformat=text"]


DEFINITION = {
    "key": "palworld", "name": "Palworld", "catalog_key": "palworld", "appid": 2394010,
    "rcon_port": RCON_PORT, "ports": [GAME_PORT, RCON_PORT], "loopback_supported": False,
    "loopback_reason": "Palworld has no documented bind-address switch; RCON may listen on every address",
    "timeouts": {"update": 3600, "start": 1800, "check": 30, "stop": 300},
    "configure": configure,
    "server_version": server_version,
    "cwd": lambda root, run: _server_dir(root),
    "launch": launch,
    # The server writes no log file and nothing to its output, so readiness is
    # RCON accepting a login.
    # A repeat on one connection and an unknown command, recorded but ungraded.
    "probes": ["ShowPlayers", "ShowPlayers", "NoSuchCommand"],
    # Pocketpair documents the commands but not their replies; these are the real ones.
    # Kick and Ban reply "Failed to Kick/Ban: <id>" for anyone offline.
    # Patterns are anchored at the start so stray bytes ahead of a reply fail the check.
    "checks": [
        {"label": "Show Players", "expect": {"reply": r"^name,playeruid,steamid(?:\n|$)"}},
        {"label": "Info", "expect": {"reply": r"^Welcome to Pal Server\[v[\d.]+\] SRT live test$"}},
        {"label": "Save", "expect": {"reply": r"^Complete Save$"}},
        {"label": "Broadcast", "arg": "SRT live test {random}",
         "expect": {"reply": r"^Broadcasted: SRT_live_test_[0-9a-f]{8}$"}},
        {"label": "Kick Player", "arg": TEST_ID, "player_required": True},
        {"label": "Ban Player", "arg": TEST_ID, "player_required": True},
        # The 60-second delay keeps the server available for the final DoExit check.
        {"label": "Shutdown 60s", "expect": {"reply": r"^The server will shut down in 60 seconds\. Please prepare to exit the game\.$"}},
        {"label": "Force Stop", "stop": True, "expect": {"disconnect": True}},
    ],
}
