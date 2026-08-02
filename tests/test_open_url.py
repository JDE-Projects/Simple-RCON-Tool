from unittest.mock import patch

from simple_rcon_tool import Api, JDE_PROJECTS_URL


def test_open_url_opens_the_approved_project_url():
    with patch("simple_rcon_tool.webbrowser.open", return_value=True) as open_browser:
        result = Api().open_url(JDE_PROJECTS_URL)

    assert result == {"ok": True}
    open_browser.assert_called_once_with(JDE_PROJECTS_URL)


def test_open_url_rejects_any_other_url():
    with patch("simple_rcon_tool.webbrowser.open") as open_browser:
        result = Api().open_url("https://example.com")

    assert result == {"ok": False, "error": "URL is not allowed."}
    open_browser.assert_not_called()


def test_open_url_handles_browser_exception():
    with patch("simple_rcon_tool.webbrowser.open", side_effect=OSError):
        result = Api().open_url(JDE_PROJECTS_URL)

    assert result == {"ok": False, "error": "Could not open the browser."}


def test_open_url_handles_browser_declining_to_open():
    with patch("simple_rcon_tool.webbrowser.open", return_value=False):
        result = Api().open_url(JDE_PROJECTS_URL)

    assert result == {"ok": False, "error": "Could not open the browser."}
