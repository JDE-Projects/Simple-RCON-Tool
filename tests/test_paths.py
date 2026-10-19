"""Where the app looks for its files: the repo root in source runs, next to
the exe (writable data) and inside the bundle (UI files) when frozen."""

import importlib.util
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATHS_FILE = os.path.join(REPO_ROOT, "app", "paths.py")


@pytest.fixture
def fresh_paths():
    # The shared fixture in conftest.py redirects app.paths.app_dir to a temp
    # folder, so load a separate copy of the module to test the real functions.
    spec = importlib.util.spec_from_file_location("paths_under_test", PATHS_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_run_uses_repo_root(fresh_paths, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert fresh_paths.app_dir() == REPO_ROOT
    assert fresh_paths.resource_path("simple_rcon_tool-UI.html") == os.path.join(
        REPO_ROOT, "simple_rcon_tool-UI.html")
    assert os.path.isfile(fresh_paths.resource_path("simple_rcon_tool-UI.html"))
    assert fresh_paths._pref_path() == os.path.join(REPO_ROOT, "simple_rcon_tool.pref")
    assert fresh_paths.SERVERS_FILE == os.path.join(REPO_ROOT, "servers.json")


def test_frozen_run_splits_data_and_bundle(fresh_paths, monkeypatch, tmp_path):
    exe_dir = tmp_path / "Simple RCON Tool"
    bundle_dir = exe_dir / "_internal"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "Simple RCON Tool.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_dir), raising=False)
    assert fresh_paths.app_dir() == str(exe_dir)
    assert fresh_paths._pref_path() == os.path.join(str(exe_dir), "simple_rcon_tool.pref")
    assert fresh_paths.resource_path("simple_rcon_tool-UI.html") == os.path.join(
        str(bundle_dir), "simple_rcon_tool-UI.html")
