"""Single-holder control lease with TTL and heartbeat."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from ..domain.enums import ErrorCode
from ..domain.models import ControlLease, utc_now_iso


class LeaseConflict(RuntimeError):
    def __init__(self, owner: str):
        super().__init__(f"control lease already held by {owner}")
        self.owner = owner


@dataclass
class LeaseCheck:
    ok: bool
    code: str | None
    detail: str
    lease: ControlLease | None = None


def _iso_after(seconds: float) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=seconds)
    ).isoformat().replace("+00:00", "Z")


class LeaseManager:
    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._lease: ControlLease | None = None

    def _expired(self, lease: ControlLease) -> bool:
        return self._clock() >= lease.expires_monotonic

    def acquire(self, session_id: str, ttl_s: int) -> ControlLease:
        if not session_id or not session_id.strip():
            raise ValueError("session_id is required")
        if ttl_s <= 0:
            raise ValueError("ttl_s must be positive")
        with self._lock:
            if self._lease is not None and not self._expired(self._lease):
                if self._lease.session_id != session_id:
                    raise LeaseConflict(self._lease.session_id)
                # Same session re-acquires: renew instead of creating a second lease.
                self._lease.ttl_s = ttl_s
                self._lease.expires_monotonic = self._clock() + ttl_s
                self._lease.expires_at = _iso_after(ttl_s)
                return self._lease

            now = self._clock()
            lease = ControlLease(
                lease_id=uuid.uuid4().hex,
                session_id=session_id,
                acquired_at=utc_now_iso(),
                expires_at=_iso_after(ttl_s),
                ttl_s=ttl_s,
                expires_monotonic=now + ttl_s,
            )
            self._lease = lease
            return lease

    def heartbeat(self, lease_id: str, session_id: str, ttl_s: int | None = None) -> LeaseCheck:
        with self._lock:
            if self._lease is None:
                return LeaseCheck(False, ErrorCode.NO_LEASE.value, "没有活动控制权租约")
            if self._lease.lease_id != lease_id:
                return LeaseCheck(False, ErrorCode.NO_LEASE.value, "lease_id 不匹配")
            if self._lease.session_id != session_id:
                return LeaseCheck(False, ErrorCode.LEASE_CONFLICT.value, "租约属于其他会话")
            if self._expired(self._lease):
                self._lease = None
                return LeaseCheck(False, ErrorCode.LEASE_EXPIRED.value, "控制权租约已过期")
            ttl = ttl_s or self._lease.ttl_s
            self._lease.ttl_s = ttl
            self._lease.expires_monotonic = self._clock() + ttl
            self._lease.expires_at = _iso_after(ttl)
            return LeaseCheck(True, None, "租约已续期", self._lease)

    def release(self, lease_id: str, session_id: str) -> LeaseCheck:
        with self._lock:
            if self._lease is None:
                return LeaseCheck(True, None, "没有活动租约")
            if self._lease.lease_id != lease_id or self._lease.session_id != session_id:
                return LeaseCheck(False, ErrorCode.LEASE_CONFLICT.value, "租约不属于此会话")
            self._lease = None
            return LeaseCheck(True, None, "租约已释放")

    def check(self, lease_id: str | None, session_id: str | None) -> LeaseCheck:
        with self._lock:
            if not lease_id or not session_id:
                return LeaseCheck(False, ErrorCode.NO_LEASE.value, "需要控制权租约")
            if self._lease is None:
                return LeaseCheck(False, ErrorCode.NO_LEASE.value, "没有活动控制权租约")
            if self._lease.lease_id != lease_id:
                return LeaseCheck(False, ErrorCode.NO_LEASE.value, "lease_id 不匹配")
            if self._lease.session_id != session_id:
                return LeaseCheck(False, ErrorCode.LEASE_CONFLICT.value, "租约属于其他会话")
            if self._expired(self._lease):
                self._lease = None
                return LeaseCheck(False, ErrorCode.LEASE_EXPIRED.value, "控制权租约已过期")
            return LeaseCheck(True, None, "租约有效", self._lease)

    def revoke(self, reason: str = "revoked") -> None:
        with self._lock:
            self._lease = None

    def owner(self) -> str | None:
        with self._lock:
            if self._lease is None or self._expired(self._lease):
                return None
            return self._lease.session_id

    def snapshot(self) -> dict | None:
        with self._lock:
            if self._lease is None or self._expired(self._lease):
                return None
            return {
                "lease_id": self._lease.lease_id,
                "session_id": self._lease.session_id,
                "acquired_at": self._lease.acquired_at,
                "expires_at": self._lease.expires_at,
                "ttl_s": self._lease.ttl_s,
            }
