"""Start the local ARK: Survival Ascended test server for the ark-asa-console scenario.

Uses the server the live test runner installed in E:\\SRT-TestServers, with the
runner's own launch flags (off BattlEye, game sockets on 127.0.0.1; RCON itself
listens on every address, as the runner records). The server's world, settings
and logs go in the driver's run folder. The driver's job closes the server when
the run ends.
"""

import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"E:\SRT-TestServers")
REPO = Path.cwd()
sys.path.insert(0, str(REPO))

from tools.live_test.games.ark_asa import DEFINITION  # noqa: E402


def main() -> None:
    password = secrets.token_hex(12)
    run = Path(os.environ["UI_DRIVE_OUT_DIR"]) / "ark-server"
    DEFINITION["configure"](ROOT, run, DEFINITION, password)
    # Its own hidden console, so the server's output never blocks on a pipe.
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    subprocess.Popen(DEFINITION["launch"](ROOT, run, password), cwd=run,
                     creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup)
    # RCON opens once the map has loaded, about two minutes in: longer than the driver
    # waits for this line, so the scenario retries Connect until the server answers.
    port = DEFINITION["rcon_port"]
    print(json.dumps({"host": "127.0.0.1", "port": port, "password": password}), flush=True)
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
