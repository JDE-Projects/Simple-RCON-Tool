import builtins
import json

import pytest

import simple_rcon_tool as app
from app import paths
from app.services import servers


def valid_server(**changes):
    server = {
        "id": "srv-one",
        "name": " One ",
        "host": " 127.0.0.1 ",
        "port": 27015,
        "game": "cs2",
        "buttons": [{"label": " Status ", "command": " status ", "confirm": False}],
    }
    server.update(changes)
    return server


@pytest.fixture(autouse=True)
def server_file(monkeypatch, tmp_path):
    servers_file = tmp_path / "servers.json"
    monkeypatch.setattr(paths, "SERVERS_FILE", str(servers_file))
    monkeypatch.setattr(servers, "_server_store_warning", None)
    return servers_file


def backup_files(server_file):
    return list(server_file.parent.glob("servers.json.bad-*"))


def test_load_missing_file_is_clean_first_run():
    assert servers.load_servers() == []


def test_load_unreadable_file_raises_without_touching_file(monkeypatch, server_file):
    server_file.write_text("[]", encoding="utf-8")
    original_open = builtins.open

    def unreadable(path, *args, **kwargs):
        if path == str(server_file):
            raise OSError("blocked")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", unreadable)

    with pytest.raises(servers.ServerStoreError):
        servers.load_servers()
    assert server_file.read_bytes() == b"[]"


@pytest.mark.parametrize(
    "contents", [b"{not json", b'{"server": "not a list"}', b"[\xff\xfe broken]"]
)
def test_load_bad_root_sets_aside_file_and_warns(server_file, contents):
    server_file.write_bytes(contents)

    assert servers.load_servers() == []
    backups = backup_files(server_file)
    assert not server_file.exists()
    assert len(backups) == 1
    assert backups[0].read_bytes() == contents
    assert servers._server_store_warning == (
        f"Your saved server list could not be read, so it was set aside as {backups[0].name} "
        "and the list starts empty."
    )


@pytest.mark.parametrize(
    "record",
    [
        "not a record",
        {},
        {key: value for key, value in valid_server().items() if key != "id"},
        {key: value for key, value in valid_server().items() if key != "name"},
        {key: value for key, value in valid_server().items() if key != "host"},
        {key: value for key, value in valid_server().items() if key != "port"},
        {key: value for key, value in valid_server().items() if key != "game"},
        valid_server(id=""),
        valid_server(id="not valid"),
        valid_server(id=7),
        valid_server(name=""),
        valid_server(name=7),
        valid_server(host=""),
        valid_server(host=7),
        valid_server(game=""),
        valid_server(game=7),
        valid_server(port=0),
        valid_server(port=65536),
        valid_server(port=True),
        valid_server(port="27015"),
        valid_server(buttons="not a list"),
        valid_server(buttons=["not a button"]),
        valid_server(buttons=[{"label": "", "command": "status"}]),
        valid_server(buttons=[{"label": "Status", "command": ""}]),
        valid_server(buttons=[{"label": "Status", "command": "status", "confirm": "yes"}]),
    ],
)
def test_load_skips_invalid_records_and_preserves_original(server_file, record):
    original = json.dumps([record]).encode("utf-8")
    server_file.write_bytes(original)

    assert servers.load_servers() == []
    backups = backup_files(server_file)
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    assert json.loads(server_file.read_text(encoding="utf-8")) == []


def test_load_defaults_missing_buttons_and_keeps_removed_catalog_game(server_file):
    record = valid_server(game="rust")
    del record["buttons"]
    server_file.write_text(json.dumps([record]), encoding="utf-8")

    assert servers.load_servers() == [{
        "id": "srv-one", "name": "One", "host": "127.0.0.1", "port": 27015,
        "game": "rust", "buttons": [],
    }]
    assert backup_files(server_file) == []


def test_load_repairs_duplicate_ids_and_keeps_first(server_file):
    first = valid_server(name="First")
    second = valid_server(name="Second")
    server_file.write_text(json.dumps([first, second]), encoding="utf-8")

    loaded = servers.load_servers()

    assert loaded[0]["id"] == "srv-one"
    assert loaded[1]["id"].startswith("srv-")
    assert loaded[1]["id"] != "srv-one"
    assert len(backup_files(server_file)) == 1
    assert json.loads(server_file.read_text(encoding="utf-8")) == loaded


def test_load_mixed_records_keeps_valid_records_and_backup_bytes(server_file):
    original = b'[ {"id":"srv-good","name":"Good","host":"127.0.0.1","port":27015,"game":"cs2","buttons":[]}, {"id":"srv-bad","name":"","host":"127.0.0.1","port":27015,"game":"cs2","buttons":[]} ]'
    server_file.write_bytes(original)

    assert servers.load_servers() == [{
        "id": "srv-good", "name": "Good", "host": "127.0.0.1", "port": 27015,
        "game": "cs2", "buttons": [],
    }]
    assert backup_files(server_file)[0].read_bytes() == original


def test_load_returns_clean_records_when_cleanup_cannot_be_saved(monkeypatch, server_file):
    server_file.write_text(json.dumps([valid_server(), valid_server(name="")]), encoding="utf-8")
    monkeypatch.setattr(servers, "save_servers", lambda _servers: False)

    assert servers.load_servers()[0]["id"] == "srv-one"
    assert "cleanup could not be saved" in servers._server_store_warning
    assert len(backup_files(server_file)) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "bad id"},
        {"name": ""}, {"name": 7},
        {"host": ""}, {"host": 7},
        {"game": ""}, {"game": "removed-game"}, {"game": 7},
        {"port": 0}, {"port": 65536}, {"port": True}, {"port": "not-a-port"},
        {"buttons": "not a list"},
        {"buttons": ["not a button"]},
        {"buttons": [{"label": "", "command": "status"}]},
        {"buttons": [{"label": "Status", "command": ""}]},
        {"buttons": [{"label": "Status", "command": "status", "confirm": "yes"}]},
    ],
)
def test_save_rejects_invalid_input_without_changing_file(server_file, changes):
    original = b'[{"id":"srv-existing","name":"Existing","host":"127.0.0.1","port":27015,"game":"cs2","buttons":[]}]'
    server_file.write_bytes(original)

    assert app.Api().save_server(valid_server(**changes))["ok"] is False
    assert server_file.read_bytes() == original


def test_save_converts_port_and_drops_password_and_extra_keys(server_file):
    result = app.Api().save_server(valid_server(
        id=None, port="27015", password="secret", extra="drop this",
    ))

    assert result["ok"] is True
    saved = json.loads(server_file.read_text(encoding="utf-8"))
    assert set(saved[0]) == {"id", "name", "host", "port", "game", "buttons"}
    assert saved[0]["port"] == 27015
    assert "password" not in server_file.read_text(encoding="utf-8")


def test_save_does_not_write_when_loading_raises_store_error(monkeypatch, server_file):
    original = b"unreadable data"
    server_file.write_bytes(original)

    def fail_load():
        raise servers.ServerStoreError("Saved server list is unavailable.")

    monkeypatch.setattr(servers, "load_servers", fail_load)

    assert app.Api().save_server(valid_server(id=None)) == {
        "ok": False, "error": "Saved server list is unavailable.",
    }
    assert server_file.read_bytes() == original


@pytest.mark.parametrize("field", ["name", "host", "port", "game"])
def test_save_rejects_missing_required_fields_without_changing_file(server_file, field):
    original = b'[{"id":"srv-existing","name":"Existing","host":"127.0.0.1","port":27015,"game":"cs2","buttons":[]}]'
    server_file.write_bytes(original)
    record = valid_server()
    del record[field]

    assert app.Api().save_server(record)["ok"] is False
    assert server_file.read_bytes() == original


def test_save_rejects_non_record_without_changing_file(server_file):
    original = b"[]"
    server_file.write_bytes(original)

    assert app.Api().save_server(None)["ok"] is False
    assert server_file.read_bytes() == original


def test_get_config_returns_warning_once(server_file):
    server_file.write_text("not json", encoding="utf-8")
    api = app.Api()

    first = api.get_config()
    second = api.get_config()

    assert first["servers"] == []
    assert first["storeWarning"] is not None
    assert second["storeWarning"] is None


@pytest.mark.parametrize("method,args", [
    ("delete_server", ("srv-one",)),
    ("connect", ("srv-one", "password")),
])
def test_delete_and_connect_return_store_error_without_writing(monkeypatch, server_file, method, args):
    original = b"unreadable data"
    server_file.write_bytes(original)

    def fail_load():
        raise servers.ServerStoreError("Saved server list is unavailable.")

    monkeypatch.setattr(servers, "load_servers", fail_load)

    assert getattr(app.Api(), method)(*args) == {
        "ok": False, "error": "Saved server list is unavailable.",
    }
    assert server_file.read_bytes() == original


def test_load_warning_names_only_what_happened(server_file):
    server_file.write_text(
        json.dumps([valid_server(), valid_server(name="Copy"), valid_server(name="")]),
        encoding="utf-8",
    )

    servers.load_servers()

    backup_name = backup_files(server_file)[0].name
    assert servers._server_store_warning == (
        "1 saved server could not be read and was skipped. "
        "1 saved server shared an ID with another and was given a new one. "
        f"The original file was kept as {backup_name}."
    )


def test_load_warning_for_skipped_records_only(server_file):
    server_file.write_text(
        json.dumps([valid_server(), valid_server(id="srv-2", name=""), valid_server(id="srv-3", port=0)]),
        encoding="utf-8",
    )

    servers.load_servers()

    backup_name = backup_files(server_file)[0].name
    assert servers._server_store_warning == (
        f"2 saved servers could not be read and were skipped. The original file was kept as {backup_name}."
    )


def new_server(name):
    return {"id": None, "name": name, "host": "192.0.2.10", "port": 27015, "game": "cs2", "buttons": []}


def test_new_server_ids_pass_validation_and_differ():
    first, second = servers._new_server_id(), servers._new_server_id()

    assert first != second
    assert isinstance(servers._validate_server(valid_server(id=first), require_catalog_game=False), dict)


def test_add_after_deleting_a_middle_server_gets_a_fresh_id(server_file):
    api = app.Api()
    ids = [api.save_server(new_server(name))["servers"][-1]["id"] for name in ("A", "B", "C")]

    api.delete_server(ids[1])
    saved_servers = api.save_server(new_server("D"))["servers"]

    saved_ids = [s["id"] for s in saved_servers]
    assert [s["name"] for s in saved_servers] == ["A", "C", "D"]
    assert len(set(saved_ids)) == 3
    assert saved_ids[2] not in ids


def test_edit_after_add_after_delete_changes_only_the_target(server_file):
    api = app.Api()
    ids = [api.save_server(new_server(name))["servers"][-1]["id"] for name in ("A", "B", "C")]
    api.delete_server(ids[1])
    new_id = api.save_server(new_server("D"))["servers"][-1]["id"]

    edited = dict(new_server("D renamed"), id=new_id)
    saved_servers = api.save_server(edited)["servers"]

    assert [s["name"] for s in saved_servers] == ["A", "C", "D renamed"]
