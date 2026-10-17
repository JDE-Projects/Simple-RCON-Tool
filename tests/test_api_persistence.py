import json

import pytest

import simple_rcon_tool as app


SAVE_ERROR = "The server could not be saved. Your saved list was not changed."
DELETE_ERROR = "The server could not be deleted. Your saved list was not changed."


def server(**changes):
    record = {
        "id": "srv-one",
        "name": "Original Server",
        "host": "192.0.2.10",
        "port": 27015,
        "game": "ark_se",
        "buttons": [],
    }
    record.update(changes)
    return record


@pytest.fixture(autouse=True)
def server_file(monkeypatch, tmp_path):
    path = tmp_path / "servers.json"
    monkeypatch.setattr(app, "SERVERS_FILE", str(path))
    monkeypatch.setattr(app, "_server_store_warning", None)
    return path


def write_server(path):
    path.write_text(json.dumps([server()]), encoding="utf-8")


def test_save_failure_returns_error_and_leaves_file_unchanged(monkeypatch, server_file):
    write_server(server_file)
    original = server_file.read_bytes()
    monkeypatch.setattr(app, "save_servers", lambda _servers: False)

    result = app.Api().save_server(server(name="Renamed Server"))

    assert result == {"ok": False, "error": SAVE_ERROR}
    assert server_file.read_bytes() == original


def test_delete_failure_returns_error_preserves_session_and_does_not_disconnect(monkeypatch, server_file):
    write_server(server_file)
    original = server_file.read_bytes()
    api = app.Api()
    session = object()
    api.sessions["srv-one"] = session
    disconnected = []
    monkeypatch.setattr(app, "save_servers", lambda _servers: False)
    monkeypatch.setattr(api, "disconnect", lambda server_id: disconnected.append(server_id))

    result = api.delete_server("srv-one")

    assert result == {"ok": False, "error": DELETE_ERROR}
    assert server_file.read_bytes() == original
    assert disconnected == []
    assert api.sessions == {"srv-one": session}


def test_delete_success_still_disconnects(server_file):
    write_server(server_file)
    api = app.Api()
    session = object()
    api.sessions["srv-one"] = session
    disconnected = []
    api.disconnect = lambda server_id: disconnected.append(server_id)

    result = api.delete_server("srv-one")

    assert result == {"ok": True, "servers": []}
    assert disconnected == ["srv-one"]


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("save_server", (server(name="Renamed Server"),)),
        ("delete_server", ("srv-one",)),
        ("connect", ("srv-one", "")),
    ],
)
def test_operation_returns_pending_warning_once(monkeypatch, server_file, method, args):
    write_server(server_file)
    warning = "Saved server data needed attention."
    original_load = app.load_servers
    raised = False

    def load_with_warning():
        nonlocal raised
        servers = original_load()
        if not raised:
            app._set_server_store_warning(warning)
            raised = True
        return servers

    monkeypatch.setattr(app, "load_servers", load_with_warning)
    result = getattr(app.Api(), method)(*args)

    assert result["warning"] == warning
    assert app._take_server_store_warning() is None


def test_get_config_returns_warning_once(server_file):
    write_server(server_file)
    app._set_server_store_warning("Saved server data needed attention.")
    api = app.Api()

    assert api.get_config()["storeWarning"] == "Saved server data needed attention."
    assert api.get_config()["storeWarning"] is None
