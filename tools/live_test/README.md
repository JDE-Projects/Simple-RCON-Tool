# Simple RCON Tool live test runner

This tool installs and tests a real dedicated server locally. It is intentionally separate from the app. It uses the app's own `Session` class and its real button catalog, but it does not modify the app or any server outside the selected test root.

## Before the first run

- Use Windows and Python 3.12 or later.
- `baseline.json` supplies the approved URLs, hashes, and versions.
- Install SteamCMD at `E:\SRT-TestServers\steamcmd\steamcmd.exe` for Valheim.
- Ensure the game and RCON ports are free. The runner checks TCP and UDP on `0.0.0.0` and `127.0.0.1` before and after a run.

## Commands

`python -m tools.live_test.run list` lists available games and their catalog keys.

`python -m tools.live_test.run run minecraft` or `python -m tools.live_test.run run valheim` updates, starts, tests, stops, and records one game. Add `--skip-update` to reuse already verified installation files.

The default data root is `E:\SRT-TestServers`. Use `--root <path>` to choose another root. Servers are reused under `servers`, Java is under `runtime`, and every run writes a new folder under `runs` containing the throwaway world/configuration, server log, `results.json`, and `results.md`.

## What it checks

The runner validates catalog labels before starting, validates downloads by hash, requires free ports, uses a new RCON password per run, runs servers in a Windows Job Object, checks RCON readiness and listener ownership, then runs each catalog button in order with pre-written reply and file/log evidence. It records `pass`, `fail`, or `unverified`. Player-only Kick checks are always `unverified` without a real player. Results record ports free before and after cleanup, listener ownership, the bind address or written loopback limitation, the runner commit, and server, download, and mod versions.

Minecraft uses localhost RCON and tests its standard console buttons. Valheim installs BepInExPack and ValheimRcon and starts without crossplay or public listing. ValheimRcon does not open its port on a brand-new world's first start, so each Valheim run boots once, waits for the world to load, shuts down with Ctrl+Break (which saves the world), then starts again for the checks. The mod has no RCON stop command, so the run ends with Ctrl+Break too, and the Job Object terminates anything still running.

## Limits and first-run confirmation

Expected replies are confirmed against Minecraft 26.3 and Valheim with ValheimRcon 1.6.2. A new server or mod version can change reply wording, which shows up as a failed check. The runner never downloads in tests and never needs elevation.
