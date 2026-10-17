import socket
import struct
import threading
import time

import pytest

from simple_rcon_tool import (
    RconClient, RconConnectionClosed, RconError, Session, game_reply_ids_lag, game_uses_sentinel,
)


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
    def __init__(self, handler, *, auth_reply=None):
        self._handler = handler
        self._auth_reply = auth_reply
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
                auth_reply = self._auth_reply
                if auth_reply is None:
                    auth_reply = _packet(auth_id, RconClient.SERVERDATA_AUTH_RESPONSE, "")
                conn.sendall(auth_reply)
                self._handler(conn)
                try:
                    conn.recv(1)  # Keep the connection open until the client closes it.
                except ConnectionResetError:
                    pass  # The client may reject a header before reading its trailing bytes.
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

    def create(handler, *, sentinel=True, reply_ids_lag=False):
        server = FakeRconServer(handler)
        client = RconClient(
            "127.0.0.1", server.port, "password", timeout=1,
            sentinel=sentinel, reply_ids_lag=reply_ids_lag,
        )
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


def test_sentinel_mode_discards_valve_marker_tail_from_later_replies(client_factory):
    replies = ("first reply", "", "third reply")

    def handler(conn):
        for expected_command, reply in zip(("first", "second", "third"), replies, strict=True):
            _command_id, command_type, command = _recv_packet(conn)
            sentinel_id, sentinel_type, sentinel_body = _recv_packet(conn)
            assert command_type == RconClient.SERVERDATA_EXECCOMMAND
            assert command == expected_command
            assert sentinel_type == RconClient.SERVERDATA_RESPONSE_VALUE
            assert sentinel_body == ""
            if reply:
                conn.sendall(_packet(0, RconClient.SERVERDATA_RESPONSE_VALUE, reply))
            conn.sendall(_packet(sentinel_id, RconClient.SERVERDATA_RESPONSE_VALUE, ""))
            # The last tail is never read; sending it would make Windows reset the socket on close.
            if expected_command != "third":
                conn.sendall(_packet(sentinel_id, RconClient.SERVERDATA_RESPONSE_VALUE, "\x00\x01\x00\x00"))

    client = client_factory(handler)
    assert client.command("first") == "first reply"
    assert client.command("second") == ""
    assert client.command("third") == "third reply"


def test_sentinel_mode_empty_echo_does_not_wait_for_extra_packet(client_factory):
    def handler(conn):
        for expected_command in ("first", "second", "third"):
            command_id, command_type, command = _recv_packet(conn)
            sentinel_id, sentinel_type, sentinel_body = _recv_packet(conn)
            assert command_type == RconClient.SERVERDATA_EXECCOMMAND
            assert command == expected_command
            assert sentinel_type == RconClient.SERVERDATA_RESPONSE_VALUE
            assert sentinel_body == ""
            conn.sendall(_packet(command_id, RconClient.SERVERDATA_RESPONSE_VALUE, command))
            conn.sendall(_packet(sentinel_id, RconClient.SERVERDATA_RESPONSE_VALUE, ""))

    client = client_factory(handler)
    for command in ("first", "second", "third"):
        started = time.monotonic()
        assert client.command(command) == command
        assert time.monotonic() - started < 0.2


def test_sentinel_mode_discards_prior_marker_within_next_reply(client_factory):
    def handler(conn):
        first_command_id, _, _ = _recv_packet(conn)
        first_sentinel_id, _, _ = _recv_packet(conn)
        conn.sendall(_packet(first_command_id, RconClient.SERVERDATA_RESPONSE_VALUE, "first"))
        conn.sendall(_packet(first_sentinel_id, RconClient.SERVERDATA_RESPONSE_VALUE, ""))

        second_command_id, _, _ = _recv_packet(conn)
        second_sentinel_id, _, _ = _recv_packet(conn)
        conn.sendall(_packet(second_command_id, RconClient.SERVERDATA_RESPONSE_VALUE, "before"))
        conn.sendall(_packet(first_sentinel_id, RconClient.SERVERDATA_RESPONSE_VALUE, "discard"))
        conn.sendall(_packet(second_command_id, RconClient.SERVERDATA_RESPONSE_VALUE, "after"))
        conn.sendall(_packet(second_sentinel_id, RconClient.SERVERDATA_RESPONSE_VALUE, ""))

    client = client_factory(handler)
    assert client.command("first") == "first"
    assert client.command("second") == "beforeafter"


def test_sentinel_mode_no_reply_returns_none_and_restores_timeout(client_factory):
    def handler(conn):
        _recv_packet(conn)
        _recv_packet(conn)

    client = client_factory(handler)
    assert client.command("status") is None
    assert client.sock.gettimeout() == 1


def test_sentinel_echo_without_reply_returns_empty_string(client_factory):
    def handler(conn):
        _recv_packet(conn)
        sentinel_id, _, _ = _recv_packet(conn)
        conn.sendall(_packet(sentinel_id, RconClient.SERVERDATA_RESPONSE_VALUE, ""))

    client = client_factory(handler)
    assert client.command("status") == ""


def test_sentinel_mode_partial_reply_then_timeout_returns_partial_text(client_factory):
    def handler(conn):
        command_id, _, _ = _recv_packet(conn)
        _recv_packet(conn)
        conn.sendall(_packet(command_id, RconClient.SERVERDATA_RESPONSE_VALUE, "partial"))

    client = client_factory(handler)
    assert client.command("status") == "partial"


def test_sentinel_mode_stale_markers_then_timeout_return_none(client_factory):
    def handler(conn):
        first_id, _, _ = _recv_packet(conn)
        first_sentinel_id, _, _ = _recv_packet(conn)
        conn.sendall(_packet(first_id, RconClient.SERVERDATA_RESPONSE_VALUE, "first"))
        conn.sendall(_packet(first_sentinel_id, RconClient.SERVERDATA_RESPONSE_VALUE, ""))

        _recv_packet(conn)
        _recv_packet(conn)
        conn.sendall(_packet(first_sentinel_id, RconClient.SERVERDATA_RESPONSE_VALUE, "stale"))

    client = client_factory(handler)
    assert client.command("first") == "first"
    assert client.command("second") is None


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


def test_no_sentinel_no_reply_returns_none(client_factory):
    def handler(conn):
        _recv_packet(conn)

    client = client_factory(handler, sentinel=False)
    assert client.command("status") is None


def test_no_sentinel_empty_reply_returns_empty_string(client_factory):
    def handler(conn):
        command_id, _, _ = _recv_packet(conn)
        conn.sendall(_packet(command_id, RconClient.SERVERDATA_AUTH_RESPONSE, ""))

    client = client_factory(handler, sentinel=False)
    assert client.command("status") == ""


def test_no_sentinel_partial_reply_then_timeout_returns_partial_text(client_factory):
    def handler(conn):
        command_id, _, _ = _recv_packet(conn)
        conn.sendall(_packet(command_id, RconClient.SERVERDATA_AUTH_RESPONSE, "partial"))

    client = client_factory(handler, sentinel=False)
    assert client.command("status") == "partial"


def test_no_sentinel_joins_packets_within_idle_gap(client_factory):
    def handler(conn):
        command_id, _, _ = _recv_packet(conn)
        conn.sendall(_packet(command_id, RconClient.SERVERDATA_AUTH_RESPONSE, "first "))
        time.sleep(0.05)
        conn.sendall(_packet(command_id, RconClient.SERVERDATA_AUTH_RESPONSE, "second"))

    client = client_factory(handler, sentinel=False)
    assert client.command("status") == "first second"


def test_no_sentinel_lagged_reply_ids_work_after_auth_and_next_command(client_factory):
    def handler(conn):
        auth_id = 1
        first_id, _, _ = _recv_packet(conn)
        conn.sendall(_packet(auth_id, RconClient.SERVERDATA_AUTH_RESPONSE, "first"))
        _second_id, _, _ = _recv_packet(conn)
        conn.sendall(_packet(first_id, RconClient.SERVERDATA_AUTH_RESPONSE, "second"))

    client = client_factory(handler, sentinel=False, reply_ids_lag=True)
    assert client.command("first") == "first"
    assert client.command("second") == "second"


def test_no_sentinel_lagged_client_accepts_command_id(client_factory):
    def handler(conn):
        command_id, _, _ = _recv_packet(conn)
        conn.sendall(_packet(command_id, RconClient.SERVERDATA_AUTH_RESPONSE, "reply"))

    client = client_factory(handler, sentinel=False, reply_ids_lag=True)
    assert client.command("status") == "reply"


def test_no_sentinel_lagged_client_ignores_reply_two_ids_back(client_factory):
    def handler(conn):
        first_id, _, _ = _recv_packet(conn)
        conn.sendall(_packet(first_id, RconClient.SERVERDATA_AUTH_RESPONSE, "first"))
        second_id, _, _ = _recv_packet(conn)
        conn.sendall(_packet(1, RconClient.SERVERDATA_AUTH_RESPONSE, "too old"))
        conn.sendall(_packet(second_id, RconClient.SERVERDATA_AUTH_RESPONSE, "second"))

    client = client_factory(handler, sentinel=False, reply_ids_lag=True)
    assert client.command("first") == "first"
    assert client.command("second") == "second"


def test_no_sentinel_lagged_client_joins_packets_within_idle_gap(client_factory):
    def handler(conn):
        _command_id, _, _ = _recv_packet(conn)
        conn.sendall(_packet(1, RconClient.SERVERDATA_AUTH_RESPONSE, "first "))
        time.sleep(0.05)
        conn.sendall(_packet(1, RconClient.SERVERDATA_AUTH_RESPONSE, "second"))

    client = client_factory(handler, sentinel=False, reply_ids_lag=True)
    assert client.command("status") == "first second"


def test_no_sentinel_unflagged_client_ignores_lagged_reply_id(client_factory):
    def handler(conn):
        command_id, _, _ = _recv_packet(conn)
        conn.sendall(_packet(1, RconClient.SERVERDATA_AUTH_RESPONSE, "lagged"))
        conn.sendall(_packet(command_id, RconClient.SERVERDATA_AUTH_RESPONSE, "reply"))

    client = client_factory(handler, sentinel=False)
    assert client.command("status") == "reply"


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
    with pytest.raises(RconConnectionClosed, match="Connection closed by server"):
        client.command("status")


@pytest.mark.parametrize("sentinel", [True, False])
def test_negative_packet_length_raises_invalid_packet_without_waiting(client_factory, sentinel):
    def handler(conn):
        _recv_packet(conn)
        if sentinel:
            _recv_packet(conn)
        conn.sendall(struct.pack("<i", -1))

    client = client_factory(handler, sentinel=sentinel)
    started = time.monotonic()
    with pytest.raises(RconError, match="invalid packet"):
        client.command("status")
    assert time.monotonic() - started < 0.5


@pytest.mark.parametrize("sentinel", [True, False])
def test_too_small_packet_length_raises_invalid_packet(client_factory, sentinel):
    def handler(conn):
        _recv_packet(conn)
        if sentinel:
            _recv_packet(conn)
        conn.sendall(struct.pack("<i", 8) + b"\x00" * 8)

    client = client_factory(handler, sentinel=sentinel)
    with pytest.raises(RconError, match="invalid packet"):
        client.command("status")


@pytest.mark.parametrize("sentinel", [True, False])
def test_oversized_packet_length_raises_invalid_packet_without_body(client_factory, sentinel):
    def handler(conn):
        _recv_packet(conn)
        if sentinel:
            _recv_packet(conn)
        conn.sendall(struct.pack("<i", 65537))

    client = client_factory(handler, sentinel=sentinel)
    with pytest.raises(RconError, match="invalid packet"):
        client.command("status")


@pytest.mark.parametrize("sentinel", [True, False])
def test_truncated_valid_length_packet_raises_connection_closed(client_factory, sentinel):
    def handler(conn):
        _recv_packet(conn)
        if sentinel:
            _recv_packet(conn)
        response = struct.pack("<ii", 1, RconClient.SERVERDATA_RESPONSE_VALUE) + b"x"
        conn.sendall(struct.pack("<i", 10) + response)
        conn.shutdown(socket.SHUT_WR)

    client = client_factory(handler, sentinel=sentinel)
    with pytest.raises(RconConnectionClosed, match="Connection closed by server"):
        client.command("status")


@pytest.mark.parametrize("sentinel", [True, False])
def test_unterminated_packet_raises_invalid_packet(client_factory, sentinel):
    def handler(conn):
        command_id, _, _ = _recv_packet(conn)
        if sentinel:
            _recv_packet(conn)
        response = struct.pack("<ii", command_id, RconClient.SERVERDATA_RESPONSE_VALUE) + b"x"
        conn.sendall(struct.pack("<i", len(response)) + response)

    client = client_factory(handler, sentinel=sentinel)
    with pytest.raises(RconError, match="invalid packet"):
        client.command("status")


@pytest.mark.parametrize("sentinel", [True, False])
def test_single_null_terminator_is_accepted(client_factory, sentinel):
    def handler(conn):
        command_id, _, _ = _recv_packet(conn)
        if sentinel:
            sentinel_id, _, _ = _recv_packet(conn)
        response = struct.pack("<ii", command_id, RconClient.SERVERDATA_RESPONSE_VALUE) + b"reply\x00"
        conn.sendall(struct.pack("<i", len(response)) + response)
        if sentinel:
            conn.sendall(_packet(sentinel_id, RconClient.SERVERDATA_RESPONSE_VALUE, ""))

    client = client_factory(handler, sentinel=sentinel)
    assert client.command("status") == "reply"


@pytest.mark.parametrize("sentinel", [True, False])
def test_minimum_packet_length_with_single_null_is_accepted(client_factory, sentinel):
    def handler(conn):
        command_id, _, _ = _recv_packet(conn)
        if sentinel:
            sentinel_id, _, _ = _recv_packet(conn)
        response = struct.pack("<ii", command_id, RconClient.SERVERDATA_RESPONSE_VALUE) + b"\x00"
        conn.sendall(struct.pack("<i", len(response)) + response)
        if sentinel:
            conn.sendall(_packet(sentinel_id, RconClient.SERVERDATA_RESPONSE_VALUE, ""))

    client = client_factory(handler, sentinel=sentinel)
    assert client.command("status") == ""


@pytest.mark.parametrize("sentinel", [True, False])
def test_4096_byte_body_is_accepted(client_factory, sentinel):
    body = "x" * 4096

    def handler(conn):
        command_id, _, _ = _recv_packet(conn)
        if sentinel:
            sentinel_id, _, _ = _recv_packet(conn)
        conn.sendall(_packet(command_id, RconClient.SERVERDATA_RESPONSE_VALUE, body))
        if sentinel:
            conn.sendall(_packet(sentinel_id, RconClient.SERVERDATA_RESPONSE_VALUE, ""))

    client = client_factory(handler, sentinel=sentinel)
    assert client.command("status") == body


def test_auth_invalid_packet_length_raises_invalid_packet():
    server = FakeRconServer(lambda conn: None, auth_reply=struct.pack("<i", -1))
    client = RconClient("127.0.0.1", server.port, "password", timeout=1)
    try:
        with pytest.raises(RconError, match="invalid packet"):
            client.connect()
    finally:
        client.close()
        server.close()


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
    assert game_uses_sentinel("minecraft") is False
    assert game_uses_sentinel("conan") is False
    assert game_uses_sentinel("gmod") is True
    assert game_uses_sentinel("unknown") is True


def test_game_reply_ids_lag_for_conan_and_other_games():
    assert game_reply_ids_lag("conan") is True
    assert game_reply_ids_lag("valheim") is False
    assert game_reply_ids_lag("minecraft") is False
    assert game_reply_ids_lag("unknown") is False


@pytest.mark.parametrize(
    ("game", "expected"),
    [
        ("valheim", False), ("minecraft", False), ("gmod", True),
    ],
)
def test_session_passes_game_sentinel_flag_to_client(game, expected):
    server = {"host": "127.0.0.1", "port": 27015, "game": game}
    assert Session(server, "password").client.sentinel is expected


def test_session_passes_game_reply_ids_lag_flag_to_client():
    server = {"host": "127.0.0.1", "port": 27015, "game": "conan"}
    assert Session(server, "password").client.reply_ids_lag is True
