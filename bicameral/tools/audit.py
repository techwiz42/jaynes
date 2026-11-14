"""The audit log: one JSON line for every tool call, the record of what left (or was read by) this process. Never the contents of a file or a page."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import Any, Dict

# Argument values worth keeping whole: short identifiers of what was asked for. Anything else (code the model wrote, say) is recorded only as a length and a hash.
KEEP_WHOLE = {"query", "url", "repo", "path", "ref", "operation", "number", "state", "k", "max_results", "max_chars", "start_line", "end_line"}
MAX_VALUE_CHARS = 300


def summarize_args(args: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in args.items():
        if key in KEEP_WHOLE and isinstance(value, (str, int, float, bool)):
            out[key] = value[:MAX_VALUE_CHARS] if isinstance(value, str) else value
        elif isinstance(value, str):
            out[key] = {"chars": len(value), "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]}
        else:
            out[key] = type(value).__name__
    return out


class AuditLog:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)

    def record(self, *, session_id: str, turn_id: str, call_id: str, tool: str, args: Dict[str, Any], ok: bool, error: str, truncated: bool, bytes_out: int, seconds: float,
               meta: Dict[str, Any]) -> None:
        line = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "session": session_id, "turn": turn_id, "call": call_id, "tool": tool,
            "args": summarize_args(args), "ok": ok, "error": error[:200], "truncated": truncated,
            "bytes_out": bytes_out, "seconds": round(seconds, 3), **{k: v for k, v in meta.items() if k not in ("content", "text", "body")},
        }
        data = json.dumps(line, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            fd = os.open(self._path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, data.encode("utf-8"))
            finally:
                os.close(fd)
