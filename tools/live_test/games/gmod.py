"""Garry's Mod live-test definition."""

from pathlib import Path
import re

GMOD_DIR = ("servers", "gmod")
START_MAP = "gm_construct"
NEXT_MAP = "gm_flatgrass"


def _server_dir(root: Path) -> Path:
    return root.joinpath(*GMOD_DIR)


def configure(root: Path, run_dir: Path, definition: dict, password: str) -> None:
    # Every setting goes on the command line (launch); nothing is written to disk.
    run_dir.mkdir(parents=True, exist_ok=True)


def server_version(root: Path) -> str:
    manifest = _server_dir(root) / f"steamapps/appmanifest_{DEFINITION['appid']}.acf"
    if not manifest.exists():
        return "unknown"
    match = re.search(r'"buildid"\s+"([^"]+)"', manifest.read_text(encoding="utf-8", errors="replace"))
    return f"build {match.group(1)}" if match else "unknown"


def launch(root: Path, run: Path, password: str) -> list[str]:
    # -ip pins every socket (RCON shares the game port over TCP) to this machine.
    # +sv_lan 1 keeps it off the Steam server list; -nohltv leaves SourceTV closed.
    return [str(_server_dir(root) / "srcds.exe"), "-console", "-game", "garrysmod",
            "-ip", "127.0.0.1", "-port", str(DEFINITION["rcon_port"]), "-nohltv",
            "+sv_lan", "1", "+maxplayers", "2", "+gamemode", "sandbox",
            "+rcon_password", password, "+map", START_MAP]


DEFINITION = {
    "key": "gmod", "name": "Garry's Mod", "catalog_key": "gmod", "appid": 4020,
    "rcon_port": 27015, "ports": [27015, 27005, 27020, 26900], "loopback_supported": True,
    "timeouts": {"update": 3600, "start": 600, "check": 30, "stop": 120},
    "configure": configure,
    "server_version": server_version,
    "stop_signal": "ctrl_break",
    "cwd": lambda root, run: _server_dir(root),
    # In -console mode srcds reads keyboard input and stops with an Engine Error if it gets a pipe.
    "console_input": True,
    "launch": launch,
    # Each button's own help text, a long multi-packet reply, and a repeat on one connection.
    "probes": ["help status", "help say", "help kick", "help changelevel",
               "cvarlist", "status", "status"],
    # The server log echoes every RCON command ('rcon from ...: command "..."'), so a
    # log match on the argument proves only that the command arrived. Patterns are
    # anchored at the start so stray bytes ahead of a reply fail the check.
    "checks": [
        {"label": "Status", "expect": {"reply": rf"^hostname: [\s\S]*\nmap\s+: {START_MAP} "}},
        # The reply is the chat line the server sent.
        {"label": "Say", "arg": "SRT live test {random}", "expect": {"reply": r"^Console: SRT live test [0-9a-f]{8}$"}},
        # With no such player online the server replies 'name "SRTTestPlayer" not found'.
        {"label": "Kick", "arg": "SRTTestPlayer", "player_required": True},
        # The reply comes before the map change starts, so the server log has
        # nothing to show yet; the next Status is the evidence.
        {"label": "Change Level", "arg": NEXT_MAP, "expect": {"reply": r"^$", "empty_reply_ok": True}},
        # The map change is confirmed by status naming the new map.
        {"label": "Status", "expect": {"reply": rf"^hostname: [\s\S]*\nmap\s+: {NEXT_MAP} "}},
    ],
}
