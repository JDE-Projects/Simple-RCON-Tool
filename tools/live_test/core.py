"""Generic, standard-library-only mechanics for a live test run."""

import ctypes
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


class LiveTestError(RuntimeError):
    """A safe refusal or lifecycle failure."""


def fill_command(button: dict, arg: str | None = None) -> str:
    """Apply button arguments exactly as the browser UI does."""
    command = button["command"]
    if "{arg}" not in command:
        return command
    value = arg or ""
    if button.get("argmode") == "underscore":
        value = value.replace(" ", "_")
    return command.replace("{arg}", value)


def validate_definition(catalog: dict, definition: dict) -> None:
    labels = {button["label"] for button in catalog["buttons"]}
    checks = {check["label"] for check in definition["checks"]}
    missing, unknown = labels - checks, checks - labels
    if missing or unknown:
        raise LiveTestError(f"catalog/check mismatch; missing={sorted(missing)}, unknown={sorted(unknown)}")
    # A check with nothing to compare against would pass on any reply, even an error.
    vague = [check["label"] for check in definition["checks"] if not check.get("expect") and not check.get("player_required")]
    if vague:
        raise LiveTestError(f"checks without an expected result: {sorted(vague)}")


def fresh_password() -> str:
    return secrets.token_urlsafe(24)


def mask_secrets(value: object, secrets_to_mask: list[str]) -> object:
    if isinstance(value, str):
        for secret in secrets_to_mask:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, list):
        return [mask_secrets(item, secrets_to_mask) for item in value]
    if isinstance(value, dict):
        return {key: mask_secrets(item, secrets_to_mask) for key, item in value.items()}
    return value


def verify_file(path: Path, item: dict) -> None:
    algorithm = "sha256" if item.get("sha256") else "sha1" if item.get("sha1") else ""
    expected = item.get(algorithm, "")
    if not expected:
        raise LiveTestError(f"baseline checksum is empty for {item['name']}")
    actual = hashlib.new(algorithm, path.read_bytes()).hexdigest()
    if not secrets.compare_digest(actual.lower(), expected.lower()):
        raise LiveTestError(f"checksum mismatch for {item['name']}")


def download_verified(item: dict, destination: Path) -> None:
    if not item.get("url"):
        raise LiveTestError(f"baseline URL is empty for {item['name']}")
    if not (item.get("sha256") or item.get("sha1")):
        raise LiveTestError(f"baseline checksum is empty for {item['name']}")
    partial = destination.with_suffix(destination.suffix + ".part")
    with urllib.request.urlopen(item["url"], timeout=120) as response, partial.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
    verify_file(partial, item)
    partial.replace(destination)


def ports_free(ports: list[int]) -> tuple[bool, str]:
    for port in ports:
        for host in ("0.0.0.0", "127.0.0.1"):
            for kind, socktype in (("TCP", socket.SOCK_STREAM), ("UDP", socket.SOCK_DGRAM)):
                probe = socket.socket(socket.AF_INET, socktype)
                try:
                    probe.bind((host, port))
                except OSError as exc:
                    return False, f"{kind} {host}:{port} is busy ({exc})"
                finally:
                    probe.close()
    return True, ""


def parse_netstat(output: str) -> list[dict]:
    rows = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 4 or fields[0].upper() != "TCP":
            continue
        local, pid = fields[1], fields[-1]
        try:
            port = int(local.rsplit(":", 1)[1].rstrip("]"))
            rows.append({"address": local.rsplit(":", 1)[0].strip("[]"), "port": port, "pid": int(pid), "state": fields[-2]})
        except (ValueError, IndexError):
            continue
    return rows


def listener_confirmed(netstat: str, port: int, pids: set[int], require_loopback: bool) -> bool:
    for row in parse_netstat(netstat):
        if row["port"] != port or row["pid"] not in pids or row["state"].upper() != "LISTENING":
            continue
        if not require_loopback or row["address"] in {"127.0.0.1", "::1"}:
            return True
    return False


def parse_appmanifest_build_id(path: Path) -> str | None:
    match = re.search(r'"buildid"\s+"([^"]+)"', path.read_text(encoding="utf-8", errors="replace"))
    return match.group(1) if match else None


def classify_check(expectation: dict, reply: str | None, exception: Exception | None, evidence: bool) -> tuple[str, str]:
    if expectation.get("disconnect"):
        return ("pass", "disconnect and evidence confirmed") if evidence else ("fail", "disconnect evidence missing")
    if exception:
        return "fail", f"{type(exception).__name__}: {exception}"
    if expectation.get("player_required"):
        return "unverified", "requires a live player"
    if not reply and not expectation.get("empty_reply_ok"):
        return "fail", "empty reply"
    pattern = expectation.get("reply")
    if pattern and not re.search(pattern, reply, re.IGNORECASE):
        return "fail", "reply did not match expectation"
    if any(key in expectation for key in ("file_contains", "file_not_contains", "modified", "modified_glob", "log_contains", "log_contains_arg")) and not evidence:
        return "unverified" if expectation.get("unverified_if_no_evidence") else "fail", "evidence probe did not confirm effect"
    return "pass", "reply and evidence matched"


class WindowsJob:
    """Kill a Windows process tree when the job handle closes."""
    KILL_ON_CLOSE = 0x00002000

    def __init__(self) -> None:
        if os.name != "nt":
            raise LiveTestError("live runs require Windows")
        self.handle = ctypes.windll.kernel32.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError()
        info = (ctypes.c_byte * 144)()
        ctypes.memmove(ctypes.addressof(info) + 16, ctypes.byref(ctypes.c_uint(self.KILL_ON_CLOSE)), 4)
        if not ctypes.windll.kernel32.SetInformationJobObject(self.handle, 9, ctypes.byref(info), len(info)):
            raise ctypes.WinError()

    def assign(self, process: subprocess.Popen) -> None:
        if not ctypes.windll.kernel32.AssignProcessToJobObject(self.handle, process._handle):
            raise ctypes.WinError()

    def pids(self) -> set[int]:
        """Return the current JobObjectBasicProcessIdList without touching processes."""
        buffer = (ctypes.c_byte * 4096)()
        returned = ctypes.c_uint()
        if not ctypes.windll.kernel32.QueryInformationJobObject(
                self.handle, 3, ctypes.byref(buffer), len(buffer), ctypes.byref(returned)):
            raise ctypes.WinError()
        assigned = ctypes.c_uint.from_buffer(buffer, 0).value
        listed = ctypes.c_uint.from_buffer(buffer, 4).value
        offset, width = 8, ctypes.sizeof(ctypes.c_size_t)
        return {int(ctypes.c_size_t.from_buffer(buffer, offset + index * width).value)
                for index in range(min(assigned, listed))}

    def terminate(self) -> None:
        if not ctypes.windll.kernel32.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError()

    def close(self) -> None:
        if self.handle:
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def write_results(run_dir: Path, result: dict, passwords: list[str]) -> None:
    clean = mask_secrets(result, passwords)
    (run_dir / "results.json").write_text(json.dumps(clean, indent=2) + "\n", encoding="utf-8")
    lines = [f"# {clean['game']} live test", "", f"Result: **{clean['status']}**", "", "| Check | Status | Expected | Actual |", "|---|---|---|---|"]
    lines.extend(f"| {item['label']} | {item['status']} | {item.get('expected', '')} | {item.get('actual', '')} |" for item in clean["checks"])
    (run_dir / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
