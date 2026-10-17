import json
import socket
import struct
import threading

import pytest

import simple_rcon_tool as app


SERVER = {
    "id": "srv-one",
    "name": "Test Server",
    "host": "127.0.0.1",
    "port": 27015,
    "game": "gmod",
    "buttons": [],
}


@pytest.fixture(autouse=True)
def server_file(monkeypatch, tmp_path):
    path = tmp_path / "servers.json"
    path.write_text(json.dumps([SERVER]), encoding="utf-8")
    monkeypatch.setattr(app, "SERVERS_FILE", str(path))
    monkeypatch.setattr(app, "_server_store_warning", None)
    return path


def _packet(req_id, type_, body):
    payload = struct.pack("<ii", req_id, type_) + body.encode("utf-8") + b"\x00\x00"
    return struct.pack("<i", len(payload)) + payload


class FakeSocket:
    def __init__(self, auth_id):
        self._reply = bytearray(_packet(auth_id, app.RconClient.SERVERDATA_AUTH_RESPONSE, ""))
        self.closed = False
        self.timeout = None

    def settimeout(self, timeout):
        self.timeout = timeout

    def sendall(self, _data):
        pass

    def recv(self, size):
        data = bytes(self._reply[:size])
        del self._reply[:size]
        return data

    def close(self):
        self.closed = True


class FakeSession:
    instances = []
    instance_created = threading.Event()

    def __init__(self, server, password):
        self.server = server
        self.password = password
        self.closed = False
        self.close_count = 0
        self.connect_started = threading.Event()
        self.connect_release = threading.Event()
        self.command_started = threading.Event()
        self.command_release = threading.Event()
        self.command_result = ""
        self.command_error = None
        self.block_connect = False
        self.block_command = False
        type(self).instances.append(self)
        type(self).instance_created.set()

    def connect(self):
        self.connect_started.set()
        if self.block_connect:
            assert self.connect_release.wait(3), "connect was not released"

    def command(self, _command):
        self.command_started.set()
        if self.block_command:
            assert self.command_release.wait(3), "command was not released"
        if self.command_error:
            raise self.command_error
        return self.command_result

    def close(self):
        self.closed = True
        self.close_count += 1


@pytest.fixture
def fake_session(monkeypatch):
    FakeSession.instances = []
    FakeSession.instance_created = threading.Event()
    monkeypatch.setattr(app, "Session", FakeSession)
    return FakeSession


def _record_events(api, monkeypatch):
    events = []
    debug = []
    debug_called = threading.Event()

    def record_debug(message):
        debug.append(message)
        debug_called.set()

    monkeypatch.setattr(api, "_emit", lambda channel, payload: events.append((channel, payload)))
    monkeypatch.setattr(app, "debug_log", record_debug)
    return events, debug, debug_called


def _join(thread):
    thread.join(timeout=3)
    assert not thread.is_alive(), "worker thread did not finish"


def test_session_connect_closes_and_wipes_password_after_auth_failure(monkeypatch):
    sock = FakeSocket(-1)
    monkeypatch.setattr(app.socket, "create_connection", lambda *_args, **_kwargs: sock)
    session = app.Session(SERVER, "wrong-password")

    with pytest.raises(app.RconError, match="Authentication failed"):
        session.connect()

    assert sock.closed
    assert session.client.sock is None
    assert session.client.password is None


def test_session_connect_wipes_password_when_socket_connection_fails(monkeypatch):
    monkeypatch.setattr(
        app.socket, "create_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionRefusedError()),
    )
    session = app.Session(SERVER, "password")

    with pytest.raises(ConnectionRefusedError):
        session.connect()

    assert session.client.sock is None
    assert session.client.password is None


def test_api_connect_auth_failure_leaves_no_session_and_wipes_password(monkeypatch):
    sock = FakeSocket(-1)
    monkeypatch.setattr(app.socket, "create_connection", lambda *_args, **_kwargs: sock)
    api = app.Api()

    result = api.connect(SERVER["id"], "wrong-password")

    assert result["ok"] is False
    assert api.sessions == {}
    assert sock.closed


@pytest.mark.parametrize("result", ["old output", None], ids=["success", "failure"])
def test_replaced_command_outcome_is_silent_and_keeps_new_session(
    monkeypatch, fake_session, result,
):
    api = app.Api()
    old = fake_session(SERVER, "old")
    old.block_command = True
    old.command_result = result or ""
    if result is None:
        old.command_error = app.RconError("old command failed")
    api.sessions[SERVER["id"]] = old
    events, debug, debug_called = _record_events(api, monkeypatch)

    assert api.send(SERVER["id"], "status") == {"ok": True}
    assert old.command_started.wait(3)
    assert api.disconnect(SERVER["id"]) == {"ok": True}
    assert api.connect(SERVER["id"], "new") == {"ok": True}
    new = api.sessions[SERVER["id"]]
    events.clear()
    debug.clear()
    debug_called.clear()
    old.command_release.set()

    assert debug_called.wait(3)

    assert api.sessions[SERVER["id"]] is new
    assert not new.closed
    assert events == []
    assert debug == ["Command outcome from replaced or closed session: srv-one"]


def test_disconnect_during_command_has_no_second_idle_event(monkeypatch, fake_session):
    api = app.Api()
    session = fake_session(SERVER, "password")
    session.block_command = True
    session.command_error = app.RconError("command failed")
    api.sessions[SERVER["id"]] = session
    events, debug, debug_called = _record_events(api, monkeypatch)

    assert api.send(SERVER["id"], "status") == {"ok": True}
    assert session.command_started.wait(3)
    api.disconnect(SERVER["id"])
    events.clear()
    debug.clear()
    debug_called.clear()
    session.command_release.set()
    assert debug_called.wait(3)

    assert events == []
    assert session.closed
    assert debug == ["Command outcome from replaced or closed session: srv-one"]


def test_delete_cancels_connect_before_it_can_install(monkeypatch, fake_session):
    api = app.Api()
    events, _debug, _debug_called = _record_events(api, monkeypatch)
    original_connect = FakeSession.connect

    def blocked_connect(self):
        self.block_connect = True
        return original_connect(self)

    monkeypatch.setattr(FakeSession, "connect", blocked_connect)
    result = []
    worker = threading.Thread(target=lambda: result.append(api.connect(SERVER["id"], "password")))
    worker.start()
    assert FakeSession.instance_created.wait(3)
    assert FakeSession.instances[0].connect_started.wait(3)

    assert api.delete_server(SERVER["id"])["ok"] is True
    FakeSession.instances[0].connect_release.set()
    _join(worker)

    assert result == [{"ok": False, "error": "Connection was cancelled."}]
    assert api.sessions == {}
    assert FakeSession.instances[0].closed
    assert events == [("status", {"serverId": "srv-one", "state": "idle"})]


def test_disconnect_all_cancels_pending_connect_without_a_session(monkeypatch, fake_session):
    api = app.Api()
    original_connect = FakeSession.connect

    def blocked_connect(self):
        self.block_connect = True
        return original_connect(self)

    monkeypatch.setattr(FakeSession, "connect", blocked_connect)
    result = []
    worker = threading.Thread(target=lambda: result.append(api.connect(SERVER["id"], "password")))
    worker.start()
    assert FakeSession.instance_created.wait(3)
    assert FakeSession.instances[0].connect_started.wait(3)

    assert api.disconnect_all() == {"ok": True}
    FakeSession.instances[0].connect_release.set()
    _join(worker)

    assert result == [{"ok": False, "error": "Connection was cancelled."}]
    assert api.sessions == {}
    assert FakeSession.instances[0].closed


def test_later_overlapping_connect_installs_and_earlier_session_closes(monkeypatch, fake_session):
    api = app.Api()
    original_connect = FakeSession.connect

    def first_connect_blocks(self):
        if self.password == "first":
            self.block_connect = True
        return original_connect(self)

    monkeypatch.setattr(FakeSession, "connect", first_connect_blocks)
    first_result = []
    first = threading.Thread(target=lambda: first_result.append(api.connect(SERVER["id"], "first")))
    first.start()
    assert FakeSession.instance_created.wait(3)
    assert FakeSession.instances[0].connect_started.wait(3)

    assert api.connect(SERVER["id"], "second") == {"ok": True}
    second = api.sessions[SERVER["id"]]
    FakeSession.instances[0].connect_release.set()
    _join(first)

    assert first_result == [{"ok": False, "error": "Connection was cancelled."}]
    assert api.sessions[SERVER["id"]] is second
    assert FakeSession.instances[0].closed
    assert not second.closed


def test_current_command_failure_logs_removes_closes_and_emits_idle(monkeypatch, fake_session):
    api = app.Api()
    session = fake_session(SERVER, "password")
    session.command_error = app.RconError("Connection closed by server.")
    api.sessions[SERVER["id"]] = session
    events, _debug, _debug_called = _record_events(api, monkeypatch)

    api._exec(SERVER["id"], session, "status", "Status")

    assert api.sessions == {}
    assert session.closed
    assert ("log", {
        "serverId": "srv-one", "text": "Connection closed by server.", "level": "err",
    }) in events
    assert ("log", {
        "serverId": "srv-one", "text": "Disconnected. Password cleared from memory.",
        "level": "muted",
    }) in events
    assert events[-1] == ("status", {"serverId": "srv-one", "state": "idle"})


def test_running_status_and_echo_are_sent_before_command_runs(monkeypatch, fake_session):
    api = app.Api()
    session = fake_session(SERVER, "password")
    session.block_command = True
    api.sessions[SERVER["id"]] = session
    events, _debug, _debug_called = _record_events(api, monkeypatch)

    assert api.send(SERVER["id"], "status", "Status") == {"ok": True}
    assert session.command_started.wait(3)

    assert events == [
        ("status", {"serverId": "srv-one", "state": "running"}),
        ("log", {"serverId": "srv-one", "text": "$ Status", "level": "cmd"}),
    ]
    session.command_release.set()


class FakeRconServer:
    def __init__(self):
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self.port = self._listener.getsockname()[1]
        self._listener.listen(1)
        self.error = None
        self.command_complete = threading.Event()
        self._thread = threading.Thread(target=self._serve)
        self._thread.start()

    @staticmethod
    def _recv_exact(conn, size):
        data = b""
        while len(data) < size:
            chunk = conn.recv(size - len(data))
            if not chunk:
                raise ConnectionError("client closed connection")
            data += chunk
        return data

    def _recv_packet(self, conn):
        (length,) = struct.unpack("<i", self._recv_exact(conn, 4))
        data = self._recv_exact(conn, length)
        return struct.unpack("<ii", data[:8])

    def _serve(self):
        try:
            conn, _ = self._listener.accept()
            with conn:
                auth_id, auth_type = self._recv_packet(conn)
                assert auth_type == app.RconClient.SERVERDATA_AUTH
                conn.sendall(_packet(auth_id, app.RconClient.SERVERDATA_AUTH_RESPONSE, ""))
                command_id, command_type = self._recv_packet(conn)
                sentinel_id, sentinel_type = self._recv_packet(conn)
                assert command_type == app.RconClient.SERVERDATA_EXECCOMMAND
                assert sentinel_type == app.RconClient.SERVERDATA_RESPONSE_VALUE
                conn.sendall(_packet(command_id, app.RconClient.SERVERDATA_RESPONSE_VALUE, "ok"))
                conn.sendall(_packet(sentinel_id, app.RconClient.SERVERDATA_RESPONSE_VALUE, ""))
                self.command_complete.set()
                try:
                    conn.recv(1)
                except ConnectionResetError:
                    pass
        except Exception as exc:
            self.error = exc
        finally:
            self._listener.close()

    def close(self):
        _join(self._thread)
        if self.error:
            raise self.error


def test_real_api_connect_send_and_disconnect_all_has_no_sessions(server_file):
    server = FakeRconServer()
    data = dict(SERVER, port=server.port)
    server_file.write_text(json.dumps([data]), encoding="utf-8")
    api = app.Api()

    assert api.connect(SERVER["id"], "password") == {"ok": True}
    assert api.send(SERVER["id"], "status") == {"ok": True}
    assert server.command_complete.wait(3)
    assert api.disconnect_all() == {"ok": True}
    server.close()
    assert api.sessions == {}
