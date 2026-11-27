"""Read-only client for the teleop status server (`jcheng.teleop.status.v1`).

Loopback only. The client performs one GET; it has no stop, lease or intent
call. A missing, unreachable, malformed or stale payload is reported as
degraded, never as ready.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from .loopback import require_loopback

SCHEMA = "jcheng.teleop.status.v1"
STATUS_PATH = "/v1/status"
STALE_AFTER_MS = 1000


@dataclass(frozen=True)
class TeleopStatusProbe:
    configured: bool
    reachable: bool
    valid: bool
    stale: bool
    degraded: bool
    phase: str | None = None
    input_age_ms: float | None = None
    deadman: bool | None = None
    mode: str | None = None
    profile_id: str | None = None
    profile_hash: str | None = None
    contract_hash: str | None = None
    lease_ref: str | None = None
    capture: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    latency_ms: int | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def not_configured(cls) -> "TeleopStatusProbe":
        return cls(
            configured=False,
            reachable=False,
            valid=False,
            stale=True,
            degraded=True,
            errors=[
                {
                    "code": "NOT_CONFIGURED",
                    "message": "JCHENG_TELEOP_STATUS_URL is not configured",
                }
            ],
        )

    @classmethod
    def failed(cls, code: str, message: str, **kwargs: Any) -> "TeleopStatusProbe":
        return cls(
            configured=True,
            reachable=False,
            valid=False,
            stale=True,
            degraded=True,
            errors=[{"code": code, "message": message}],
            **kwargs,
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "reachable": self.reachable,
            "valid": self.valid,
            "stale": self.stale,
            "degraded": self.degraded,
            "phase": self.phase,
            "input_age_ms": self.input_age_ms,
            "deadman": self.deadman,
            "mode": self.mode,
            "profile_id": self.profile_id,
            "profile_hash": self.profile_hash,
            "contract_hash": self.contract_hash,
            "lease_ref": self.lease_ref,
            "capture": self.capture,
            "payload": self.payload,
            "latency_ms": self.latency_ms,
            "errors": self.errors,
        }


class TeleopStatusClient:
    def __init__(self, url: str, *, timeout_s: float = 1.0) -> None:
        base = require_loopback(url, "teleop status")
        self.url = base[: -len(STATUS_PATH)] if base.endswith(STATUS_PATH) else base
        self.timeout_s = timeout_s
        self._opener = build_opener(ProxyHandler({}))

    def probe(self) -> TeleopStatusProbe:
        started = time.monotonic()
        try:
            request = Request(self.url + STATUS_PATH, method="GET")
            with self._opener.open(request, timeout=self.timeout_s) as response:
                raw = response.read(65536)
        except HTTPError as exc:
            return TeleopStatusProbe.failed(
                "HTTP_ERROR", f"status server returned {exc.code}", latency_ms=int((time.monotonic() - started) * 1000)
            )
        except (URLError, TimeoutError, OSError) as exc:
            return TeleopStatusProbe.failed("UNREACHABLE", str(exc))
        latency_ms = int((time.monotonic() - started) * 1000)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            return TeleopStatusProbe.failed(
                "INVALID_PAYLOAD", f"status is not JSON: {exc}", latency_ms=latency_ms
            )
        if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
            return TeleopStatusProbe.failed(
                "INVALID_PAYLOAD", "status schema is not jcheng.teleop.status.v1", latency_ms=latency_ms
            )
        input_state = payload.get("input")
        if not isinstance(input_state, dict) or not isinstance(input_state.get("stale"), bool):
            return TeleopStatusProbe.failed(
                "INVALID_PAYLOAD", "status has no input.stale flag", latency_ms=latency_ms
            )
        age = input_state.get("age_ms")
        stale_after = payload.get("stale_after_ms", STALE_AFTER_MS)
        stale = bool(input_state["stale"]) or (
            not isinstance(age, (int, float)) or age > float(stale_after)
        )
        return TeleopStatusProbe(
            configured=True,
            reachable=True,
            valid=True,
            stale=stale,
            degraded=stale,
            phase=str(payload.get("phase")) if payload.get("phase") else None,
            input_age_ms=float(age) if isinstance(age, (int, float)) else None,
            deadman=input_state.get("deadman")
            if isinstance(input_state.get("deadman"), bool)
            else None,
            mode=input_state.get("mode") if isinstance(input_state.get("mode"), str) else None,
            profile_id=payload.get("profile_id"),
            profile_hash=payload.get("profile_hash"),
            contract_hash=payload.get("contract_hash"),
            lease_ref=payload.get("lease_ref"),
            capture=payload.get("capture") if isinstance(payload.get("capture"), dict) else {},
            payload=payload,
            latency_ms=latency_ms,
        )
