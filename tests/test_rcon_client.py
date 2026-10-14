import socket
import struct
import threading
import time

import pytest

from simple_rcon_tool import RconClient, RconError, Session, game_uses_sentinel


def _packet(req_id, type_, body):
    payload = struct.pack("<ii", req_id, type_) + body.encode("utf-8") + b"\x00\x00"
    return struct.pack("<i", len(payload)) + payload


def _recv_exact(sock, size):
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("client closed connection")
        data += chunk
    return data


def _recv_packet(sock):
    (length,) = struct.unpack("<i", _recv_exact(sock, 4))
    data = _recv_exact(sock, length)
    req_id, type_ = struct.unpack("<ii", data[:8])
    return req_id, type_, data[8:-2].decode("utf-8")


class FakeRconServer:
    def __init__(self, handler):
        self._handler = handler
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self.port = self._listener.getsockname()[1]
        self._listener.listen(1)
        self.error = None
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        try:
            conn, _ = self._listener.accept()
            with conn:
                auth_id, auth_type, _ = _recv_packet(conn)
                assert auth_type == RconClient.SERVERDATA_AUTH
                conn.sendall(_packet(auth_id, RconClient.SERVERDATA_AUTH_RESPONSE, ""))
                self._handler(conn)
                conn.recv(1)  # Keep the connection open until the client closes it.
        except Exception as exc:
            self.error = exc
        finally:
            self._listener.close()

    def close(self):
        self._thread.join(timeout=3)
        assert not self._thread.is_alive(), "fake RCON server did not finish"
        if self.error:
            raise self.error


@pytest.fixture
def client_factory():
    clients = []
    servers = []

    def create(handler, *, sentinel=True):
        server = FakeRconServer(handler)
        client = RconClient("127.0.0.1", server.port, "password", timeout=1, sentinel=sentinel)
        client.connect()
        clients.append(client)
        servers.append(server)
        return client

    yield create

    for client in clients:
        client.close()
    for server in servers:
        server.close()


@pytest.mark.parametrize("bodies", [("reply",), ("first ", "second")])
def test_sentinel_mode_collects_command_reply_packets(client_factory, bodies):
    def handler(conn):
        command_id, command_type, _ = _recv_packet(conn)
        sentinel_id, sentinel_type, sentinel_body = _recv_packet(conn)
        assert command_type == RconClient.SERVERDATA_EXECCOMMAND
        assert sentinel_type == RconClient.SERVERDATA_RESPONSE_VALUE
        assert sentinel_body == ""
        for body in bodies:
            conn.sendall(_packet(command_id, RconClient.SERVERDATA_RESPONSE_VALUE, body))
        conn.sendall(_packet(sentinel_id, RconClient.SERVERDATA_RESPONSE_VALUE, ""))

    client = client_factory(handler)
    assert client.command("status") == "".join(bodies).strip()


def test_sentinel_mode_keeps_reply_with_other_id(client_factory):
    def handler(conn):
        _recv_packet(conn)
        sentinel_id, _, _ = _recv_packet(conn)
        conn.sendall(_packet(0, RconClient.SERVERDATA_RESPONSE_VALUE, "reply"))
        conn.sendall(_packet(sentinel_id, RconClient.SERVERDATA_RESPONSE_VALUE, ""))

    client = client_factory(handler)
    assert client.command("status") == "reply"


def test_no_sentinel_sends_no_sentinel_and_reuses_connection(client_factory):
    def handler(conn):
        for expected in ("first", "second"):
            command_id, command_type, command = _recv_packet(conn)
            assert command_type == RconClient.SERVERDATA_EXECCOMMAND
            assert command == expected
            conn.sendall(_packet(command_id, RconClient.SERVERDATA_AUTH_RESPONSE, expected))

    client = client_factory(handler, sentinel=False)
    assert client.command("first") == "first"
    assert client.command("second") == "second"


def test_no_sentinel_joins_packets_within_idle_gap(client_factory):
    def handler(conn):
        command_id, _, _ = _recv_packet(conn)
        conn.sendall(_packet(command_id, RconClient.SERVERDATA_AUTH_RESPONSE, "first "))
        time.sleep(0.05)
        conn.sendall(_packet(command_id, RconClient.SERVERDATA_AUTH_RESPONSE, "second"))

    client = client_factory(handler, sentinel=False)
    assert client.command("status") == "first second"


def test_no_sentinel_excludes_late_reply_from_next_command(client_factory):
    def handler(conn):
        first_id, _, _ = _recv_packet(conn)
        conn.sendall(_packet(first_id, RconClient.SERVERDATA_AUTH_RESPONSE, "first"))
        time.sleep(0.4)
        conn.sendall(_packet(first_id, RconClient.SERVERDATA_AUTH_RESPONSE, "late"))
        second_id, _, _ = _recv_packet(conn)
        conn.sendall(_packet(second_id, RconClient.SERVERDATA_AUTH_RESPONSE, "second"))

    client = client_factory(handler, sentinel=False)
    assert client.command("first") == "first"
    assert client.command("second") == "second"


@pytest.mark.parametrize("split_at", [2, 12])
def test_no_sentinel_reads_packet_split_beyond_idle_gap(client_factory, split_at):
    def handler(conn):
        command_id, _, _ = _recv_packet(conn)
        conn.sendall(_packet(command_id, RconClient.SERVERDATA_AUTH_RESPONSE, "first"))
        time.sleep(0.05)
        response = _packet(command_id, RconClient.SERVERDATA_AUTH_RESPONSE, "second")
        conn.sendall(response[:split_at])
        time.sleep(0.4)
        conn.sendall(response[split_at:])

    client = client_factory(handler, sentinel=False)
    assert client.command("status") == "firstsecond"


@pytest.mark.parametrize("sentinel", [True, False])
def test_close_mid_packet_raises_rcon_error(client_factory, sentinel):
    def handler(conn):
        command_id, _, _ = _recv_packet(conn)
        response = _packet(command_id, RconClient.SERVERDATA_AUTH_RESPONSE, "reply")
        conn.sendall(response[:8])
        conn.shutdown(socket.SHUT_WR)

    client = client_factory(handler, sentinel=sentinel)
    with pytest.raises(RconError, match="Connection closed by server"):
        client.command("status")


@pytest.mark.parametrize("sentinel", [True, False])
def test_timeout_mid_packet_raises_rcon_error(client_factory, sentinel):
    def handler(conn):
        command_id, _, _ = _recv_packet(conn)
        response = _packet(command_id, RconClient.SERVERDATA_AUTH_RESPONSE, "reply")
        conn.sendall(response[:8])
        time.sleep(1.1)

    client = client_factory(handler, sentinel=sentinel)
    with pytest.raises(RconError, match="Timed out while receiving packet"):
        client.command("status")


def test_command_restores_socket_timeout(client_factory):
    def handler(conn):
        command_id, _, _ = _recv_packet(conn)
        conn.sendall(_packet(command_id, RconClient.SERVERDATA_AUTH_RESPONSE, "reply"))

    client = client_factory(handler, sentinel=False)
    assert client.command("status") == "reply"
    assert client.sock.gettimeout() == client.timeout


def test_game_uses_sentinel_for_flagged_source_and_unknown_games():
    assert game_uses_sentinel("valheim") is False
    assert game_uses_sentinel("windrose") is False
    assert game_uses_sentinel("factorio") is False
    assert game_uses_sentinel("gmod") is True
    assert game_uses_sentinel("unknown") is True


@pytest.mark.parametrize(
    ("game", "expected"),
    [("valheim", False), ("windrose", False), ("factorio", False), ("gmod", True)],
)
def test_session_passes_game_sentinel_flag_to_client(game, expected):
    server = {"host": "127.0.0.1", "port": 27015, "game": game}
    assert Session(server, "password").client.sentinel is expected
