"""Single-use preview tokens bound to a payload hash."""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..domain.enums import ErrorCode


@dataclass
class TokenCheck:
    ok: bool
    code: str | None
    detail: str


@dataclass
class _TokenRecord:
    payload_hash: str
    expires_monotonic: float
    used: bool = False


def _hash_payload(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PreviewTokenManager:
    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._tokens: dict[str, _TokenRecord] = {}

    def issue(self, payload: dict[str, Any], ttl_s: int = 30) -> tuple[str, str]:
        token = secrets.token_urlsafe(24)
        now = self._clock()
        with self._lock:
            self._cleanup_locked(now)
            self._tokens[token] = _TokenRecord(
                payload_hash=_hash_payload(payload),
                expires_monotonic=now + ttl_s,
            )
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=ttl_s)
        ).isoformat().replace("+00:00", "Z")
        return token, expires_at

    def consume(self, token: str | None, payload: dict[str, Any]) -> TokenCheck:
        if not token:
            return TokenCheck(False, ErrorCode.PREVIEW_INVALID.value, "preview token is required")
        now = self._clock()
        with self._lock:
            self._cleanup_locked(now)
            record = self._tokens.get(token)
            if record is None:
                return TokenCheck(False, ErrorCode.PREVIEW_INVALID.value, "preview token is unknown or expired")
            if record.used:
                return TokenCheck(False, ErrorCode.PREVIEW_REUSED.value, "preview token was already used")
            if now >= record.expires_monotonic:
                self._tokens.pop(token, None)
                return TokenCheck(False, ErrorCode.PREVIEW_EXPIRED.value, "preview token expired")
            if record.payload_hash != _hash_payload(payload):
                return TokenCheck(
                    False,
                    ErrorCode.PREVIEW_PAYLOAD_MISMATCH.value,
                    "payload does not match the preview",
                )
            record.used = True
            return TokenCheck(True, None, "preview token accepted")

    def _cleanup_locked(self, now: float) -> None:
        expired = [token for token, rec in self._tokens.items() if now >= rec.expires_monotonic]
        for token in expired:
            self._tokens.pop(token, None)
