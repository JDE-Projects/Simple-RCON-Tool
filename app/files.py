import json
import os
import tempfile
import time

from app.services import diagnostics


def _write_json_atomic(path, data, **dump_kwargs) -> bool:
    """Write JSON data without changing the destination unless replacement succeeds."""
    try:
        contents = json.dumps(data, **dump_kwargs)
    except Exception as exc:
        diagnostics.debug_log(f"_write_json_atomic serialize failed: {type(exc).__name__}: {exc}")
        return False

    temp_path = None
    file_handle = None
    file_descriptor = None
    step = "create"
    try:
        destination_folder = os.path.dirname(os.path.abspath(path))
        destination_name = os.path.basename(path)
        file_descriptor, temp_path = tempfile.mkstemp(
            dir=destination_folder,
            prefix=f".{destination_name}.",
            suffix=".tmp",
        )
        step = "open"
        file_handle = os.fdopen(file_descriptor, "w", encoding="utf-8")
        file_descriptor = None
        step = "write"
        file_handle.write(contents)
        step = "flush"
        file_handle.flush()
        step = "fsync"
        os.fsync(file_handle.fileno())
        step = "close"
        file_handle.close()
        file_handle = None
        step = "replace"
        os.replace(temp_path, path)
        temp_path = None
        return True
    except Exception as exc:
        diagnostics.debug_log(f"_write_json_atomic {step} failed: {type(exc).__name__}: {exc}")
        return False
    finally:
        if file_handle is not None:
            try:
                file_handle.close()
            except Exception as exc:
                diagnostics.debug_log(f"_write_json_atomic cleanup close failed: {type(exc).__name__}: {exc}")
        elif file_descriptor is not None:
            try:
                os.close(file_descriptor)
            except Exception as exc:
                diagnostics.debug_log(f"_write_json_atomic cleanup close failed: {type(exc).__name__}: {exc}")
        if temp_path is not None:
            try:
                os.remove(temp_path)
            except FileNotFoundError:
                pass
            except Exception as exc:
                diagnostics.debug_log(f"_write_json_atomic cleanup remove failed: {type(exc).__name__}: {exc}")


def _backup_path(path):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = f"{path}.bad-{stamp}"
    candidate = base
    suffix = 2
    while os.path.exists(candidate):
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate
