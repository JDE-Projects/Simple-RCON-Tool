import json
import socket
import ssl
import urllib.error

from simple_rcon_tool import _update_error_reason


def _http_error(code):
    return urllib.error.HTTPError("https://api.github.com/x", code, "msg", {}, None)


def test_reason_for_http_403():
    assert _update_error_reason(_http_error(403)) == (
        "GitHub is rate-limiting update checks from this network. "
        "Try again later."
    )


def test_reason_for_http_404():
    assert _update_error_reason(_http_error(404)) == "No published release was found."


def test_reason_for_http_5xx():
    assert _update_error_reason(_http_error(503)) == (
        "GitHub is having trouble on its end (HTTP 503)."
    )


def test_reason_for_other_http_error():
    assert _update_error_reason(_http_error(418)) == "GitHub returned an error (HTTP 418)."


def test_reason_for_json_decode_error():
    exc = json.JSONDecodeError("Expecting value", "not json", 0)
    assert _update_error_reason(exc) == (
        "GitHub returned something unexpected. This often means a proxy "
        "or a guest wifi sign-in page answered instead."
    )


def test_reason_for_ssl_cert_verification_error():
    exc = urllib.error.URLError(ssl.SSLCertVerificationError("cert verify failed"))
    assert _update_error_reason(exc) == (
        "GitHub's certificate could not be verified. This usually means "
        "antivirus or a network filter is inspecting HTTPS traffic."
    )


def test_reason_for_ssl_eof_error():
    exc = urllib.error.URLError(ssl.SSLEOFError("eof"))
    assert _update_error_reason(exc) == (
        "The secure connection was cut off during the handshake with GitHub."
    )


def test_reason_for_ssl_zero_return_error():
    exc = urllib.error.URLError(ssl.SSLZeroReturnError("zero return"))
    assert _update_error_reason(exc) == (
        "The secure connection was cut off during the handshake with GitHub."
    )


def test_reason_for_generic_ssl_error():
    exc = urllib.error.URLError(ssl.SSLError("ssl failure"))
    assert _update_error_reason(exc) == "The secure connection to GitHub failed."


def test_reason_for_dns_failure():
    exc = urllib.error.URLError(socket.gaierror("name resolution failed"))
    assert _update_error_reason(exc) == (
        "The address for api.github.com could not be looked up. Check "
        "DNS or the internet connection."
    )


def test_reason_for_socket_timeout():
    exc = urllib.error.URLError(socket.timeout("timed out"))
    assert _update_error_reason(exc) == "GitHub didn't respond in time."


def test_reason_for_timeout_error():
    exc = urllib.error.URLError(TimeoutError("timed out"))
    assert _update_error_reason(exc) == "GitHub didn't respond in time."


def test_reason_for_connection_refused():
    exc = urllib.error.URLError(ConnectionRefusedError("refused"))
    assert _update_error_reason(exc) == (
        "The connection was refused or reset. A firewall or proxy may "
        "be blocking it."
    )


def test_reason_for_connection_reset():
    exc = urllib.error.URLError(ConnectionResetError("reset"))
    assert _update_error_reason(exc) == (
        "The connection was refused or reset. A firewall or proxy may "
        "be blocking it."
    )


def test_reason_for_network_unreachable():
    import errno
    cause = OSError("unreachable")
    cause.errno = errno.ENETUNREACH
    exc = urllib.error.URLError(cause)
    assert _update_error_reason(exc) == "No network connection."


def test_reason_for_plain_url_error_fallback():
    exc = urllib.error.URLError("some odd reason")
    assert _update_error_reason(exc) == "Couldn't reach GitHub. Check the internet connection."


def test_reason_for_generic_fallback():
    exc = ValueError("something else entirely broke")
    assert _update_error_reason(exc) == "ValueError: something else entirely broke"


def test_reason_for_generic_fallback_truncates_long_messages():
    exc = ValueError("x" * 200)
    reason = _update_error_reason(exc)
    assert len(reason) == 120
    assert reason.endswith("...")
    assert reason.startswith("ValueError: ")
