import os
import sys


# ----------------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------------

def app_dir():
    """Folder for servers.json. Sits next to the .exe when frozen."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_path(name):
    """Locate bundled files whether running as a script or a PyInstaller exe."""
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


SERVERS_FILE = os.path.join(app_dir(), "servers.json")


def _pref_path() -> str:
    return os.path.join(app_dir(), "simple_rcon_tool.pref")
