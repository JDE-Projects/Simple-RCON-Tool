import builtins
import json
from pathlib import Path

import pytest

import simple_rcon_tool as app


@pytest.fixture
def stores(monkeypatch, tmp_path):
    servers_file = tmp_path / "servers.json"
    prefs_file = tmp_path / "simple_rcon_tool.pref"
    monkeypatch.setattr(app, "SERVERS_FILE", str(servers_file))
    monkeypatch.setattr(app, "_pref_path", lambda: str(prefs_file))
    return {"servers": servers_file, "prefs": prefs_file}


def temp_files(path: Path):
    return list(path.parent.glob(f".{path.name}.*.tmp"))


@pytest.mark.parametrize(
    ("store", "save", "data", "expected"),
    [
        ("servers", app.save_servers, [{"name": "One"}], '[\n  {\n    "name": "One"\n  }\n]'),
        ("prefs", app.save_prefs, {"theme": "dark"}, '{"theme": "dark"}'),
    ],
)
def test_save_writes_json_atomically(stores, store, save, data, expected):
    path = stores[store]

    assert save(data) is True
    assert path.read_text(encoding="utf-8") == expected
    assert temp_files(path) == []


@pytest.mark.parametrize("store,save", [("servers", app.save_servers), ("prefs", app.save_prefs)])
def test_save_serialization_failure_preserves_destination(stores, store, save):
    path = stores[store]
    original = b"existing bytes"
    path.write_bytes(original)

    assert save({"bad": {1}}) is False
    assert path.read_bytes() == original
    assert temp_files(path) == []


@pytest.mark.parametrize("store,save", [("servers", app.save_servers), ("prefs", app.save_prefs)])
def test_save_temp_creation_failure_preserves_destination(monkeypatch, stores, store, save):
    path = stores[store]
    original = b"existing bytes"
    path.write_bytes(original)
    monkeypatch.setattr(app.tempfile, "mkstemp", lambda **_kwargs: (_ for _ in ()).throw(OSError("blocked")))

    assert save({"value": 1}) is False
    assert path.read_bytes() == original
    assert temp_files(path) == []


@pytest.mark.parametrize("store,save", [("servers", app.save_servers), ("prefs", app.save_prefs)])
@pytest.mark.parametrize("failure", ["write", "flush"])
def test_save_write_and_flush_failure_preserve_destination(monkeypatch, stores, store, save, failure):
    path = stores[store]
    original = b"existing bytes"
    path.write_bytes(original)
    original_fdopen = app.os.fdopen

    class FailingFile:
        def __init__(self, handle):
            self.handle = handle

        def write(self, contents):
            if failure == "write":
                raise OSError("blocked")
            return self.handle.write(contents)

        def flush(self):
            if failure == "flush":
                raise OSError("blocked")
            return self.handle.flush()

        def fileno(self):
            return self.handle.fileno()

        def close(self):
            return self.handle.close()

    monkeypatch.setattr(app.os, "fdopen", lambda *args, **kwargs: FailingFile(original_fdopen(*args, **kwargs)))

    assert save({"value": 1}) is False
    assert path.read_bytes() == original
    assert temp_files(path) == []


@pytest.mark.parametrize("store,save", [("servers", app.save_servers), ("prefs", app.save_prefs)])
def test_save_close_failure_preserves_destination(monkeypatch, stores, store, save):
    path = stores[store]
    original = b"existing bytes"
    path.write_bytes(original)
    original_fdopen = app.os.fdopen

    class CloseFailingFile:
        def __init__(self, handle):
            self.handle = handle
            self.close_calls = 0

        def write(self, contents):
            return self.handle.write(contents)

        def flush(self):
            return self.handle.flush()

        def fileno(self):
            return self.handle.fileno()

        def close(self):
            self.close_calls += 1
            self.handle.close()
            if self.close_calls == 1:
                raise OSError("blocked")

    monkeypatch.setattr(app.os, "fdopen", lambda *args, **kwargs: CloseFailingFile(original_fdopen(*args, **kwargs)))

    assert save({"value": 1}) is False
    assert path.read_bytes() == original
    assert temp_files(path) == []


@pytest.mark.parametrize("store,save", [("servers", app.save_servers), ("prefs", app.save_prefs)])
def test_save_fsync_failure_preserves_destination(monkeypatch, stores, store, save):
    path = stores[store]
    original = b"existing bytes"
    path.write_bytes(original)
    monkeypatch.setattr(app.os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError("blocked")))

    assert save({"value": 1}) is False
    assert path.read_bytes() == original
    assert temp_files(path) == []


@pytest.mark.parametrize("store,save", [("servers", app.save_servers), ("prefs", app.save_prefs)])
def test_save_replace_failure_preserves_destination(monkeypatch, stores, store, save):
    path = stores[store]
    original = b"existing bytes"
    path.write_bytes(original)
    monkeypatch.setattr(app.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("blocked")))

    assert save({"value": 1}) is False
    assert path.read_bytes() == original
    assert temp_files(path) == []


@pytest.mark.parametrize("store,save", [("servers", app.save_servers), ("prefs", app.save_prefs)])
def test_save_cleanup_failure_is_logged(monkeypatch, stores, store, save):
    path = stores[store]
    original = b"existing bytes"
    path.write_bytes(original)
    messages = []
    original_remove = app.os.remove
    monkeypatch.setattr(app.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("blocked")))

    def remove_then_fail(temp_path):
        original_remove(temp_path)
        raise OSError("cleanup blocked")

    monkeypatch.setattr(app.os, "remove", remove_then_fail)
    monkeypatch.setattr(app, "debug_log", messages.append)

    assert save({"value": 1}) is False
    assert path.read_bytes() == original
    assert temp_files(path) == []
    assert any("cleanup remove failed: OSError: cleanup blocked" in message for message in messages)


def test_load_prefs_missing_file_returns_empty(stores):
    assert app.load_prefs() == {}


def test_load_prefs_unreadable_file_is_left_in_place(monkeypatch, stores):
    path = stores["prefs"]
    original = b'{"theme":"dark"}'
    path.write_bytes(original)
    original_open = builtins.open

    def unreadable(file, *args, **kwargs):
        if file == str(path):
            raise OSError("blocked")
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", unreadable)

    assert app.load_prefs() == {}
    assert path.read_bytes() == original
    assert list(path.parent.glob("simple_rcon_tool.pref.bad-*")) == []


@pytest.mark.parametrize("contents", [b"{not json", b"\xff\xfe broken", b"[]"])
def test_load_prefs_bad_data_is_set_aside(stores, contents):
    path = stores["prefs"]
    path.write_bytes(contents)

    assert app.load_prefs() == {}
    backups = list(path.parent.glob("simple_rcon_tool.pref.bad-*"))
    assert not path.exists()
    assert len(backups) == 1
    assert backups[0].read_bytes() == contents


def test_load_prefs_valid_file_returns_dict(stores):
    path = stores["prefs"]
    path.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")

    assert app.load_prefs() == {"theme": "dark"}
