"""Hold the saved server file open so persistence attempts fail in save-error."""

import json
import os
import time
from pathlib import Path


def main() -> None:
    servers_file = Path(os.environ["UI_DRIVE_APP_DIR"]) / "servers.json"
    server = {
        "id": "srv-one",
        "name": "Original Server",
        "host": "192.0.2.10",
        "port": 27015,
        "game": "ark_se",
        "buttons": [],
    }
    servers_file.write_text(json.dumps([server]), encoding="utf-8")
    with open(servers_file, encoding="utf-8"):
        print(json.dumps({"name": server["name"]}), flush=True)
        while True:
            time.sleep(60)


if __name__ == "__main__":
    main()
