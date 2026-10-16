# Simple RCON Tool live test runner

This tool installs and tests a real dedicated server locally. It is intentionally separate from the app. It uses the app's own `Session` class and its real button catalog, but it does not modify the app or any server outside the selected test root.

Run it against every game in the catalog before each release. A real incompatibility blocks the release until it is fixed or the game is removed from the catalog; an infrastructure failure (download, missing file, port in use) is not a reason to remove a game.

## Before the first run

- Use Windows and Python 3.12 or later.
- `baseline.json` supplies the approved URLs, hashes, and versions for non-Steam downloads (Minecraft, Java, Valheim's mods). Steam servers come from SteamCMD with an anonymous login.
- Install SteamCMD at `E:\SRT-TestServers\steamcmd\steamcmd.exe`. On its first run after updating itself it exits with code 7; run it again.
- Ensure the game and RCON ports are free. The runner checks TCP and UDP on `0.0.0.0` and `127.0.0.1` before and after a run.

## Commands

From the repo root:

`.venv/Scripts/python.exe -m tools.live_test.run list` lists available games and their catalog keys.

`.venv/Scripts/python.exe -m tools.live_test.run run <game>` updates, starts, tests, stops, and records one game. Add `--skip-update` to reuse already installed files.

The default data root is `E:\SRT-TestServers`. Use `--root <path>` to choose another root. Servers are reused under `servers`, Java is under `runtime`, and every run writes a new folder under `runs` containing the server log, `results.json`, and `results.md`. The runner never deletes run folders.

## What it checks

The runner validates catalog labels before starting, validates downloads by hash, requires free ports, uses a new RCON password per run, runs servers in a Windows Job Object, checks RCON readiness and listener ownership, then runs each catalog button in order with pre-written reply and file/log evidence. It records `pass`, `fail`, or `unverified`. Buttons that need a player in the game are always `unverified`. Results record ports free before and after cleanup, listener ownership, the bind address or written loopback limitation, the runner commit, and server, download, and mod versions.

A new server or mod version can change reply wording, which shows up as a failed check. Read the actual reply in `results.md` before deciding whether the game or the expected pattern is wrong.

## Adding a game

Add a definition in `tools/live_test/games/` and register it in `games/__init__.py`. Pin any non-Steam download in `baseline.json` with a checksum. The shared runner stays free of game names (a test enforces it).

Definition keys:

- `key`, `name`, `catalog_key`: runner key, display name, and the app catalog key whose buttons are checked.
- `appid`: SteamCMD app id. `downloads`: `baseline.json` entries to fetch (`mod: True` records it as a mod version).
- `rcon_port`, `ports`: the RCON port and every port the server opens.
- `loopback_supported`: `True` when RCON can be bound to 127.0.0.1; otherwise `loopback_reason` says why not.
- `timeouts`: `update`, `start`, `check`, `stop`, in seconds.
- `configure(root, run_dir, definition, password)`: writes config before start. `launch(root, run, password)`: the command line. `cwd`: start folder when the server must run from its install folder.
- `server_version(root)`: version recorded in results.
- `server_log(run_dir)`: the log file to watch when the server writes nothing to its output.
- `ready_log`: a log line that means commands work. Without it, readiness is RCON accepting a login.
- `first_boot`: `{"ready_log": ...}` to start once, wait for that line, and stop before the real run.
- `stop_signal`: `"ctrl_break"` to stop with Ctrl+Break; otherwise the Job Object force-closes the server.
- `console_input`: give the server a real console for input.
- `probes`: commands sent before the checks and recorded but not graded (repeats on one connection, an unknown command).
- `checks`: one per catalog button, by `label`, with optional `arg` (`{random}` becomes a fresh token), `stop` (the button stops the server), `cleanup` (a command sent afterwards), `evidence_wait`, `player_required`, and `expect`.
- `expect` keys: `reply` (regex, anchored at the start), `empty_reply_ok`, `disconnect`, `evidence` (a function), `file_contains` / `file_not_contains` (`[path, text]`), `modified`, `modified_glob`, `log_contains`, `log_contains_arg`, `unverified_if_no_evidence`.

## Per-game setup

- **Minecraft:** 26.3 needs Java 25 (Temurin, from `baseline.json`).
- **Valheim:** installs BepInExPack and ValheimRcon from `gcdn.thunderstore.io` (the `ccdn` server is blocked here). ValheimRcon does not open its port on a brand-new world's first start, so each run boots once, shuts down with Ctrl+Break, then starts again for the checks.
- **Eco:** needs `-offline` (skips the account sign-in) and an empty `Storage/UserTextures/Reviewed` folder in its install folder (the definition does both).
- **ARK: Survival Evolved:** needs two DirectX audio files Windows 11 does not ship, `X3DAudio1_7.dll` and `XAPOFX1_5.dll`; without them the server exits at once with code 0xC0000135. Take them from Microsoft's DirectX End-User Runtimes (June 2010) package, `directx_Jun2010_redist.exe` (check its Microsoft signature): extract it with `/Q /T:<folder>`, then `expand` `Feb2010_X3DAudio_x64.cab` and `Jun2010_XAudio_x64.cab` into `servers\ark_se\ShooterGame\Binaries\Win64`. Nothing is installed into Windows. The package is kept in `E:\SRT-TestServers\downloads\directx_jun2010`. Its log stays empty without `-FORCELOGFLUSH`. Startup takes about six minutes.
- **Counter-Strike 2:** needs `steamclient64.dll`, `tier0_s64.dll` and `vstdlib_s64.dll` from SteamCMD next to `cs2.exe` (the definition copies them).
- **Palworld:** `PalServer.exe` only checks for a DirectX file and stops with a "DirectX Runtime" error, so the definition starts `PalServer-Win64-Shipping-Cmd.exe` directly. It writes no log, so readiness is the RCON login.
- **Project Zomboid:** the server's own start script asks Java for 16 GB; the definition starts Java itself with 4 GB.

Some servers keep their world, settings or logs in the install folder rather than the run folder (ARK Evolved under `ShooterGame/Saved`, Palworld under `Pal/Saved`). Clear those by hand.

## When a server will not start or reply

- Exits at once: check which DLLs its exe imports and whether it needs Steam client files.
- Writes nothing to its output: point `server_log` at its log file, and look for a log-flush switch.
- RCON listens on every address: check whether RCON follows the game's address setting.
- Replies are empty or garbled: record the raw bytes before guessing.
