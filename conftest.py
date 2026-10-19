"""Shared pytest setup: keep every file the app writes inside a temp folder."""

import os

import pytest

from app import paths
from app.services import diagnostics


@pytest.fixture(autouse=True)
def isolated_app_data(tmp_path_factory, monkeypatch):
    """Point every place the app writes at a fresh temp folder.

    Covers the prefs file, servers.json, the debug log folder (logging and
    pruning) and console exports, which all resolve through paths.app_dir().
    A test that needs its own location can still patch these names itself.
    """
    data_dir = str(tmp_path_factory.mktemp("app_data"))
    monkeypatch.setattr(paths, "app_dir", lambda: data_dir)
    monkeypatch.setattr(paths, "SERVERS_FILE", os.path.join(data_dir, "servers.json"))
    monkeypatch.setattr(diagnostics._debug_logger, "log_dir", data_dir)
    return data_dir
