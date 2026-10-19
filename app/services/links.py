import urllib.parse
import webbrowser

from app.services import diagnostics


JDE_PROJECTS_URL = "https://jde-projects.com"
ALLOWED_URL_HOST = "jde-projects.com"


def _is_allowed_url(url):
    """True only for a plain https address on the JDE-Projects website: no
    other host, scheme, port, login part, or whitespace/control characters."""
    if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
        return False
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    # Comparing the whole netloc rules out ports and user@host tricks.
    return parts.scheme == "https" and parts.netloc == ALLOWED_URL_HOST


def open_url(api, url):
    """Open a JDE-Projects website page in the user's default browser.
    Any other address is refused (see _is_allowed_url)."""
    if not _is_allowed_url(url):
        diagnostics.debug_log("open_url refused an address outside the allowed site")
        return {"ok": False, "error": "URL is not allowed."}
    try:
        if webbrowser.open(url):
            return {"ok": True}
    except Exception as e:
        diagnostics.debug_log(f"Could not open project URL: {e}")
        return {"ok": False, "error": "Could not open the browser."}
    return {"ok": False, "error": "Could not open the browser."}
