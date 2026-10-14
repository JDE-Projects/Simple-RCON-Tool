"""Minecraft Java live-test definition."""

from pathlib import Path
import zipfile


def configure(root: Path, run_dir: Path, definition: dict, password: str) -> None:
    (run_dir / "eula.txt").write_text("eula=true\n", encoding="utf-8")
    properties = {
        "enable-rcon": "true", "rcon.port": str(definition["rcon_port"]),
        "rcon.password": password, "server-ip": "127.0.0.1",
        "enable-query": "false", "level-name": "world",
    }
    (run_dir / "server.properties").write_text(
        "\n".join(f"{key}={value}" for key, value in properties.items()) + "\n",
        encoding="utf-8",
    )


def install_jre(archive: zipfile.ZipFile, root: Path, server_dir: Path) -> None:
    runtime = root / "runtime"
    if (runtime / "java" / "bin" / "java.exe").exists():
        return
    archive.extractall(runtime)
    extracted = next((path for path in runtime.iterdir() if path.is_dir() and path.name != "java"), None)
    if not extracted:
        raise RuntimeError("Temurin archive did not contain a JRE directory")
    extracted.rename(runtime / "java")


def server_version(root: Path) -> str:
    return "26.3"


DEFINITION = {
    "key": "minecraft", "name": "Minecraft (Java)", "catalog_key": "minecraft",
    "rcon_port": 25575, "ports": [25565, 25575], "loopback_supported": True,
    "timeouts": {"update": 900, "start": 180, "check": 20, "stop": 60},
    "downloads": [
        {"baseline": "minecraft_server", "destination": lambda root, server: server / "server.jar"},
        {"baseline": "temurin_jre", "destination": lambda root, server: root / "runtime" / "temurin-jre.zip", "install": install_jre},
    ],
    "configure": configure,
    "server_version": server_version,
    "launch": lambda root, run, password: [str(root / "runtime" / "java" / "bin" / "java.exe"),
                                "-Xmx2G", "-jar", str(root / "servers" / "minecraft" / "server.jar"), "nogui"],
    "checks": [
        {"label": "List", "expect": {"reply": r"There are 0 of a max of"}},
        {"label": "Say", "arg": "srt-check-{random}", "expect": {"log_contains_arg": True, "empty_reply_ok": True}},
        {"label": "Save All", "expect": {"reply": r"Saved the game", "modified": "world/level.dat"}},
        {"label": "Kick", "arg": "SRTTestPlayer", "player_required": True},
        {"label": "Ban", "arg": "Notch",
         "expect": {"file_contains": ["banned-players.json", "Notch"], "unverified_if_no_evidence": True}, "cleanup": "pardon Notch"},
        {"label": "Ban List", "expect": {"reply": r"There are no bans|There are \d+ ban"}},
        {"label": "Weather Clear", "expect": {"reply": r"Set the weather to clear"}},
        {"label": "Set Day", "expect": {"reply": r"Set the time to|Set minecraft:overworld to time marker minecraft:day"}},
        {"label": "Stop Server", "expect": {"disconnect": True, "process_exits": True, "log_contains": "Stopping"}, "stop": True},
    ],
}
