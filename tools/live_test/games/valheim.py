"""Valheim live-test definition."""

from pathlib import Path
import shutil
import zipfile
import re

VALHEIM_RCON_CONFIG = "BepInEx/config/org.tristan.rcon.cfg"
LOOPBACK_REASON = ("ValheimRcon always listens on all addresses (RconProxy.cs); its Whitelist IP mask is set "
                   "to 127.0.0.1 so other addresses are refused.")


def configure(root: Path, run_dir: Path, definition: dict, password: str) -> None:
    config = root / "servers" / definition["key"] / VALHEIM_RCON_CONFIG
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_bytes(
        ("[1. Rcon]\r\n"
         f"Port = {definition['rcon_port']}\r\n"
         f"Password = {password}\r\n"
         "Whitelist IP mask = 127.0.0.1\r\n"
         "Blacklist IP mask = \r\n").encode("utf-8")
    )


def _install_member(archive: zipfile.ZipFile, name: str, destination: Path) -> None:
    if name.endswith("/") or ".." in Path(name).parts:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(name) as source, destination.open("wb") as output:
        shutil.copyfileobj(source, output)


def install_bepinex(archive: zipfile.ZipFile, root: Path, server_dir: Path) -> None:
    prefix = "BepInExPack_Valheim/"
    members = [name for name in archive.namelist() if name.startswith(prefix)]
    if not members:
        raise RuntimeError("BepInExPack archive did not contain its top-level directory")
    for name in members:
        _install_member(archive, name, server_dir / name.removeprefix(prefix))


def install_rcon(archive: zipfile.ZipFile, root: Path, server_dir: Path) -> None:
    matches = [name for name in archive.namelist() if name == "ValheimRcon.dll"]
    if len(matches) != 1:
        raise RuntimeError("ValheimRcon archive did not contain exactly one root ValheimRcon.dll")
    _install_member(archive, matches[0], server_dir / "BepInEx" / "plugins" / "ValheimRcon" / "ValheimRcon.dll")


def server_version(root: Path) -> str:
    manifest = root / "servers" / DEFINITION["key"] / f"steamapps/appmanifest_{DEFINITION['appid']}.acf"
    if not manifest.exists():
        return "unknown"
    match = re.search(r'"buildid"\s+"([^"]+)"', manifest.read_text(encoding="utf-8", errors="replace"))
    return match.group(1) if match else "unknown"


DEFINITION = {
    "key": "valheim", "name": "Valheim", "catalog_key": "valheim", "appid": 896660,
    "rcon_port": 2458, "ports": [2456, 2457, 2458], "loopback_supported": False,
    "loopback_reason": LOOPBACK_REASON,
    "timeouts": {"update": 1800, "start": 600, "check": 30, "stop": 60},
    "downloads": [
        {"baseline": "bepinexpack", "destination": lambda root, server: server / "BepInExPack_Valheim.zip", "install": install_bepinex, "mod": True},
        {"baseline": "valheim_rcon", "destination": lambda root, server: server / "ValheimRcon.zip", "install": install_rcon, "mod": True},
    ], "configure": configure,
    # ValheimRcon does not open its port on a brand-new world's first start
    # (observed 2026-10-14 with 1.6.2); after one clean shutdown it does.
    "first_boot": {"ready_log": "Loading: Done"},
    "stop_signal": "ctrl_break",
    "server_version": server_version,
    "launch": lambda root, run, password: [str(root / "servers" / "valheim" / "valheim_server.exe"), "-nographics", "-batchmode",
        "-name", "SRT-live-test", "-port", "2456", "-world", "SRT-live-test", "-password", password,
        "-savedir", str(run / "save"), "-public", "0"],
    "checks": [
        {"label": "Save", "expect": {"modified_glob": "save/**/*"}},
        {"label": "Kick", "arg": "SRTTestPlayer", "player_required": True},
        {"label": "Ban", "arg": "SRTTestPlayer", "expect": {"file_contains": ["save/bannedlist.txt", "SRTTestPlayer"]}},
        {"label": "List Bans", "expect": {"reply": r"SRTTestPlayer"}},
        {"label": "Unban", "arg": "SRTTestPlayer", "expect": {"file_not_contains": ["save/bannedlist.txt", "SRTTestPlayer"]}},
    ],
}
