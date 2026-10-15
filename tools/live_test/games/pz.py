"""Project Zomboid live-test definition."""

from pathlib import Path
import re
import sqlite3

PZ_DIR = ("servers", "pz")
SERVER_NAME = "srt"
# Settings, world, database and logs live under the run folder through -cachedir.
CACHE = "cache"


def _server_dir(root: Path) -> Path:
    return root.joinpath(*PZ_DIR)


def configure(root: Path, run_dir: Path, definition: dict, password: str) -> None:
    # The server fills in every setting missing from this file with its default.
    # Public and UPnP off keep it off the server list and the router.
    settings = {
        "Public": "false", "UPnP": "false", "DefaultPort": "16261", "UDPPort": "16262",
        "RCONPort": str(definition["rcon_port"]), "RCONPassword": password,
    }
    server = run_dir / CACHE / "Server"
    server.mkdir(parents=True, exist_ok=True)
    (server / f"{SERVER_NAME}.ini").write_text(
        "\n".join(f"{key}={value}" for key, value in settings.items()) + "\n", encoding="utf-8")


def _name(arg: str) -> str:
    return arg.strip().strip('"')


def chat_logged(run_dir: Path, arg: str) -> bool:
    """The chat log (a dated file) names each server message sent."""
    line = f"Server alert message: '{_name(arg)}' sent"
    return any(line in path.read_text(encoding="utf-8", errors="replace")
               for path in (run_dir / CACHE / "Logs").glob("*_chat.txt"))


def _role(run_dir: Path, arg: str) -> str | None:
    """The account's role in the server database: a ban sets "banned", an unban "user"."""
    database = run_dir / CACHE / "db" / f"{SERVER_NAME}.db"
    if not database.exists():
        return None
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as connection:
        row = connection.execute("SELECT r.name FROM whitelist w JOIN role r ON r.id = w.role WHERE w.username = ?",
                                 (_name(arg),)).fetchone()
    return row[0] if row else None


def banned(run_dir: Path, arg: str) -> bool:
    return _role(run_dir, arg) == "banned"


def unbanned(run_dir: Path, arg: str) -> bool:
    return _role(run_dir, arg) == "user"


def server_version(root: Path) -> str:
    manifest = _server_dir(root) / f"steamapps/appmanifest_{DEFINITION['appid']}.acf"
    if not manifest.exists():
        return "unknown"
    match = re.search(r'"buildid"\s+"([^"]+)"', manifest.read_text(encoding="utf-8", errors="replace"))
    return f"build {match.group(1)}" if match else "unknown"


def launch(root: Path, run: Path, password: str) -> list[str]:
    # The shipped StartServer64.bat, with a 4 GB heap instead of its fixed 16 GB.
    # -nosteam and -Dzomboid.steam=0 keep it off Steam; -ip pins its sockets
    # (RCON included) to this machine. -adminpassword answers the first-start
    # prompt for the admin account's password.
    return [str(_server_dir(root) / "jre64" / "bin" / "java.exe"), "-Djava.awt.headless=true",
            "-Dzomboid.steam=0", "-Dzomboid.znetlog=1", "-XX:+UseZGC", "-XX:-CreateCoredumpOnCrash",
            "-XX:-OmitStackTraceInFastThrow", "-Xmx4g", "-Djava.library.path=natives/",
            "-cp", "java/;java/projectzomboid.jar", "zombie.network.GameServer", "-statistic", "0",
            f"-cachedir={run / CACHE}", "-servername", SERVER_NAME, "-nosteam", "-ip", "127.0.0.1",
            "-adminusername", "admin", "-adminpassword", password]


DEFINITION = {
    "key": "pz", "name": "Project Zomboid", "catalog_key": "pz", "appid": 380870,
    "rcon_port": 27015, "ports": [16261, 16262, 27015], "loopback_supported": True,
    "timeouts": {"update": 3600, "start": 900, "check": 30, "stop": 120},
    "configure": configure,
    "server_version": server_version,
    # The classpath and natives/ are relative to the install folder.
    "cwd": lambda root, run: _server_dir(root),
    "launch": launch,
    "ready_log": "*** SERVER STARTED ****",
    # A long reply (more than one 4096-byte packet), a repeat on one connection,
    # and what the Server Message button sends for text with its own quotes, text
    # already quoted by habit, and an apostrophe.
    "probes": ["help", "players", "players", 'servermsg "SRT say "hi" there"',
               'servermsg ""SRT already quoted""', "servermsg \"SRT it's fine\""],
    # Patterns are anchored at the start so stray bytes ahead of a reply fail the check.
    # The server does not log RCON commands; the evidence below is written by the game itself.
    "checks": [
        {"label": "Players", "expect": {"reply": r"^Players connected \(0\):$"}},
        # The save is queued; "SaveAll took" is logged when it finishes.
        {"label": "Save", "evidence_wait": 15, "expect": {"reply": r"^World saved$", "log_contains": "SaveAll took"}},
        {"label": "Server Message", "arg": "SRT live test {random}", "evidence_wait": 5,
         "expect": {"reply": r"^Message sent\.$", "evidence": chat_logged}},
        # With no such player online the server replies "User <name> doesn't exist."
        # (and still logs a kick).
        {"label": "Kick User", "arg": '"SRTTestPlayer"', "player_required": True},
        # A name the server has never seen gets an account with the banned role.
        {"label": "Ban User", "arg": '"SRTTestPlayer"',
         "expect": {"reply": r"^System banned user SRTTestPlayer$", "evidence": banned}},
        # The reply is the same whether or not the name was banned; the role is the evidence.
        {"label": "Unban User", "arg": '"SRTTestPlayer"',
         "expect": {"reply": r"^System unbanned user SRTTestPlayer$", "evidence": unbanned}},
        {"label": "Show Options", "expect": {"reply": r"^List of Server Options:\n\* AdminSafehouse="}},
        {"label": "Save & Quit", "stop": True, "evidence_wait": 60,
         "expect": {"disconnect": True, "log_contains": "Shutdown handling started"}},
    ],
}
