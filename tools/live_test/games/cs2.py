"""Counter-Strike 2 live-test definition."""

from pathlib import Path
import re
import shutil

CS2_DIR = ("servers", "cs2")
START_MAP = "de_dust2"
NEXT_MAP = "de_mirage"
STEAM_CLIENT_FILES = ("steamclient64.dll", "tier0_s64.dll", "vstdlib_s64.dll")
# Bots may only take names from the game's bot profiles; this is one of them.
BOT = "Rezan"
STATUS = r"^Server:  Running \[127\.0\.0\.1:27015\]\n"


def _server_dir(root: Path) -> Path:
    return root.joinpath(*CS2_DIR)


def _bin(root: Path) -> Path:
    return _server_dir(root) / "game" / "bin" / "win64"


def configure(root: Path, run_dir: Path, definition: dict, password: str) -> None:
    # Every setting goes on the command line (launch). Without a Steam client installed
    # the server stops with "Could not determine Steam client install directory"; it
    # finds the client files next to its exe instead, so they come from SteamCMD's
    # folder (same Valve client, kept current by SteamCMD's self-update).
    run_dir.mkdir(parents=True, exist_ok=True)
    for name in STEAM_CLIENT_FILES:
        shutil.copy2(root / "steamcmd" / name, _bin(root) / name)


def map_changed(run_dir: Path, arg: str) -> bool:
    """The reply comes when the change is queued; the server log says when the new map is up."""
    log = run_dir / "server.log"
    return log.exists() and f"Host activate: Changelevel ({arg})" in log.read_text(encoding="utf-8", errors="replace")


def server_version(root: Path) -> str:
    manifest = _server_dir(root) / f"steamapps/appmanifest_{DEFINITION['appid']}.acf"
    if not manifest.exists():
        return "unknown"
    match = re.search(r'"buildid"\s+"([^"]+)"', manifest.read_text(encoding="utf-8", errors="replace"))
    return f"build {match.group(1)}" if match else "unknown"


def launch(root: Path, run: Path, password: str) -> list[str]:
    # -usercon turns on RCON over TCP. -ip pins the server's sockets to this machine.
    # +sv_lan 1 keeps it off the Steam server list. Four slots leave room for the test bot.
    return [str(_bin(root) / "cs2.exe"), "-dedicated", "-usercon", "-maxplayers", "4",
            "-ip", "127.0.0.1", "-port", str(DEFINITION["rcon_port"]),
            "+sv_lan", "1", "+game_type", "0", "+game_mode", "1",
            "+rcon_password", password, "+map", START_MAP]


DEFINITION = {
    "key": "cs2", "name": "Counter-Strike 2", "catalog_key": "cs2", "appid": 730,
    "rcon_port": 27015, "ports": [27015, 27020], "loopback_supported": True,
    "timeouts": {"update": 14400, "start": 600, "check": 30, "stop": 120},
    "configure": configure,
    "server_version": server_version,
    "stop_signal": "ctrl_break",
    "cwd": lambda root, run: _server_dir(root),
    # The server reads keyboard input from its console and logs an error each frame on a pipe.
    "console_input": True,
    "launch": launch,
    # The map's own bots get random names; they are replaced by one with a known name for
    # Kick. A repeat on one connection, a long reply (358 KB, one packet) and an unknown command.
    "probes": ["bot_quota 0", "bot_kick all", f"bot_add_ct expert {BOT}", "status", "status",
               "cvarlist", "NoSuchCommand"],
    # Patterns are anchored at the start so stray bytes ahead of a reply fail the check.
    "checks": [
        {"label": "Status", "expect": {"reply": STATUS + rf"[\s\S]*\nloaded spawngroup\(  1\)  : SV:  \[1: {START_MAP} "
                                                 rf"[\s\S]*'{BOT}'\n#end$"}},
        {"label": "Say", "arg": "SRT live test {random}",
         "expect": {"reply": r"^\[All Chat\]\[Console \(0\)\]: SRT live test [0-9a-f]{8}$"}},
        # A bot goes through the same name lookup as a player; the next Status is the evidence.
        {"label": "Kick", "arg": BOT, "expect": {"reply": rf"^{BOT} kicked by Console \(NETWORK_DISCONNECT_KICKED\)$"}},
        {"label": "Status", "expect": {"reply": STATUS + rf"(?![\s\S]*'{BOT}')[\s\S]*\n#end$"}},
        {"label": "Change Level", "arg": NEXT_MAP, "evidence_wait": 60,
         "expect": {"reply": rf'^Changelevel to {NEXT_MAP}\nchangelevel "{NEXT_MAP}"\n', "evidence": map_changed}},
        {"label": "Status", "expect": {"reply": STATUS + rf"[\s\S]*\nloaded spawngroup\(  1\)  : SV:  \[1: {NEXT_MAP} "}},
    ],
}
