import json

import simple_rcon_tool as app


UNSUPPORTED_GAME_KEYS = {
    "factorio", "rust", "7dtd", "soulmask", "dayz", "satisfactory", "windrose",
}
UNSUPPORTED_GAME_ERROR = (
    "This server's game is no longer supported. Edit the server to pick another game, "
    "or delete it."
)


def test_catalog_contains_only_unique_source_rcon_games():
    assert not UNSUPPORTED_GAME_KEYS.intersection(app.CATALOG_BY_KEY)
    assert len(app.CATALOG_BY_KEY) == len(app.GAME_CATALOG)
    for game in app.GAME_CATALOG:
        assert {"key", "name", "protocol", "buttons"}.issubset(game)
        assert game["protocol"] == "source"
        assert isinstance(game["buttons"], list)


def test_connect_rejects_saved_unsupported_game_before_password_check(monkeypatch, tmp_path):
    servers_file = tmp_path / "servers.json"
    servers_file.write_text(json.dumps([{
        "id": "srv-rust", "name": "Rust server", "host": "127.0.0.1", "port": 28016,
        "game": "rust", "buttons": [],
    }]), encoding="utf-8")
    monkeypatch.setattr(app, "SERVERS_FILE", str(servers_file))
    api = app.Api()

    assert api.connect("srv-rust", "") == {"ok": False, "error": UNSUPPORTED_GAME_ERROR}
    assert api.sessions == {}


def test_save_server_rejects_removed_game():
    result = app.Api().save_server({
        "name": "Factorio server", "host": "127.0.0.1", "port": 27015,
        "game": "factorio", "buttons": [],
    })

    assert result == {"ok": False, "error": "Pick a game from the list."}
