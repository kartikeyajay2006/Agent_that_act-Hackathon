"""Structured JSON logging shared by the demo services.

Every log line is a single JSON object on stdout so the Docker Engine log API
can serve it to the MCP server without any extra log pipeline.
"""

from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timezone
from typing import Any

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}


class JsonLogger:
    def __init__(self, service: str, version: str | None = None, min_level: str = "INFO") -> None:
        self.service = service
        self.version = version
        self.min_level = _LEVELS.get(min_level.upper(), 20)
        self._lock = threading.Lock()

    def _emit(self, level: str, event: str, **fields: Any) -> None:
        if _LEVELS[level] < self.min_level:
            return
        record: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "service": self.service,
            "level": level,
            "event": event,
        }
        if self.version:
            record["version"] = self.version
        record.update(fields)
        line = json.dumps(record, default=str)
        with self._lock:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()

    def debug(self, event: str, **fields: Any) -> None:
        self._emit("DEBUG", event, **fields)

    def info(self, event: str, **fields: Any) -> None:
        self._emit("INFO", event, **fields)

    def warn(self, event: str, **fields: Any) -> None:
        self._emit("WARN", event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        self._emit("ERROR", event, **fields)
