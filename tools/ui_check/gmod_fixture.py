"""Start the local Garry's Mod test server for the gmod-console scenario.

Uses the server the live test runner installed in E:\\SRT-TestServers, with the
runner's own launch flags (127.0.0.1 only, LAN mode, no SourceTV). The driver's
job closes the server when the run ends.
"""

import json
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"E:\SRT-TestServers")
REPO = Path.cwd()
sys.path.insert(0, str(REPO))

from tools.live_test.games.gmod import DEFINITION  # noqa: E402


def main() -> None:
    password = secrets.token_hex(12)
    # srcds in -console mode needs a real console for input, so it gets its own, hidden.
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    subprocess.Popen(DEFINITION["launch"](ROOT, None, password), cwd=DEFINITION["cwd"](ROOT, None),
                     creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup)
    port = DEFINITION["rcon_port"]
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            break
        except OSError:
            time.sleep(1)
    else:
        raise SystemExit("Garry's Mod server did not open RCON within 300 seconds")
    print(json.dumps({"host": "127.0.0.1", "port": port, "password": password}), flush=True)
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
