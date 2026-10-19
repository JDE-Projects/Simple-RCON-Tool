import os

import simple_rcon_tool as app
from app import paths
from app.services import diagnostics, prefs, servers

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _inside(path, folder):
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(folder)]) == os.path.abspath(folder)
    except ValueError:  # different drives
        return False


def test_every_data_location_points_at_the_temp_folder(isolated_app_data):
    assert paths.app_dir() == isolated_app_data
    assert paths.SERVERS_FILE == os.path.join(isolated_app_data, "servers.json")
    assert paths._pref_path() == os.path.join(isolated_app_data, "simple_rcon_tool.pref")
    assert diagnostics._debug_logger.log_dir == isolated_app_data
    assert not _inside(isolated_app_data, REPO_ROOT)


def test_default_writes_land_in_the_temp_folder(isolated_app_data):
    assert prefs.save_prefs({"theme": "light"})
    assert servers.save_servers([])
    export = app.Api().export_console("Isolation", "text")

    assert export["ok"] is True
    assert os.path.dirname(export["path"]) == isolated_app_data
    assert sorted(os.listdir(isolated_app_data)) == sorted(
        ["servers.json", "simple_rcon_tool.pref", export["name"]]
    )
