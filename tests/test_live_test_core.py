import hashlib
import json
from pathlib import Path

import pytest

from simple_rcon_tool import CATALOG_BY_KEY
from tools.live_test import run
from tools.live_test.core import (LiveTestError, classify_check, fill_command, listener_confirmed,
                                  mask_secrets, parse_appmanifest_build_id, parse_netstat,
                                  validate_definition, verify_file, write_results)
from tools.live_test.games import GAMES


def test_definitions_match_real_catalog_in_both_directions():
    for definition in GAMES.values():
        validate_definition(CATALOG_BY_KEY[definition["catalog_key"]], definition)
    with pytest.raises(LiveTestError, match="missing"):
        validate_definition(CATALOG_BY_KEY["minecraft"], {"checks": []})


def test_definition_refuses_checks_without_an_expected_result():
    catalog = {"buttons": [{"label": "Who Is", "command": "whois {arg}"}, {"label": "Kick", "command": "kick {arg}"}]}
    with pytest.raises(LiveTestError, match="Who Is"):
        validate_definition(catalog, {"checks": [{"label": "Who Is", "expect": {}},
                                                 {"label": "Kick", "player_required": True}]})


def test_argument_filling_matches_ui_including_underscore_mode():
    assert fill_command({"command": "say {arg} {arg}"}, "two words") == "say two words two words"
    assert fill_command({"command": "Broadcast {arg}", "argmode": "underscore"}, "two words") == "Broadcast two_words"


@pytest.mark.parametrize(
    ("expectation", "reply", "error", "evidence", "status"),
    [({}, "", None, True, "fail"), ({}, "reply", TimeoutError(), True, "fail"),
     ({"player_required": True}, "accepted", None, True, "unverified"),
     ({"player_required": True}, "", ConnectionAbortedError(), True, "fail"),
     ({"reply": "ok"}, "ok", None, True, "pass"),
     ({"disconnect": True}, "", ConnectionError(), True, "pass")],
)
def test_check_status_rules(expectation, reply, error, evidence, status):
    assert classify_check(expectation, reply, error, evidence)[0] == status


class FakeSession:
    reply = "ok"
    error = None
    commands = []
    log_path = None

    def __init__(self, server, password):
        self.password = password

    def connect(self):
        return None

    def command(self, command):
        self.commands.append(command)
        if self.log_path:
            self.log_path.write_text(command, encoding="utf-8")
        if self.error:
            raise self.error
        return self.reply

    def close(self):
        return None


class ExitedProcess:
    def wait(self, timeout):
        return 0


def _checks(monkeypatch, tmp_path, checks, *, reply="ok", error=None, timeout=20, log_evidence=True):
    FakeSession.reply, FakeSession.error, FakeSession.commands = reply, error, []
    log_path = tmp_path / "server.log"
    FakeSession.log_path = log_path if log_evidence else None
    monkeypatch.setattr(run, "Session", FakeSession)
    catalog = {"buttons": [{"label": check["label"], "command": check.get("command", "say {arg}"),
                            **({"argmode": "underscore"} if check.get("underscore") else {})} for check in checks]}
    definition = {"catalog_key": "fake", "rcon_port": 1, "checks": checks,
                  "timeouts": {"check": timeout, "stop": 1}}
    redactions = []
    records = run.run_checks(definition, catalog, "password-not-for-output", tmp_path, log_path, ExitedProcess(), redactions)
    return records, redactions, log_path


def test_run_checks_uses_core_argument_filling(monkeypatch, tmp_path):
    records, _, _ = _checks(monkeypatch, tmp_path, [{"label": "Broadcast", "arg": "two words", "underscore": True,
                                                       "command": "Broadcast {arg}", "expect": {}}])
    assert FakeSession.commands == ["Broadcast two_words"]
    assert records[0]["status"] == "pass"


def test_probes_run_first_and_are_recorded_ungraded(monkeypatch, tmp_path):
    FakeSession.reply, FakeSession.error, FakeSession.commands, FakeSession.log_path = "", None, [], None
    monkeypatch.setattr(run, "Session", FakeSession)
    definition = {"catalog_key": "fake", "rcon_port": 1, "probes": ["help"], "timeouts": {"check": 20, "stop": 1},
                  "checks": [{"label": "List", "expect": {}}]}
    catalog = {"buttons": [{"label": "List", "command": "list"}]}
    records = run.run_checks(definition, catalog, "pw", tmp_path, tmp_path / "server.log", ExitedProcess())
    assert FakeSession.commands == ["help", "list"]
    assert [row["status"] for row in records] == ["info", "fail"]


def test_say_empty_reply_requires_log_evidence(monkeypatch, tmp_path):
    checks = [{"label": "Say", "arg": "srt-{random}", "expect": {"log_contains_arg": True, "empty_reply_ok": True}}]
    records, redactions, log_path = _checks(monkeypatch, tmp_path, checks, reply="")
    assert records[0]["status"] == "pass"
    records, _, _ = _checks(monkeypatch, tmp_path, checks, reply="", log_evidence=False)
    assert records[0]["status"] == "fail"
    write_results(tmp_path, {"game": "fake", "status": "pass", "checks": records}, ["password-not-for-output", *redactions])
    result = (tmp_path / "results.json").read_text(encoding="utf-8")
    assert "password-not-for-output" not in result
    assert "not-for-output" not in result


def test_ban_without_evidence_is_unverified(monkeypatch, tmp_path):
    records, _, _ = _checks(monkeypatch, tmp_path, [{"label": "Ban", "expect": {"file_contains": ["missing.txt", "player"]},
                                                       "unverified_if_no_evidence": True}])
    assert records[0]["status"] == "unverified"


def test_timeout_and_elapsed_check_limit_fail(monkeypatch, tmp_path):
    records, _, _ = _checks(monkeypatch, tmp_path, [{"label": "Timeout", "expect": {}}], error=TimeoutError("late"))
    assert records[0]["status"] == "fail"
    ticks = iter([0, 10])
    monkeypatch.setattr(run.time, "monotonic", lambda: next(ticks))
    records, _, _ = _checks(monkeypatch, tmp_path, [{"label": "Slow", "expect": {}}], timeout=1)
    assert records[0]["status"] == "fail"
    assert "exceeded" in records[0]["exception"]


def test_stop_check_passes_on_disconnect_and_process_exit(monkeypatch, tmp_path):
    records, _, _ = _checks(monkeypatch, tmp_path, [{"label": "Stop", "stop": True,
                                                       "expect": {"disconnect": True}}], error=ConnectionError("closed"))
    assert records[0]["status"] == "pass"


def test_evidence_function_gets_run_folder_and_argument_and_is_recorded_by_name(monkeypatch, tmp_path):
    seen = []

    def banned(run_dir, arg):
        seen.append((run_dir, arg))
        return arg == "Name"

    checks = [{"label": "Ban", "arg": "Name", "expect": {"reply": "^ok$", "evidence": banned}},
              {"label": "Ban Other", "arg": "Other", "expect": {"reply": "^ok$", "evidence": banned}}]
    records, _, _ = _checks(monkeypatch, tmp_path, checks)
    assert seen == [(tmp_path, "Name"), (tmp_path, "Other")]
    assert [row["status"] for row in records] == ["pass", "fail"]
    write_results(tmp_path, {"game": "fake", "status": "pass", "checks": records}, [])
    assert json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))["checks"][0]["expected"]["evidence"] == "banned"


def test_evidence_wait_keeps_looking_for_late_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(run.time, "sleep", lambda seconds: None)
    calls = []

    def saved(run_dir, arg):
        calls.append(arg)
        return len(calls) >= 3

    records, _, _ = _checks(monkeypatch, tmp_path, [{"label": "Save", "evidence_wait": 5,
                                                       "expect": {"reply": "^ok$", "evidence": saved}}])
    assert records[0]["status"] == "pass"
    assert len(calls) == 3
    calls.clear()
    records, _, _ = _checks(monkeypatch, tmp_path, [{"label": "Save", "expect": {"reply": "^ok$", "evidence": saved}}])
    assert records[0]["status"] == "fail"
    assert len(calls) == 1


def test_checksum_refuses_empty_and_mismatch(tmp_path):
    file = tmp_path / "download.bin"
    file.write_bytes(b"safe")
    with pytest.raises(LiveTestError, match="empty"):
        verify_file(file, {"name": "item"})
    with pytest.raises(LiveTestError, match="mismatch"):
        verify_file(file, {"name": "item", "sha256": "0" * 64})
    verify_file(file, {"name": "item", "sha256": hashlib.sha256(b"safe").hexdigest()})


def test_netstat_parsing_and_loopback_pid_matching_including_ipv6():
    output = "  TCP    127.0.0.1:25575     0.0.0.0:0      LISTENING       123\n  TCP    [::1]:25575          [::]:0           LISTENING       456"
    assert {row["address"] for row in parse_netstat(output)} == {"127.0.0.1", "::1"}
    assert listener_confirmed(output, 25575, {123}, True)
    assert listener_confirmed(output, 25575, {456}, True)


def test_results_masking_and_appmanifest_parsing(tmp_path):
    assert mask_secrets({"password": "top-secret", "nested": ["top-secret"]}, ["top-secret"]) == {"password": "[REDACTED]", "nested": ["[REDACTED]"]}
    manifest = tmp_path / "appmanifest_896660.acf"
    manifest.write_text('"AppState"\n{\n "buildid" "123456"\n}', encoding="utf-8")
    assert parse_appmanifest_build_id(manifest) == "123456"


def test_post_cleanup_port_recheck_fails_run(monkeypatch, tmp_path):
    definition = {"key": "fake", "name": "Fake", "catalog_key": "fake", "ports": [1], "rcon_port": 1,
                  "loopback_supported": True, "loopback_reason": "", "checks": [], "timeouts": {"stop": 1},
                  "configure": lambda root, run_dir, definition, password: None, "server_version": lambda root: "1"}
    class Job:
        handle = True
        def assign(self, process): pass
        def pids(self): return set()
        def terminate(self): pass
        def close(self): self.handle = None
    class Process:
        def poll(self): return 0
        def wait(self, timeout): return 0
    monkeypatch.setattr(run, "catalog_for", lambda definition: {"buttons": []})
    monkeypatch.setattr(run, "isolation", lambda definition, root: {"ports_free_before": True})
    monkeypatch.setattr(run, "WindowsJob", Job)
    monkeypatch.setattr(run.subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(run, "ready", lambda *args: None)
    monkeypatch.setattr(run, "run_checks", lambda *args: [])
    monkeypatch.setattr(run, "ports_free", lambda ports: (False, "TCP 127.0.0.1:1 is busy"))
    monkeypatch.setattr(run, "runner_git_commit", lambda: "unknown")
    assert run.run_game(definition, tmp_path, True) == 1
    result = json.loads(next((tmp_path / "runs").glob("*/results.json")).read_text(encoding="utf-8"))
    assert result["isolation"]["ports_free_after_cleanup"] is False


@pytest.mark.parametrize("ready_log", [None, "Startup report"])
def test_ready_log_waits_after_ready_and_before_checks(monkeypatch, tmp_path, ready_log):
    definition = {"key": "fake", "name": "Fake", "catalog_key": "fake", "ports": [1], "rcon_port": 1,
                  "loopback_supported": True, "loopback_reason": "", "checks": [], "timeouts": {"start": 7, "stop": 1},
                  "launch": lambda root, run_dir, password: [],
                  "configure": lambda root, run_dir, definition, password: None, "server_version": lambda root: "1"}
    if ready_log:
        definition["ready_log"] = ready_log
    calls = []
    class Job:
        handle = True
        def assign(self, process): pass
        def pids(self): return set()
        def terminate(self): pass
        def close(self): self.handle = None
    class Process:
        def poll(self): return 0
        def wait(self, timeout): return 0
    monkeypatch.setattr(run, "catalog_for", lambda definition: {"buttons": []})
    monkeypatch.setattr(run, "isolation", lambda definition, root: {"ports_free_before": True})
    monkeypatch.setattr(run, "WindowsJob", Job)
    monkeypatch.setattr(run.subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(run, "ready", lambda *args: calls.append("ready"))
    monkeypatch.setattr(run, "wait_for_log", lambda log_path, text, process, timeout, stage:
                        calls.append(("wait_for_log", log_path.name, text, timeout, stage)))
    monkeypatch.setattr(run, "run_checks", lambda *args: calls.append("checks") or [])
    monkeypatch.setattr(run, "ports_free", lambda ports: (True, ""))
    monkeypatch.setattr(run, "runner_git_commit", lambda: "unknown")
    assert run.run_game(definition, tmp_path, True) == 0
    if ready_log:
        assert calls == ["ready", ("wait_for_log", "server.log", "Startup report", 7, "startup"), "checks"]
    else:
        assert calls == ["ready", "checks"]


def test_ready_log_timeout_fails_run_without_checks(monkeypatch, tmp_path):
    definition = {"key": "fake", "name": "Fake", "catalog_key": "fake", "ports": [1], "rcon_port": 1,
                  "loopback_supported": True, "loopback_reason": "", "checks": [], "timeouts": {"start": 7, "stop": 1},
                  "launch": lambda root, run_dir, password: [],
                  "configure": lambda root, run_dir, definition, password: None, "server_version": lambda root: "1",
                  "ready_log": "never logged"}
    class Job:
        handle = True
        def assign(self, process): pass
        def pids(self): return set()
        def terminate(self): pass
        def close(self): self.handle = None
    class Process:
        def poll(self): return 0
        def wait(self, timeout): return 0
    def no_line(*args):
        raise LiveTestError("startup did not log 'never logged' within 7 seconds")
    checks = []
    monkeypatch.setattr(run, "catalog_for", lambda definition: {"buttons": []})
    monkeypatch.setattr(run, "isolation", lambda definition, root: {"ports_free_before": True})
    monkeypatch.setattr(run, "WindowsJob", Job)
    monkeypatch.setattr(run.subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(run, "ready", lambda *args: None)
    monkeypatch.setattr(run, "wait_for_log", no_line)
    monkeypatch.setattr(run, "run_checks", lambda *args: checks.append("ran") or [])
    monkeypatch.setattr(run, "ports_free", lambda ports: (True, ""))
    monkeypatch.setattr(run, "runner_git_commit", lambda: "unknown")
    assert run.run_game(definition, tmp_path, True) == 1
    assert checks == []
    result = json.loads(next((tmp_path / "runs").glob("*/results.json")).read_text(encoding="utf-8"))
    assert result["lifecycle_error"] == "startup did not log 'never logged' within 7 seconds"


def test_skip_update_still_records_download_and_mod_versions():
    baseline = json.loads((Path(run.__file__).parent / "baseline.json").read_text(encoding="utf-8"))
    downloads, mods = run.recorded_versions(GAMES["valheim"], baseline)
    assert downloads["bepinexpack"] == "5.4.2351"
    assert mods == {"bepinexpack": "5.4.2351", "valheim_rcon": "1.6.2"}


def test_shared_runner_has_no_game_key_strings():
    source = Path(run.__file__).read_text(encoding="utf-8").lower()
    assert "minecraft" not in source
    assert "valheim" not in source


class _FakeProcess:
    def __init__(self, exits_on_signal=True, returncode=None):
        self.exits_on_signal, self.returncode, self.signals = exits_on_signal, returncode, []

    def send_signal(self, sig):
        self.signals.append(sig)

    def wait(self, timeout=None):
        if not self.exits_on_signal:
            raise run.subprocess.TimeoutExpired("server", timeout)
        self.returncode = 0
        return 0

    def poll(self):
        return self.returncode


def test_ctrl_break_stop_reports_clean_exit_and_timeout():
    assert run.stop_with_ctrl_break(_FakeProcess(), 1) is True
    assert run.stop_with_ctrl_break(_FakeProcess(exits_on_signal=False), 1) is False


def test_wait_for_log_finds_text_and_fails_when_server_exits(tmp_path):
    log = tmp_path / "first-boot.log"
    log.write_text("starting\nLoading: Done\n", encoding="utf-8")
    run.wait_for_log(log, "Loading: Done", _FakeProcess(), 5)
    with pytest.raises(LiveTestError, match="exited with code 3"):
        run.wait_for_log(log, "never logged", _FakeProcess(returncode=3), 5)
    with pytest.raises(LiveTestError, match="exited with code 3 during startup"):
        run.wait_for_log(log, "never logged", _FakeProcess(returncode=3), 5, "startup")
    with pytest.raises(LiveTestError, match="^startup did not log 'never logged' within 0 seconds$"):
        run.wait_for_log(log, "never logged", _FakeProcess(), 0, "startup")


def test_first_boot_shuts_down_cleanly_or_fails(monkeypatch, tmp_path):
    definition = {"first_boot": {"ready_log": "Loading: Done"}, "timeouts": {"start": 5, "stop": 5}}
    process = _FakeProcess()

    def fake_start(definition, root, run_dir, password, log, job):
        log.write(b"Loading: Done\n")
        log.flush()
        return process

    monkeypatch.setattr(run, "start_server", fake_start)
    run.first_boot(definition, tmp_path, tmp_path, "pw", job=None)
    assert process.signals == [run.signal.CTRL_BREAK_EVENT]
    stuck = _FakeProcess(exits_on_signal=False)
    monkeypatch.setattr(run, "start_server", lambda *args: (args[4].write(b"Loading: Done\n"), args[4].flush(), stuck)[2])
    with pytest.raises(LiveTestError, match="did not shut down cleanly"):
        run.first_boot(definition, tmp_path, tmp_path, "pw", job=None)


def test_ready_fails_fast_when_server_has_exited():
    definition = {"timeouts": {"start": 600}, "rcon_port": 1, "catalog_key": "fake", "loopback_supported": True}
    with pytest.raises(LiveTestError, match="exited with code 2 before RCON was ready"):
        run.ready(definition, "pw", None, _FakeProcess(returncode=2))


@pytest.mark.parametrize("console_input", [False, True])
def test_console_input_hands_server_the_console(monkeypatch, tmp_path, console_input):
    opened, seen = [], {}

    class FakeConsole:
        closed = False

        def close(self):
            self.closed = True

    def fake_open(path, mode):
        opened.append((path, mode))
        return FakeConsole()

    class FakeJob:
        def assign(self, process):
            seen["assigned"] = process

    def fake_popen(command, **kwargs):
        seen.update(kwargs)
        return "process"

    monkeypatch.setattr(run, "open", fake_open, raising=False)
    monkeypatch.setattr(run.subprocess, "Popen", fake_popen)
    definition = {"launch": lambda root, run_dir, password: ["server.exe"], "console_input": console_input}
    assert run.start_server(definition, tmp_path, tmp_path, "pw", None, FakeJob()) == "process"
    if console_input:
        assert opened == [("CONIN$", "rb")]
        assert isinstance(seen["stdin"], FakeConsole) and seen["stdin"].closed
    else:
        assert opened == [] and seen["stdin"] is None
    assert seen["assigned"] == "process"
