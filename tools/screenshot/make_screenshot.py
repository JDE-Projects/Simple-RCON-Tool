#!/usr/bin/env python3
"""Regenerate screenshots/rcon-light-dark.png.

Simple RCON Tool is a desktop app: at screenshot time there is no pywebview
window and no Python backend to talk to. Its UI is a self-contained HTML file
that already degrades gracefully outside pywebview (every backend call is
guarded by `if (!window.pywebview) return;`), so this tool serves the page and
its assets from a temp folder, seeds a sample fleet of servers straight into
the page's own SERVERS/CATALOG globals, and drives render() to produce the
picture.

Nothing here touches the working copy. The UI file, the icon, and the fonts
folder are copied into a temp folder and served from there; the real files are
only ever read, never written. The game catalog is read straight out of
simple_rcon_tool.py (via ast, no import) so the buttons shown can never drift
from the app's own catalog.

    python tools/screenshot/make_screenshot.py

Options:
    --keep            leave the temp folder in place for inspection
    --build-tools P   path to the build-tools repo (default: sibling folder)

Requires (see tools/screenshot/README.md for the full breakdown):
    - Node 22+ and an installed Edge or Chrome (for build-tools/screenshot/capture.mjs)
    - Pillow, for build-tools/screenshot/compose.py (not part of this repo's
      own requirements; run this script with a Python that has it, e.g. the
      system Python, rather than installing Pillow into the app's own venv)
"""

import ast
import http.server
import json
import os
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scene  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))

OUT_IMAGE = os.path.join(REPO_ROOT, "screenshots", "rcon-light-dark.png")

# Each theme is laid out at this size and captured at half scale, giving two
# 900x??? halves and the composite the README uses. Height is tuned to close
# just under the last panel, before the version bar, with no empty band below.
LAYOUT_WIDTH = 1800
LAYOUT_HEIGHT = 720
CAPTURE_SCALE = 0.5


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def read_source() -> str:
    path = os.path.join(REPO_ROOT, "simple_rcon_tool.py")
    with open(path, encoding="utf-8") as f:
        return f.read()


def read_app_version(source: str) -> str:
    match = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', source)
    if not match:
        fail("could not find APP_VERSION in simple_rcon_tool.py")
    return match.group(1)


def read_game_catalog(source: str) -> list:
    """Pull GAME_CATALOG out of simple_rcon_tool.py without importing the
    module (which pulls in pywebview/PySide6). It is a plain list literal, so
    ast.literal_eval on the sliced source is enough."""
    start = source.index("GAME_CATALOG = [") + len("GAME_CATALOG = ")
    end = source.index("\nCATALOG_BY_KEY", start)
    try:
        return ast.literal_eval(source[start:end])
    except (ValueError, SyntaxError) as exc:
        fail(f"could not parse GAME_CATALOG: {exc}")


def stage_ui(temp_dir: str) -> None:
    """Copy just what the page needs into temp_dir."""
    shutil.copy2(os.path.join(REPO_ROOT, "simple_rcon_tool-UI.html"),
                 os.path.join(temp_dir, "index.html"))
    shutil.copy2(os.path.join(REPO_ROOT, "simple_rcon_tool.png"), temp_dir)
    shutil.copytree(os.path.join(REPO_ROOT, "fonts"),
                     os.path.join(temp_dir, "fonts"))


def build_setup_script(version: str, catalog: list) -> str:
    """JavaScript that seeds CATALOG/SERVERS and drives the page's own render
    path, then marks one server connected and plays back a sample console
    session.

    The UI's boot() only runs on the pywebviewready event, which never fires
    in a plain browser, so this calls the same steps boot() would: it sets
    CATALOG/CAT_BY_KEY/SERVERS, the version bar text, populateGameSelect(),
    and render(), each guarded so the script still works if a function is
    renamed or removed.
    """
    parts = [
        f"CATALOG = {json.dumps(catalog)};",
        "CAT_BY_KEY = Object.fromEntries(CATALOG.map(g => [g.key, g]));",
        f"SERVERS = {json.dumps(scene.SERVERS)};",
        f"document.getElementById('verLabel').textContent = 'v' + {json.dumps(version)};",
        "if (typeof populateGameSelect === 'function') populateGameSelect();",
        "if (typeof render === 'function') render();",
        # Show one server already connected, console populated, rather than
        # an empty idle fleet.
        "var __c = document.querySelector('[data-card=\"srv-ark\"]');",
        "if (__c) __c.classList.add('connected');",
    ]
    for text, level, server_id in scene.CONSOLE_LINES:
        srv = next((s for s in scene.SERVERS if s["id"] == server_id), None)
        srv_name = srv["name"] if srv else None
        parts.append(
            "if (typeof addLine === 'function') addLine("
            f"{json.dumps(text)}, {json.dumps(level)}, "
            f"{json.dumps(srv_name)}, {json.dumps(server_id)});"
        )
    return "\n".join(parts)


def write_capture_config(temp_dir: str, port: int, version: str, catalog: list) -> str:
    config = {
        "url": f"http://127.0.0.1:{port}/index.html",
        "width": LAYOUT_WIDTH,
        "height": LAYOUT_HEIGHT,
        "scale": CAPTURE_SCALE,
        "outDir": "shots",
        "waitFor": "typeof render === 'function'",
        "setup": build_setup_script(version, catalog),
        "settleMs": 500,
        "shots": [
            {"name": "light", "script": "applyTheme('light')"},
            {"name": "dark", "script": "applyTheme('dark')"},
        ],
    }
    path = os.path.join(temp_dir, "shots.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    return path


def run(cmd: list, label: str) -> None:
    result = subprocess.run(cmd, cwd=REPO_ROOT)
    if result.returncode != 0:
        fail(f"{label} failed with exit code {result.returncode}")


def main(argv: list) -> None:
    keep = "--keep" in argv
    build_tools = os.path.join(os.path.dirname(REPO_ROOT), "build-tools")
    if "--build-tools" in argv:
        index = argv.index("--build-tools") + 1
        if index >= len(argv):
            fail("--build-tools needs a path after it")
        build_tools = argv[index]

    capture_script = os.path.join(build_tools, "screenshot", "capture.mjs")
    compose_script = os.path.join(build_tools, "screenshot", "compose.py")
    for path in (capture_script, compose_script):
        if not os.path.exists(path):
            fail(f"missing {path}. Pass --build-tools with the repo path.")

    try:
        import PIL  # noqa: F401
    except ImportError:
        fail(
            "Pillow is not installed for this Python "
            f"({sys.executable}). compose.py needs it. Run this script with "
            "a Python that has Pillow (e.g. the system Python), not the "
            "repo's own venv: Pillow is not one of this app's own "
            "dependencies."
        )

    source = read_source()
    version = read_app_version(source)
    catalog = read_game_catalog(source)
    temp_dir = tempfile.mkdtemp(prefix="rcon-screenshot-")
    httpd = None

    try:
        stage_ui(temp_dir)

        port = free_port()

        class Handler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def __init__(self, *a, **kw):
                super().__init__(*a, directory=temp_dir, **kw)

        httpd = socketserver.TCPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        config_path = write_capture_config(temp_dir, port, version, catalog)
        run(["node", capture_script, config_path], "capture")

        shots_dir = os.path.join(temp_dir, "shots")
        run([sys.executable, compose_script, OUT_IMAGE,
             os.path.join(shots_dir, "light.png"),
             os.path.join(shots_dir, "dark.png")], "compose")
    finally:
        if httpd is not None:
            httpd.shutdown()
        if keep:
            print(f"temp folder kept at {temp_dir}")
        else:
            shutil.rmtree(temp_dir, ignore_errors=True)
            if os.path.exists(temp_dir):
                print(f"WARNING: could not remove {temp_dir}", file=sys.stderr)

    print(f"seeded version: v{version}")
    print(f"updated {OUT_IMAGE}")


if __name__ == "__main__":
    main(sys.argv[1:])
