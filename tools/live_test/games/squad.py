"""Squad live-test definition."""

from pathlib import Path
import re
import shutil

SQUAD_DIR = ("servers", "squad")
# The server reads every .cfg from this folder (SERVERCONFIGSDIR), so the run gets its own copy.
CONFIG = "ServerConfig"
# The only layer in the run's rotation, so the next layer is known.
LAYER = "AlBasrah_AAS_v1"


def _server_dir(root: Path) -> Path:
    return root.joinpath(*SQUAD_DIR)


def _set(path: Path, values: dict) -> None:
    """Replace "Key=value" lines in a shipped .cfg, keeping its comments."""
    text = path.read_text(encoding="utf-8", errors="replace")
    for key, value in values.items():
        text, count = re.subn(rf"(?m)^{key}=.*$", f"{key}={value}", text)
        if not count:
            text += f"\n{key}={value}\n"
    path.write_text(text, encoding="utf-8")


def configure(root: Path, run_dir: Path, definition: dict, password: str) -> None:
    # The shipped config, copied into the run folder. Advertising off and LAN mode
    # keep the server off the public list. RCON settings go on the command line.
    config = run_dir / CONFIG
    shutil.copytree(_server_dir(root) / "SquadGame" / "ServerConfig", config)
    _set(config / "Server.cfg", {"ServerName": '"SRT-live-test"', "ShouldAdvertise": "false",
                                 "IsLANMatch": "true", "MaxPlayers": "2", "MapRotationMode": "LayerList",
                                 "RandomizeAtStart": "false"})
    (config / "LayerRotation.cfg").write_text(f"{LAYER}\n", encoding="utf-8")
    (run_dir / "user").mkdir(parents=True, exist_ok=True)


def broadcast_logged(run_dir: Path, arg: str) -> bool:
    """The server log records each broadcast as "ADMIN COMMAND: Message broadcasted <text> from RCON"."""
    log = run_dir / "server.log"
    return log.exists() and f"Message broadcasted <{arg}>" in log.read_text(encoding="utf-8", errors="replace")


def match_ended(run_dir: Path, arg: str) -> bool:
    """The match state changes after the server logs "ADMIN COMMAND: Match ended from RCON"."""
    log = run_dir / "server.log"
    text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    _, found, after = text.partition("ADMIN COMMAND: Match ended from RCON")
    return bool(found) and "LogGameState: Match State Changed" in after


def layer_reloaded(run_dir: Path, arg: str) -> bool:
    """A restart loads the layer again: a second "Bringing World .../Gameplay_Layers/..." line after startup's."""
    log = run_dir / "server.log"
    text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    return len(re.findall(r"LogWorld: Bringing World /\S+/Gameplay_Layers/", text)) >= 2


def server_version(root: Path) -> str:
    manifest = _server_dir(root) / f"steamapps/appmanifest_{DEFINITION['appid']}.acf"
    if not manifest.exists():
        return "unknown"
    match = re.search(r'"buildid"\s+"([^"]+)"', manifest.read_text(encoding="utf-8", errors="replace"))
    return f"build {match.group(1)}" if match else "unknown"


def launch(root: Path, run: Path, password: str) -> list[str]:
    server = _server_dir(root) / "SquadGame" / "Binaries" / "Win64" / "SquadGameServer.exe"
    # Rcon.cfg names RCONIP, RCONPORT and RCONPASSWORD as command-line settings.
    # MULTIHOME pins the game's sockets; -UserDir puts Saved (logs) in the run folder.
    return [str(server), "MULTIHOME=127.0.0.1", "Port=7787", "QueryPort=27165", "BEACONPORT=15000",
            "RCONIP=127.0.0.1", f"RCONPORT={DEFINITION['rcon_port']}", f"RCONPASSWORD={password}",
            f"SERVERCONFIGSDIR={run / CONFIG}", f"-UserDir={run / 'user'}",
            "-log", "-stdout", "-FullStdOutLogOutput"]


DEFINITION = {
    "key": "squad", "name": "Squad", "catalog_key": "squad", "appid": 403240,
    "rcon_port": 21114, "ports": [7787, 27165, 15000, 21114], "loopback_supported": True,
    "timeouts": {"update": 3600, "start": 1800, "check": 30, "stop": 300},
    "configure": configure,
    "server_version": server_version,
    "launch": launch,
    "stop_signal": "ctrl_break",
    # RCON opens about 10 s before the layer loads; a command sent before then gets no reply in time.
    "ready_log": "LogGameState: Match State Changed from EnteringMap to WaitingToStart",
    # ListCommands 1 is the server's own help text (about 10 KB, many packets), sent twice on one
    # connection; an unknown command gets no reply.
    "probes": ["ListCommands 1", "ListCommands 1", "ShowCommandInfo AdminKickById", "ShowCurrentMap", "NoSuchCommand"],
    # Reply starts are the ones SquadJS parses (squad-server/rcon.js); patterns are anchored
    # so stray bytes ahead of a reply fail the check.
    "checks": [
        {"label": "List Players", "expect": {"reply": r"^----- Active Players -----\n"
                                                       r"----- Recently Disconnected Players \[Max of 15\] -----$"}},
        {"label": "List Squads", "expect": {"reply": r"^----- Active Squads -----\nTeam ID: 1 \(.+\) - Tickets: \d+\n"
                                                      r"Team ID: 2 \(.+\) - Tickets: \d+$"}},
        {"label": "Show Next Map", "expect": {"reply": r"^Next level is Al Basrah, layer is AlBasrah_AAS_v1"}},
        {"label": "Broadcast", "arg": "SRT live test {random}", "evidence_wait": 5,
         "expect": {"reply": r"^Message broadcasted <SRT live test [0-9a-f]{8}>$", "evidence": broadcast_logged}},
        # The server needs an ID and a reason; for an ID not online it replies
        # "ERROR: Unable to find player with id (99)".
        {"label": "Kick by ID", "arg": "99 SRT test", "player_required": True},
        # With no players the match stays in staging, and ending it changes nothing,
        # so the effect is recorded as unverified.
        {"label": "End Match", "evidence_wait": 15,
         "expect": {"reply": r"^Match ended$", "evidence": match_ended, "unverified_if_no_evidence": True}},
        {"label": "Restart Match", "evidence_wait": 30,
         "expect": {"reply": r"^Game restarted$", "evidence": layer_reloaded}},
    ],
}
