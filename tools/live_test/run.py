"""Run offline-safe orchestration for a deliberately local game server test."""

import argparse
import json
import secrets
import signal
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from simple_rcon_tool import CATALOG_BY_KEY, Session
from tools.live_test.core import (LiveTestError, WindowsJob, classify_check, download_verified, fill_command,
                                  fresh_password, listener_confirmed, ports_free, utc_stamp,
                                  validate_definition, verify_file, write_results)
from tools.live_test.games import GAMES

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

DEFAULT_ROOT = Path(r"E:\SRT-TestServers")


def catalog_for(definition: dict) -> dict:
    try:
        catalog = CATALOG_BY_KEY[definition["catalog_key"]]
    except KeyError as exc:
        raise LiveTestError(f"catalog game not found: {definition['catalog_key']}") from exc
    validate_definition(catalog, definition)
    return catalog


def update(definition: dict, root: Path, baseline: dict) -> tuple[dict, dict]:
    server_dir = root / "servers" / definition["key"]
    server_dir.mkdir(parents=True, exist_ok=True)
    if definition.get("appid"):
        steamcmd = root / "steamcmd" / "steamcmd.exe"
        if not steamcmd.exists():
            raise LiveTestError(f"SteamCMD not found: {steamcmd}")
        command = [str(steamcmd), "+force_install_dir", str(server_dir), "+login", "anonymous",
                   "+app_update", str(definition["appid"]), "validate", "+quit"]
        result = subprocess.run(command, timeout=definition["timeouts"]["update"], check=False)
        if result.returncode == 7:
            result = subprocess.run(command, timeout=definition["timeouts"]["update"], check=False)
        if result.returncode:
            raise LiveTestError(f"SteamCMD failed with exit code {result.returncode}")
    download_versions, mod_versions = {}, {}
    for download in definition.get("downloads", []):
        name = download["baseline"]
        item = baseline[name]
        destination = download["destination"](root, server_dir)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            verify_file(destination, item)
        else:
            download_verified(item, destination)
        installer = download.get("install")
        if installer:
            with zipfile.ZipFile(destination) as archive:
                installer(archive, root, server_dir)
        download_versions[name] = item["version"]
        if download.get("mod"):
            mod_versions[name] = item["version"]
    return download_versions, mod_versions


def recorded_versions(definition: dict, baseline: dict) -> tuple[dict, dict]:
    downloads = {item["baseline"]: baseline[item["baseline"]]["version"] for item in definition.get("downloads", [])}
    mods = {item["baseline"]: baseline[item["baseline"]]["version"] for item in definition.get("downloads", []) if item.get("mod")}
    return downloads, mods


def isolation(definition: dict, root: Path) -> dict:
    free, message = ports_free(definition["ports"])
    if not free:
        raise LiveTestError(message)
    return {"ports_free_before": True}


def evidence_for(expectation: dict, run_dir: Path, log_path: Path, sent_at: float, arg: str) -> bool:
    # A definition's own function, for evidence the keys below cannot read (a database, a dated log file).
    if "evidence" in expectation:
        return bool(expectation["evidence"](run_dir, arg))
    if "file_contains" in expectation or "file_not_contains" in expectation:
        relative, text = expectation.get("file_contains", expectation.get("file_not_contains"))
        content = (run_dir / relative).read_text(encoding="utf-8", errors="replace") if (run_dir / relative).exists() else ""
        return (text in content) if "file_contains" in expectation else (text not in content)
    if "modified" in expectation:
        path = run_dir / expectation["modified"]
        return path.exists() and path.stat().st_mtime >= sent_at
    if "modified_glob" in expectation:
        return any(path.is_file() and path.stat().st_mtime >= sent_at for path in run_dir.glob(expectation["modified_glob"]))
    if "log_contains" in expectation or "log_contains_arg" in expectation:
        text = expectation.get("log_contains", arg)
        return log_path.exists() and text in log_path.read_text(encoding="utf-8", errors="replace")
    return True


def run_checks(definition: dict, catalog: dict, password: str, run_dir: Path, log_path: Path,
               process: subprocess.Popen, redactions: list[str] | None = None) -> list[dict]:
    buttons = {button["label"]: button for button in catalog["buttons"]}
    session = Session({"host": "127.0.0.1", "port": definition["rcon_port"], "game": definition["catalog_key"]}, password)
    records = []
    try:
        session.connect()
        # Probes are raw commands recorded for research (syntax, help text), never graded.
        for command in definition.get("probes", []):
            started, reply, error = time.time(), "", None
            try:
                reply = session.command(command)
            except Exception as exc:
                error = exc
            records.append({"label": f"probe: {command}", "status": "info", "expected": {}, "actual": reply,
                            "evidence": "recorded, not graded", "timing_seconds": round(time.time() - started, 3),
                            "exception": str(error) if error else None})
        for check in definition["checks"]:
            button, started, started_monotonic = buttons[check["label"]], time.time(), time.monotonic()
            token = secrets.token_hex(4)
            arg = check.get("arg", "").replace("{random}", token)
            if "{random}" in check.get("arg", "") and redactions is not None:
                redactions.append(token)
            command = fill_command(button, arg)
            reply, error = "", None
            try:
                reply = session.command(command)
            except Exception as exc:  # Expected for stop/disconnect commands.
                error = exc
            if time.monotonic() - started_monotonic > definition["timeouts"]["check"]:
                error = TimeoutError(f"check exceeded {definition['timeouts']['check']} seconds")
            expected = check.get("expect", {})
            effect = evidence_for(expected, run_dir, log_path, started, arg)
            # Some servers act after replying (a queued save, a shutdown); "evidence_wait" keeps looking that many seconds.
            if not effect and check.get("evidence_wait"):
                deadline = time.monotonic() + check["evidence_wait"]
                while not effect and time.monotonic() < deadline:
                    time.sleep(0.5)
                    effect = evidence_for(expected, run_dir, log_path, started, arg)
            if check.get("stop"):
                try:
                    process.wait(timeout=definition["timeouts"]["stop"])
                    effect = effect and True
                except subprocess.TimeoutExpired:
                    effect = False
            status, note = classify_check({**expected, "player_required": check.get("player_required", False),
                                           "unverified_if_no_evidence": check.get("unverified_if_no_evidence", expected.get("unverified_if_no_evidence", False))}, reply, error, effect)
            # Results are JSON, so an evidence function is recorded by name.
            shown = {key: value.__name__ if callable(value) else value for key, value in expected.items()}
            records.append({"label": check["label"], "status": status, "expected": shown, "actual": reply,
                            "evidence": note, "timing_seconds": round(time.time() - started, 3),
                            "exception": str(error) if error else None})
            if check.get("cleanup"):
                try:
                    cleanup_reply = session.command(check["cleanup"])
                    records[-1]["cleanup"] = {"command": check["cleanup"], "reply": cleanup_reply}
                except Exception as exc:
                    records[-1]["cleanup"] = {"command": check["cleanup"], "exception": str(exc)}
    finally:
        session.close()
    return records


def ready(definition: dict, password: str, job: WindowsJob, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + definition["timeouts"]["start"]
    last_error = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise LiveTestError(f"server exited with code {process.returncode} before RCON was ready")
        probe = Session({"host": "127.0.0.1", "port": definition["rcon_port"], "game": definition["catalog_key"]}, password)
        try:
            probe.connect()
            probe.close()
            output = subprocess.check_output(["netstat", "-ano"], text=True, encoding="utf-8", errors="replace")
            if listener_confirmed(output, definition["rcon_port"], job.pids(), definition["loopback_supported"]):
                return
            raise LiveTestError("RCON accepted a connection but netstat ownership was not confirmed")
        except Exception as exc:
            last_error = exc
            time.sleep(1)
        finally:
            probe.close()
    raise LiveTestError(f"RCON did not become ready: {last_error}")


def start_server(definition: dict, root: Path, run_dir: Path, password: str, log, job: WindowsJob) -> subprocess.Popen:
    # Own process group so a console Ctrl+Break reaches only the server.
    # Some servers only start from their install folder; "cwd" picks the folder, else the run folder.
    cwd = definition["cwd"](root, run_dir) if definition.get("cwd") else run_dir
    # Some servers stop with an error unless their input is a real console; "console_input" hands them this one.
    stdin = open("CONIN$", "rb") if definition.get("console_input") else None
    try:
        process = subprocess.Popen(definition["launch"](root, run_dir, password), cwd=cwd, stdin=stdin, stdout=log,
                                   stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    finally:
        if stdin:
            stdin.close()
    job.assign(process)
    return process


def stop_with_ctrl_break(process: subprocess.Popen, timeout: float) -> bool:
    """Ask the server to shut down as Ctrl+Break in its console would. True when it exits in time."""
    try:
        process.send_signal(signal.CTRL_BREAK_EVENT)
        process.wait(timeout=timeout)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def job_emptied(job: WindowsJob, timeout: float) -> bool:
    """True once every process in the job has exited. Helpers (a crash reporter) may outlive the server briefly."""
    deadline = time.monotonic() + timeout
    while job.pids():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)
    return True


def wait_for_log(log_path: Path, text: str, process: subprocess.Popen, timeout: float, stage: str = "first boot") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise LiveTestError(f"server exited with code {process.returncode} during {stage}")
        if text in log_path.read_text(encoding="utf-8", errors="replace"):
            return
        time.sleep(2)
    raise LiveTestError(f"{stage} did not log {text!r} within {timeout} seconds")


def first_boot(definition: dict, root: Path, run_dir: Path, password: str, job: WindowsJob) -> None:
    """Start once, wait for the world to load, shut down cleanly so the world is saved."""
    log_path = run_dir / "first-boot.log"
    with log_path.open("wb") as log:
        process = start_server(definition, root, run_dir, password, log, job)
        try:
            wait_for_log(log_path, definition["first_boot"]["ready_log"], process, definition["timeouts"]["start"])
        finally:
            if not stop_with_ctrl_break(process, definition["timeouts"]["stop"]):
                raise LiveTestError("server did not shut down cleanly after first boot")


def run_game(definition: dict, root: Path, skip_update: bool) -> int:
    catalog = catalog_for(definition)
    baseline = json.loads((HERE / "baseline.json").read_text(encoding="utf-8"))
    download_versions, mod_versions = recorded_versions(definition, baseline) if skip_update else update(definition, root, baseline)
    isolation_evidence = isolation(definition, root)
    run_dir = root / "runs" / f"{definition['key']}-{utc_stamp()}"
    run_dir.mkdir(parents=True, exist_ok=False)
    password, log_path, redactions = fresh_password(), run_dir / "server.log", []
    definition["configure"](root, run_dir, definition, password)
    job, process = WindowsJob(), None
    records, lifecycle_error = [], None
    graceful_stop = None
    try:
        if definition.get("first_boot"):
            first_boot(definition, root, run_dir, password, job)
        with log_path.open("wb") as log:
            process = start_server(definition, root, run_dir, password, log, job)
            ready(definition, password, job, process)
            # Some servers open RCON before they can run commands; "ready_log" waits for the line that says they can.
            if definition.get("ready_log"):
                wait_for_log(log_path, definition["ready_log"], process, definition["timeouts"]["start"], "startup")
            isolation_evidence["rcon_listener_owned_by_job"] = True
            isolation_evidence["bind_address"] = "127.0.0.1" if definition["loopback_supported"] else definition["loopback_reason"]
            records = run_checks(definition, catalog, password, run_dir, log_path, process, redactions)
    except Exception as exc:
        lifecycle_error = str(exc)
    finally:
        if process and process.poll() is None and definition.get("stop_signal") == "ctrl_break":
            graceful_stop = stop_with_ctrl_break(process, definition["timeouts"]["stop"])
        if process and process.poll() is None:
            job.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                lifecycle_error = lifecycle_error or "server did not exit after Job Object termination"
        if not job_emptied(job, definition["timeouts"]["stop"]):
            lifecycle_error = lifecycle_error or "Job Object still contains processes after cleanup"
        if job.handle:
            job.close()
        free, message = ports_free(definition["ports"])
        isolation_evidence["ports_free_after_cleanup"] = free
        if not free:
            lifecycle_error = lifecycle_error or f"ports remain busy after cleanup: {message}"
    status = "pass" if not lifecycle_error and all(row["status"] in {"pass", "unverified", "info"} for row in records) else "fail"
    result = {"game": definition["name"], "catalog_key": definition["catalog_key"], "date": utc_stamp(), "status": status,
              "isolation": isolation_evidence, "lifecycle_error": lifecycle_error, "checks": records,
              "server_version": definition["server_version"](root), "download_versions": download_versions,
              "mod_versions": mod_versions, "runner_git_commit": runner_git_commit(), "graceful_stop": graceful_stop}
    write_results(run_dir, result, [password, *redactions])
    print(f"{definition['name']}: {status} ({len(records)} checks), results: {run_dir / 'results.md'}")
    return 0 if status == "pass" else 1


def runner_git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True, encoding="utf-8", timeout=10).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local live RCON checks.")
    subparsers = parser.add_subparsers(dest="action", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("game", choices=sorted(GAMES))
    run_parser.add_argument("--skip-update", action="store_true")
    run_parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    subparsers.add_parser("list")
    args = parser.parse_args()
    if args.action == "list":
        for definition in GAMES.values():
            print(f"{definition['key']}: {definition['name']} (catalog key: {definition['catalog_key']})")
        return 0
    try:
        return run_game(GAMES[args.game], args.root, args.skip_update)
    except LiveTestError as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
