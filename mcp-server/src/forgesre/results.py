"""Uniform tool result envelope: success / failure / partial with machine-readable codes."""

from __future__ import annotations

from typing import Any


class ToolError(Exception):
    """Raised inside tools; converted into a structured failure result."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def ok(**data: Any) -> dict[str, Any]:
    return {"success": True, "status": "success", **data}


def partial(message: str, **data: Any) -> dict[str, Any]:
    return {"success": False, "status": "partial", "message": message, **data}


def fail(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"success": False, "status": "failure", "error_code": code, "message": message, **details}


def from_error(err: ToolError) -> dict[str, Any]:
    status = {"INVALID_INPUT": "invalid_input", "TIMEOUT": "timeout"}.get(err.code, "failure")
    return {"success": False, "status": status, "error_code": err.code, "message": err.message, **err.details}
