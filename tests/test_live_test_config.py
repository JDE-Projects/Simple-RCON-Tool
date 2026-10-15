import importlib
import zipfile

import pytest

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


@pytest.mark.parametrize("game", ["ark_asa", "ark_se"])
def test_ark_ban_evidence_reads_the_ban_list_next_to_the_exe(tmp_path, game):
    module = importlib.import_module(f"tools.live_test.games.{game}")
    TEST_ID, banned, unbanned = module.TEST_ID, module.banned, module.unbanned
    run_dir = tmp_path / "runs" / f"{game}-1"
    run_dir.mkdir(parents=True)
    assert not banned(run_dir, TEST_ID) and unbanned(run_dir, TEST_ID)
    ban_list = tmp_path / "servers" / game / "ShooterGame" / "Binaries" / "Win64" / "BanList.txt"
    ban_list.parent.mkdir(parents=True)
    ban_list.write_text(TEST_ID + "\n", encoding="utf-8")
    assert banned(run_dir, TEST_ID) and not unbanned(run_dir, TEST_ID)


def test_ark_se_save_evidence_needs_a_save_after_startup_and_the_run_world(tmp_path):
    from tools.live_test.games.ark_se import MAP, server_log, world_saved
    run_dir = tmp_path / "runs" / "ark_se-1"
    run_dir.mkdir(parents=True)
    world = tmp_path / "servers" / "ark_se" / "ShooterGame" / "Saved" / "ark_se-1" / f"{MAP}.ark"
    world.parent.mkdir(parents=True)
    world.write_bytes(b"ark")
    server_log(run_dir).write_text("Saving world...\nFull Startup: 1.00 seconds\n", encoding="utf-8")
    assert not world_saved(run_dir, "")
    server_log(run_dir).write_text("Full Startup: 1.00 seconds\nSaving world...\n", encoding="utf-8")
    assert world_saved(run_dir, "")
    world.unlink()
    assert not world_saved(run_dir, "")
