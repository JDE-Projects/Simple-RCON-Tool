import zipfile

from tools.live_test.games.conan import BAN_FILE, DEFINITION as CONAN, TEST_ID
from tools.live_test.games.minecraft import DEFINITION as MINECRAFT
from tools.live_test.games.valheim import (DEFINITION as VALHEIM, VALHEIM_RCON_CONFIG,
                                           install_bepinex, install_rcon)


def test_minecraft_config_is_localhost_and_contains_fresh_password(tmp_path):
    MINECRAFT["configure"](tmp_path, tmp_path, MINECRAFT, "fresh-password")
    config = (tmp_path / "server.properties").read_text(encoding="utf-8")
    assert "server-ip=127.0.0.1" in config
    assert "rcon.password=fresh-password" in config
    assert (tmp_path / "eula.txt").read_text(encoding="utf-8") == "eula=true\n"


def test_valheim_config_is_fresh_section_aware_and_crlf(tmp_path):
    VALHEIM["configure"](tmp_path, tmp_path, VALHEIM, "fresh-password")
    data = (tmp_path / "servers" / "valheim" / VALHEIM_RCON_CONFIG).read_bytes()
    assert b"[1. Rcon]\r\n" in data
    assert b"Port = 2458\r\nPassword = fresh-password\r\n" in data
    assert b"Whitelist IP mask = 127.0.0.1\r\n" in data
    assert b"\n" not in data.replace(b"\r\n", b"")


def test_valheim_mod_install_layout(tmp_path):
    bepinex = tmp_path / "bepinex.zip"
    rcon = tmp_path / "rcon.zip"
    with zipfile.ZipFile(bepinex, "w") as archive:
        archive.writestr("BepInExPack_Valheim/BepInEx/core/BepInEx.dll", b"core")
        archive.writestr("BepInExPack_Valheim/winhttp.dll", b"winhttp")
        archive.writestr("BepInExPack_Valheim/doorstop_config.ini", b"doorstop")
    with zipfile.ZipFile(rcon, "w") as archive:
        archive.writestr("ValheimRcon.dll", b"rcon")
        archive.writestr("README.md", b"readme")
    server = tmp_path / "servers" / "valheim"
    with zipfile.ZipFile(bepinex) as archive:
        install_bepinex(archive, tmp_path, server)
    with zipfile.ZipFile(rcon) as archive:
        install_rcon(archive, tmp_path, server)
    assert (server / "BepInEx" / "core" / "BepInEx.dll").read_bytes() == b"core"
    assert (server / "winhttp.dll").read_bytes() == b"winhttp"
    assert (server / "BepInEx" / "plugins" / "ValheimRcon" / "ValheimRcon.dll").read_bytes() == b"rcon"
    assert not (server / "README.md").exists()


def test_conan_config_seeds_ban_list_with_test_id_only(tmp_path):
    CONAN["configure"](tmp_path, tmp_path, CONAN, "secret")
    assert (tmp_path / BAN_FILE).read_text(encoding="utf-8") == TEST_ID + "\n"
    unban = next(check for check in CONAN["checks"] if check["label"] == "Unban")
    assert unban["arg"] == TEST_ID
    assert unban["expect"]["file_not_contains"] == [BAN_FILE, TEST_ID]
