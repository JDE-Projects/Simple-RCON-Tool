import json
import socket

import pytest

from app.api import Api
from app import paths
from app.services import catalog, rcon


UNSUPPORTED_GAME_KEYS = {
    "factorio", "rust", "7dtd", "soulmask", "dayz", "satisfactory", "windrose",
}
UNSUPPORTED_GAME_ERROR = (
    "This server's game is no longer supported. Edit the server to pick another game, "
    "or delete it."
)


def test_catalog_contains_only_unique_source_rcon_games():
    assert not UNSUPPORTED_GAME_KEYS.intersection(catalog.CATALOG_BY_KEY)
    assert len(catalog.CATALOG_BY_KEY) == len(catalog.GAME_CATALOG)
    for game in catalog.GAME_CATALOG:
        assert {"key", "name", "buttons"}.issubset(game)
        assert isinstance(game["buttons"], list)


def test_stop_command_catalog_flags_only_the_five_stop_buttons():
    assert {(game["key"], button["label"]) for game in catalog.GAME_CATALOG for button in game["buttons"]
            if button.get("stops_server")} == {
        ("ark_se", "Shutdown"),
        ("ark_asa", "Shutdown"),
        ("palworld", "Force Stop"),
        ("minecraft", "Stop Server"),
        ("pz", "Save & Quit"),
    }


@pytest.mark.parametrize(
    ("game_key", "command", "expected"),
    [
        ("minecraft", "stop", True),
        ("minecraft", "  STOP  ", True),
        ("ark_se", "doexit", True),
        ("ark_se", "Shutdown", False),
        ("minecraft", "list", False),
        ("palworld", "Shutdown 60 Restarting", False),
        ("unknown", "stop", False),
    ],
)
def test_is_stop_command_matches_stop_commands_by_text(game_key, command, expected):
    assert rcon.is_stop_command(game_key, command) is expected


@pytest.mark.parametrize(
    ("reply", "error", "expected"),
    [
        (None, rcon.RconConnectionClosed("closed"), "expected_disconnect"),
        (None, ConnectionResetError(), "expected_disconnect"),
        (None, ConnectionAbortedError(), "expected_disconnect"),
        (None, None, "no_reply"),
        ("", None, "replied"),
        ("reply", None, "replied"),
        (None, rcon.RconError("failed"), "failed"),
        (None, socket.timeout(), "failed"),
    ],
)
def test_stop_outcome_classifies_replies_and_errors(reply, error, expected):
    assert rcon.stop_outcome(reply, error) == expected


def test_connect_rejects_saved_unsupported_game_before_password_check(monkeypatch, tmp_path):
    servers_file = tmp_path / "servers.json"
    servers_file.write_text(json.dumps([{
        "id": "srv-rust", "name": "Rust server", "host": "127.0.0.1", "port": 28016,
        "game": "rust", "buttons": [],
    }]), encoding="utf-8")
    monkeypatch.setattr(paths, "SERVERS_FILE", str(servers_file))
    api = Api("test")

    assert api.connect("srv-rust", "") == {"ok": False, "error": UNSUPPORTED_GAME_ERROR}
    assert api._sessions == {}


def test_save_server_rejects_removed_game():
    result = Api("test").save_server({
        "name": "Factorio server", "host": "127.0.0.1", "port": 27015,
        "game": "factorio", "buttons": [],
    })

    assert result == {"ok": False, "error": "Pick a game from the list."}
