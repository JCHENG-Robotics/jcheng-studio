"""Structured service errors shared by the API layer."""

from __future__ import annotations

from typing import Any


class ServiceError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class StorageError(RuntimeError):
    """SQLite event persistence failed. Motion must fail closed."""
