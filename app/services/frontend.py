import json

from app.services import diagnostics


def _mark_frontend_ready(api):
    api._frontend_ready = True
    with api._debug_warnings_lock:
        warnings = api._debug_warnings
        api._debug_warnings = []
    for message in warnings:
        api._emit("debugWarning", {
            "text": message,
            "debug": diagnostics._debug_logger.is_enabled(),
        })


def _handle_debug_warning(api, message):
    with api._debug_warnings_lock:
        if not api._frontend_ready or not api._window:
            api._debug_warnings.append(message)
            return
    api._emit("debugWarning", {
        "text": message,
        "debug": diagnostics._debug_logger.is_enabled(),
    })


def _emit(api, channel, payload):
    if not api._window or not api._frontend_ready:
        return
    data = json.dumps(payload)
    js = f"window.__onPyEvent && window.__onPyEvent({json.dumps(channel)}, {data});"
    try:
        api._window.evaluate_js(js)
    except Exception:
        pass


def _log(api, server_id, text, level="out"):
    api._emit("log", {"serverId": server_id, "text": text, "level": level})
