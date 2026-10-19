import urllib.parse


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
