import socket
import struct
import threading
import time

from app.services import catalog


# ----------------------------------------------------------------------------
# Source RCON client (standard library only)
# ----------------------------------------------------------------------------

RCON_REPLY_IDLE_GAP = 0.3


class RconError(Exception):
    pass


class RconConnectionClosed(RconError):
    pass


def is_stop_command(game_key, command):
    # Matches on command text, so a typed stop command counts the same as the button.
    game = catalog.CATALOG_BY_KEY.get(game_key)
    if not game:
        return False
    command = command.strip().casefold()
    return any(
        button.get("stops_server") and button["command"].strip().casefold() == command
        for button in game["buttons"]
    )


def stop_outcome(reply, error):
    if isinstance(error, (RconConnectionClosed, ConnectionResetError, ConnectionAbortedError)):
        return "expected_disconnect"
    if error is not None:
        return "failed"
    if reply is None:
        return "no_reply"
    return "replied"


class _RconIdleTimeout(Exception):
    pass


class RconClient:
    SERVERDATA_EVENT = 1
    SERVERDATA_AUTH = 3
    SERVERDATA_AUTH_RESPONSE = 2
    SERVERDATA_EXECCOMMAND = 2
    SERVERDATA_RESPONSE_VALUE = 0
    # 9 is ID + type + one null terminator. CS2 sends cvarlist as one packet of about 350 KB;
    # 4 MiB leaves room for that and still stops absurd waits.
    MIN_PACKET_LENGTH = 9
    MAX_PACKET_LENGTH = 4 * 1024 * 1024

    def __init__(
        self, host, port, password, timeout=8, *, sentinel=True, reply_ids_lag=False,
        ignore_event_packets=False,
    ):
        self.host = host
        self.port = int(port)
        self.password = password  # memory only
        self.timeout = timeout
        self.sentinel = sentinel
        self.reply_ids_lag = reply_ids_lag
        self.ignore_event_packets = ignore_event_packets
        self.sock = None
        self._id = 0
        self._last_sent_id = None
        self._sentinel_ids = set()

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        self._authenticate()

    def _next_id(self):
        self._id += 1
        return self._id

    def _send(self, type_, body):
        req_id = self._next_id()
        payload = struct.pack("<ii", req_id, type_) + body.encode("utf-8") + b"\x00\x00"
        packet = struct.pack("<i", len(payload)) + payload
        self.sock.sendall(packet)
        self._last_sent_id = req_id
        return req_id

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise RconConnectionClosed("Connection closed by server.")
            buf += chunk
        return buf

    def _recv_packet(self):
        (length,) = struct.unpack("<i", self._recv_exact(4))
        return self._read_packet_body(length)

    def _read_packet_body(self, length):
        if not self.MIN_PACKET_LENGTH <= length <= self.MAX_PACKET_LENGTH:
            raise RconError("The server sent an invalid packet.")
        data = self._recv_exact(length)
        req_id, type_ = struct.unpack("<ii", data[:8])
        tail = data[8:]
        if tail.endswith(b"\x00\x00"):
            body = tail[:-2]
        elif tail.endswith(b"\x00"):
            body = tail[:-1]
        else:
            raise RconError("The server sent an invalid packet.")
        return req_id, type_, body.decode("utf-8", errors="replace")

    def _recv_command_packet(self):
        header = self.sock.recv(4)
        if not header:
            raise RconConnectionClosed("Connection closed by server.")
        return self._recv_packet_from_header(header)

    def _recv_packet_from_header(self, header):
        try:
            header += self._recv_exact(4 - len(header))
            (length,) = struct.unpack("<i", header)
            return self._read_packet_body(length)
        except socket.timeout as exc:
            raise RconError("Timed out while receiving packet.") from exc

    def _recv_packet_after_idle_gap(self, idle_timeout, normal_timeout):
        self.sock.settimeout(idle_timeout)
        try:
            try:
                header = self.sock.recv(4)
            except socket.timeout as exc:
                raise _RconIdleTimeout from exc
        finally:
            self.sock.settimeout(normal_timeout)
        if not header:
            raise RconConnectionClosed("Connection closed by server.")
        return self._recv_packet_from_header(header)

    def _authenticate(self):
        self._send(self.SERVERDATA_AUTH, self.password)
        try:
            while True:
                req_id, type_, _ = self._recv_packet()
                if type_ == self.SERVERDATA_AUTH_RESPONSE:
                    if req_id == -1:
                        raise RconError("Authentication failed (wrong RCON password).")
                    return
                # ignore the mirrored empty packet some servers send first
        except RconConnectionClosed as exc:
            raise RconError(
                "The server closed the connection during login. The RCON password may be wrong."
            ) from exc

    def command(self, cmd):
        previous_id = self._last_sent_id
        command_id = self._send(self.SERVERDATA_EXECCOMMAND, cmd)
        parts = []
        received_reply = False
        normal_timeout = self.sock.gettimeout()
        try:
            if self.sentinel:
                sentinel_id = self._send(self.SERVERDATA_RESPONSE_VALUE, "")
                self._sentinel_ids.add(sentinel_id)
                try:
                    while True:
                        req_id, type_, body = self._recv_command_packet()
                        if self.ignore_event_packets and type_ == self.SERVERDATA_EVENT:
                            continue
                        if req_id == sentinel_id:
                            break
                        if req_id in self._sentinel_ids:
                            continue
                        # Any other id counts as reply text: not every server
                        # echoes the command's id.
                        received_reply = True
                        parts.append(body)
                except socket.timeout:
                    if not received_reply:
                        return None
            else:
                accepted_ids = {command_id}
                if self.reply_ids_lag and previous_id is not None:
                    accepted_ids.add(previous_id)
                try:
                    while True:
                        req_id, type_, body = self._recv_command_packet()
                        if self.ignore_event_packets and type_ == self.SERVERDATA_EVENT:
                            continue
                        if req_id in accepted_ids:
                            received_reply = True
                            parts.append(body)
                            break
                except socket.timeout:
                    return "".join(parts).strip() if received_reply else None

                idle_deadline = time.monotonic() + RCON_REPLY_IDLE_GAP
                while True:
                    idle_timeout = idle_deadline - time.monotonic()
                    if idle_timeout <= 0:
                        break
                    try:
                        req_id, type_, body = self._recv_packet_after_idle_gap(
                            idle_timeout, normal_timeout
                        )
                    except _RconIdleTimeout:
                        break
                    if self.ignore_event_packets and type_ == self.SERVERDATA_EVENT:
                        continue
                    if req_id in accepted_ids:
                        parts.append(body)
                        idle_deadline = time.monotonic() + RCON_REPLY_IDLE_GAP
        finally:
            self.sock.settimeout(normal_timeout)
        return "".join(parts).strip()

    def close(self):
        self.password = None  # wipe from memory
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None


# ----------------------------------------------------------------------------
# A single live RCON session (one per connected server)
# ----------------------------------------------------------------------------

class Session:
    def __init__(self, server, password):
        self.server = server
        self.client = RconClient(
            server["host"], server["port"], password,
            sentinel=catalog.game_uses_sentinel(server.get("game")),
            reply_ids_lag=catalog.game_reply_ids_lag(server.get("game")),
            ignore_event_packets=catalog.game_ignores_event_packets(server.get("game")),
        )
        self.lock = threading.Lock()  # one command at a time per socket

    def connect(self):
        try:
            self.client.connect()
        except Exception:
            self.close()
            raise

    def command(self, cmd):
        with self.lock:
            return self.client.command(cmd)

    def close(self):
        try:
            self.client.close()
        except Exception:
            pass
