"""ARK: Survival Ascended live-test definition."""

from pathlib import Path
import re

ARK_DIR = ("servers", "ark_asa")
MAP = "TheIsland_WP"
# An Epic account ID no real player has (32 hex characters, like the real ones).
TEST_ID = "0002aaaaaaaaaaaaaaaaaaaaaaaaaaaa"
# What the server answers for any command with no output of its own, unknown commands included.
NO_RESPONSE = r"^Server received, But no response!!$"


def _server_dir(root: Path) -> Path:
    return root.joinpath(*ARK_DIR)


def configure(root: Path, run_dir: Path, definition: dict, password: str) -> None:
    # Every setting goes on the command line (launch); Saved (world, config, logs)
    # goes in the run folder through -UserDir.
    (run_dir / "user").mkdir(parents=True, exist_ok=True)


def chat_logged(run_dir: Path, arg: str) -> bool:
    """The game log (-servergamelog) records each server chat message as "SERVER: <text>"."""
    return any(f"SERVER: {arg}" in path.read_text(encoding="utf-8", errors="replace")
               for path in (run_dir / "user" / "Saved" / "Logs").glob("ServerGame.*.log"))


def _ban_list(run_dir: Path) -> str:
    # The server keeps its ban list next to the exe, outside the run folder.
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
    server = _server_dir(root) / "ShooterGame" / "Binaries" / "Win64" / "ArkAscendedServer.exe"
    # The server reads ServerAdminPassword last on the URL. -MULTIHOME pins the
    # game's sockets but not RCON's (see loopback_reason). -NoBattlEye skips the
    # anti-cheat service. -servergamelog writes the game log that records chat.
    url = (f"{MAP}?listen?SessionName=SRT-live-test?MultiHome=127.0.0.1?RCONEnabled=True"
           f"?RCONPort={DEFINITION['rcon_port']}?ServerAdminPassword={password}")
    return [str(server), url, "-port=7777", "-MULTIHOME=127.0.0.1", "-NoBattlEye", "-servergamelog",
            "-log", "-stdout", "-FullStdOutLogOutput", f"-UserDir={run / 'user'}"]


DEFINITION = {
    "key": "ark_asa", "name": "ARK: Survival Ascended", "catalog_key": "ark_asa", "appid": 2430930,
    "rcon_port": 27020, "ports": [7777, 27020], "loopback_supported": False,
    # The server's only RCON settings are RCONEnabled, RCONPort and
    # RCONServerGameLogBuffer; with MULTIHOME=127.0.0.1 RCON still listens on 0.0.0.0.
    "loopback_reason": "no RCON address setting; listens on every address (0.0.0.0) despite MULTIHOME",
    "timeouts": {"update": 3600, "start": 1800, "check": 30, "stop": 300},
    "configure": configure,
    "server_version": server_version,
    "launch": launch,
    "ready_log": "Server has completed startup and is now advertising for join",
    # A repeat on one connection, and the console wiki's "cheat" prefix, which
    # RCON does not take (the server answers the no-output line and runs nothing).
    "probes": ["ListPlayers", "ListPlayers", "cheat ListPlayers"],
    # Reply texts are the server's own strings (ArkAscendedServer.exe).
    # Patterns are anchored at the start so stray bytes ahead of a reply fail the check.
    # The server does not log RCON commands; the evidence below is written by the game itself.
    "checks": [
        {"label": "List Players", "expect": {"reply": r"^No Players Connected$"}},
        {"label": "Save World",
         "expect": {"reply": r"^World Saved$", "modified": f"user/Saved/SavedArks/{MAP}/{MAP}.ark"}},
        {"label": "Server Message", "arg": "SRT live test {random}", "evidence_wait": 5,
         "expect": {"reply": NO_RESPONSE, "evidence": chat_logged}},
        # GetChat returns the chat since the last GetChat: the message just sent.
        {"label": "Get Chat", "expect": {"reply": r"^SERVER: SRT live test [0-9a-f]{8}$"}},
        # Broadcast answers the no-output line, leaves nothing in chat or the logs,
        # and shows only on a player's screen.
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
        # The reply is "Exiting..."; the process must then exit.
        {"label": "Shutdown", "stop": True, "expect": {"disconnect": True}},
    ],
}
