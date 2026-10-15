"""Conan Exiles live-test definition."""

from pathlib import Path
import re

CONAN_DIR = ("servers", "conan")
# A platform ID no real player has, seeded into the ban list before start.
TEST_ID = "76561190000000001"
BAN_FILE = "user/Saved/blacklist.txt"
CLEAN = r"^(?![\s\S]*(?:Couldn't parse|Still processing))"


def _server_dir(root: Path) -> Path:
    return root.joinpath(*CONAN_DIR)


def configure(root: Path, run_dir: Path, definition: dict, password: str) -> None:
    # RCON settings go on the command line (launch); the world and its config
    # land under the run folder through -UserDir. The ban list is seeded with
    # TEST_ID because banplayer only finds players who are online.
    ban_file = run_dir / BAN_FILE
    ban_file.parent.mkdir(parents=True, exist_ok=True)
    ban_file.write_text(TEST_ID + "\n", encoding="utf-8")


def server_version(root: Path) -> str:
    manifest = _server_dir(root) / f"steamapps/appmanifest_{DEFINITION['appid']}.acf"
    if not manifest.exists():
        return "unknown"
    match = re.search(r'"buildid"\s+"([^"]+)"', manifest.read_text(encoding="utf-8", errors="replace"))
    return f"build {match.group(1)}" if match else "unknown"


def launch(root: Path, run: Path, password: str) -> list[str]:
    server = _server_dir(root) / "ConanSandbox" / "Binaries" / "Win64" / "ConanSandboxServer-Win64-Shipping.exe"
    # -MULTIHOME pins the server's sockets to this machine; Conan has no RCON
    # address setting of its own. -nosteam keeps it off the Steam server list.
    # -UserDir moves Saved (world, config, logs) into the run folder.
    return [str(server), "-log", "-stdout", "-FullStdOutLogOutput", "-nosteam",
            "-MULTIHOME=127.0.0.1", f"-UserDir={run / 'user'}",
            "-RconEnabled=1", f"-RconPassword={password}", f"-RconPort={DEFINITION['rcon_port']}"]


DEFINITION = {
    "key": "conan", "name": "Conan Exiles", "catalog_key": "conan", "appid": 443030,
    "rcon_port": 25575, "ports": [7777, 7778, 25575, 27015], "loopback_supported": True,
    "timeouts": {"update": 1800, "start": 1200, "check": 30, "stop": 180},
    "configure": configure,
    "server_version": server_version,
    "stop_signal": "ctrl_break",
    "launch": launch,
    # RCON opens about 20 s before the map loads; until then every command
    # answers "Couldn't find the command". This line marks the map loaded.
    "ready_log": "LogServerStats: Startup report",
    # The seeded ban list; listplayers twice for a repeat on one connection.
    "probes": ["listbans", "listplayers", "listplayers"],
    # Each pattern also refuses stray text from a second command ("Couldn't parse", "Still processing").
    # Unban is confirmed by the ban file losing the seeded TEST_ID.
    # A failed unban replies "Failed to ban player <id>, was not banned."
    "checks": [
        {"label": "List Players", "expect": {"reply": CLEAN + r"Idx \| Char name \| Player name \| User ID"}},
        {"label": "List Bans", "expect": {"reply": CLEAN + rf"[\s\S]*{TEST_ID}"}},
        {"label": "Unban", "arg": TEST_ID,
         "expect": {"reply": CLEAN + rf"Player {TEST_ID} unbanned\.$", "file_not_contains": [BAN_FILE, TEST_ID]}},
        # An empty ban list replies with this line alone.
        {"label": "List Bans", "expect": {"reply": CLEAN + r"Successfully executed: listbans$"}},
        {"label": "Broadcast", "arg": "SRT live test {random}",
         "expect": {"reply": CLEAN + r"Message has been broadcast\.$", "log_contains_arg": True}},
        {"label": "Help", "expect": {"reply": CLEAN + r"Commands:[\s\S]*listplayers[\s\S]*BanPlayer"}},
        # banplayer and kickplayer reply "No player with ..." for anyone offline.
        {"label": "Ban", "arg": f"{TEST_ID} srt live test", "player_required": True},
        {"label": "Kick", "arg": "SRTTestPlayer srt live test", "player_required": True},
    ],
}
