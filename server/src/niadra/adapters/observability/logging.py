"""JSON logs to stdout, one line per record, collected by the log shipper.

Only ids, routes, counts and timings are logged: never message text, handles or other personal data
(5.20). The request id travels in a context variable so every line of a request carries it.
"""

import contextvars
import logging
import sys
import traceback
from typing import Any

import orjson

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if request_id := request_id_var.get():
            entry["request_id"] = request_id
        for key in (
            "route",
            "method",
            "status",
            "duration_ms",
            "task",
            "space_id",
            "attempt",
            "stages",
            "read_path",
            "db_statements",
        ):
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        if record.exc_info:
            entry["error"] = safe_traceback(record.exc_info)
        return orjson.dumps(entry, default=str).decode()


def safe_traceback(exc_info: Any) -> str:
    """Where it failed and what failed, never why in its own words: an exception's message may quote a
    value (a database error names the key that collided, a validation error the input it refused)."""
    _, error, _ = exc_info
    lines: list[str] = []
    seen: set[int] = set()
    while isinstance(error, BaseException) and id(error) not in seen:
        seen.add(id(error))
        frames = traceback.extract_tb(error.__traceback__)
        lines += [f"  {f.filename}:{f.lineno} in {f.name}" for f in frames]
        lines.append(f"{type(error).__module__}.{type(error).__qualname__}")
        error = error.__cause__ or error.__context__
        if error is not None:
            lines.append("caused by:")
    return "\n".join(lines)


def configure(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for noisy in ("uvicorn.access", "httpx", "aio_pika", "aiormq"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
