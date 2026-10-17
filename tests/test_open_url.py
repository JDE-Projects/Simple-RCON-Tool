from unittest.mock import patch

import pytest

from simple_rcon_tool import Api, JDE_PROJECTS_URL, _is_allowed_url


ALLOWED = [
    "https://jde-projects.com",
    "https://jde-projects.com/",
    "https://jde-projects.com/support/#commercial-licensing",
]

REFUSED = [
    "http://jde-projects.com",
    "https://github.com/JDE-Projects",
    "https://evil.example",
    "https://jde-projects.com.evil.example",
    "https://evil.example/jde-projects.com",
    "https://jde-projects.com@evil.example",
    "https://user@jde-projects.com",
    "https://jde-projects.com:8443/",
    "https://sub.jde-projects.com",
    "https://www.jde-projects.com",
    "file:///C:/Windows/System32/calc.exe",
    "javascript:alert(1)",
    "jde-projects.com",
    "https://jde-projects.com/\nhttps://evil.example",
    " https://jde-projects.com",
    "",
    None,
    123,
]


def test_header_url_is_the_website():
    assert JDE_PROJECTS_URL == "https://jde-projects.com"


@pytest.mark.parametrize("url", ALLOWED)
def test_is_allowed_url_accepts_website(url):
    assert _is_allowed_url(url) is True


@pytest.mark.parametrize("url", REFUSED)
def test_is_allowed_url_refuses_everything_else(url):
    assert _is_allowed_url(url) is False


@pytest.mark.parametrize("url", ALLOWED)
def test_open_url_opens_website_pages(url):
    with patch("simple_rcon_tool.webbrowser.open", return_value=True) as open_browser:
        result = Api().open_url(url)

    assert result == {"ok": True}
    open_browser.assert_called_once_with(url)


@pytest.mark.parametrize("url", REFUSED)
def test_open_url_refuses_without_calling_the_browser(url):
    with patch("simple_rcon_tool.webbrowser.open") as open_browser, \
            patch("simple_rcon_tool.debug_log") as log:
        result = Api().open_url(url)

    assert result == {"ok": False, "error": "URL is not allowed."}
    open_browser.assert_not_called()
    log.assert_called_once()


def test_open_url_handles_browser_exception():
    with patch("simple_rcon_tool.webbrowser.open", side_effect=OSError):
        result = Api().open_url(JDE_PROJECTS_URL)

    assert result == {"ok": False, "error": "Could not open the browser."}


def test_open_url_handles_browser_declining_to_open():
    with patch("simple_rcon_tool.webbrowser.open", return_value=False):
        result = Api().open_url(JDE_PROJECTS_URL)

    assert result == {"ok": False, "error": "Could not open the browser."}
