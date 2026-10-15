"""ARK: Survival Evolved live-test definition."""

from pathlib import Path
import re

ARK_DIR = ("servers", "ark_se")
MAP = "TheIsland"
# A SteamID64 no real player has (account number 0).
TEST_ID = "76561197960265728"
# What the server answers for any command with no output of its own, unknown commands included.
NO_RESPONSE = r"^Server received, But no response!!$"


def _server_dir(root: Path) -> Path:
    return root.joinpath(*ARK_DIR)


def _saved(run_dir: Path) -> Path:
    # The server has no -UserDir; Saved (settings, logs, worlds) stays in the install folder.
    return _server_dir(run_dir.parents[1]) / "ShooterGame" / "Saved"


def configure(root: Path, run_dir: Path, definition: dict, password: str) -> None:
    # Every setting goes on the command line (launch); the world gets its own folder
    # per run through AltSaveDirectoryName.
    run_dir.mkdir(parents=True, exist_ok=True)


def chat_logged(run_dir: Path, arg: str) -> bool:
    """The game log (-servergamelog) records each server chat message as "SERVER: <text>"."""
    return any(f"SERVER: {arg}" in path.read_text(encoding="utf-8", errors="replace")
               for path in (_saved(run_dir) / "Logs").glob("ServerGame.*.log"))


def server_log(run_dir: Path) -> Path:
    """The server writes nothing to its output; its log goes to this file (ABSLOG)."""
    return run_dir / "ShooterGame.log"


def world_saved(run_dir: Path, arg: str) -> bool:
    """A save after startup: "Saving world..." logged after "Full Startup:", and the run's world file exists.
    The autosave interval (15 minutes by default) is longer than a run."""
    log = server_log(run_dir)
    text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    _, found, after = text.partition("Full Startup:")
    return bool(found) and "Saving world..." in after and (_saved(run_dir) / run_dir.name / f"{MAP}.ark").exists()


def _ban_list(run_dir: Path) -> str:
    # The server keeps its ban list next to the exe.
    path = _server_dir(run_dir.parents[1]) / "ShooterGame" / "Binaries" / "Win64" / "BanList.txt"
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def banned(run_dir: Path, arg: str) -> bool:
    return arg in _ban_list(run_dir)


def unbanned(run_dir: Path, arg: str) -> bool:
    return arg not in _ban_list(run_dir)


def server_version(root: Path) -> str:
    manifest = _server_dir(root) / f"steamapps/appmanifest_{DEFINITION['appid']}.acf"
    if not manifest.exists():
        return "unknown"
    match = re.search(r'"buildid"\s+"([^"]+)"', manifest.read_text(encoding="utf-8", errors="replace"))
    return f"build {match.group(1)}" if match else "unknown"


def launch(root: Path, run: Path, password: str) -> list[str]:
    server = _server_dir(root) / "ShooterGame" / "Binaries" / "Win64" / "ShooterGameServer.exe"
    # ServerAdminPassword goes last on the URL. AltSaveDirectoryName gives each run a fresh
    # world under Saved. -NoBattlEye skips the anti-cheat service. -servergamelog writes
    # the game log that records chat. Without -FORCELOGFLUSH the log file stays empty.
    url = (f"{MAP}?listen?SessionName=SRT-live-test?MultiHome=127.0.0.1?QueryPort=27015"
           f"?RCONEnabled=True?RCONPort={DEFINITION['rcon_port']}?AltSaveDirectoryName={run.name}"
           f"?ServerAdminPassword={password}")
    return [str(server), url, "-server", "-port=7777", "-MULTIHOME=127.0.0.1", "-NoBattlEye", "-servergamelog",
            "-log", "-FORCELOGFLUSH", f"-ABSLOG={server_log(run)}"]


DEFINITION = {
    "key": "ark_se", "name": "ARK: Survival Evolved", "catalog_key": "ark_se", "appid": 376030,
    # RCON follows MultiHome (unlike Ascended), so it listens on 127.0.0.1 only.
    "rcon_port": 27020, "ports": [7777, 7778, 27015, 27020], "loopback_supported": True,
    "timeouts": {"update": 3600, "start": 1800, "check": 30, "stop": 300},
    "configure": configure,
    "server_version": server_version,
    "launch": launch,
    "server_log": server_log,
    "ready_log": "Full Startup:",
    # A repeat on one connection, the console wiki's "cheat" prefix (RCON does not take
    # it), and an unknown command: both answer the no-output line.
    "probes": ["ListPlayers", "ListPlayers", "cheat ListPlayers", "NoSuchCommand"],
    # Reply texts are the server's own strings (ShooterGameServer.exe).
    # Patterns are anchored at the start so stray bytes ahead of a reply fail the check.
    "checks": [
        {"label": "List Players", "expect": {"reply": r"^No Players Connected$"}},
        {"label": "Save World", "evidence_wait": 10,
         "expect": {"reply": r"^World Saved$", "evidence": world_saved}},
        {"label": "Server Message", "arg": "SRT live test {random}", "evidence_wait": 5,
         "expect": {"reply": NO_RESPONSE, "evidence": chat_logged}},
        {"label": "Get Chat", "expect": {"reply": r"^SERVER: SRT live test [0-9a-f]{8}$"}},
        {"label": "Broadcast", "arg": "SRT live test {random}", "player_required": True},
        {"label": "Destroy Wild Dinos", "expect": {"reply": r"^All Wild Dinos Destroyed$"}},
        # The server replies "<arg> Kicked" for any argument, whether or not anyone
        # matches, so the reply proves nothing.
        {"label": "Kick Player", "arg": TEST_ID, "player_required": True},
        # Ban and unban work on IDs that are not online; the ban list file is the evidence.
        {"label": "Ban Player", "arg": TEST_ID, "evidence_wait": 5,
         "expect": {"reply": rf"^{TEST_ID} Banned$", "evidence": banned}},
        {"label": "Unban Player", "arg": TEST_ID, "evidence_wait": 5,
         "expect": {"reply": rf"^{TEST_ID} Unbanned$", "evidence": unbanned}},
        {"label": "Shutdown", "stop": True, "expect": {"disconnect": True}},
    ],
}
