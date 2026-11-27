"""The studio's single system write: request the system motion stop.

This is the only non-GET call the operator console makes to the ecosystem
gateway. It calls `POST /v1/motion/stop`, which latches the system safety stop;
it does not acquire a lease, execute motion or call the studio-local adapter.
The backend holds the bearer token; the browser never sees it.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from .loopback import require_loopback

STOP_PATH = "/v1/motion/stop"


class SystemStopClient:
    def __init__(self, url: str, token: str | None, *, timeout_s: float = 2.0) -> None:
        base = require_loopback(url, "system stop")
        self.url = base[: -len(STOP_PATH)] if base.endswith(STOP_PATH) else base
        self.token = (token or "").strip()
        self.timeout_s = timeout_s
        self._opener = build_opener(ProxyHandler({}))

    def request(self, operator_id: str, reason: str = "") -> dict[str, Any]:
        if not operator_id.strip():
            raise ValueError("operator_id is required for a system stop")
        if not self.token:
            raise ValueError("the system stop token is not configured")
        body = json.dumps({"operator_id": operator_id, "reason": reason}).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        started = time.monotonic()
        try:
            request = Request(self.url + STOP_PATH, data=body, headers=headers, method="POST")
            with self._opener.open(request, timeout=self.timeout_s) as response:
                raw = response.read(65536)
                status = response.status
        except HTTPError as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            detail: dict[str, Any] = {}
            try:
                detail = json.loads(exc.read() or b"{}")
            except (json.JSONDecodeError, OSError):
                detail = {}
            return {
                "requested": True,
                "accepted": False,
                "latency_ms": latency_ms,
                "status_code": exc.code,
                "error": detail.get("error", {"code": "HTTP_ERROR", "message": str(exc)}),
            }
        except (URLError, TimeoutError, OSError) as exc:
            return {
                "requested": True,
                "accepted": False,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "status_code": None,
                "error": {"code": "UNREACHABLE", "message": str(exc)},
            }
        latency_ms = int((time.monotonic() - started) * 1000)
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            payload = {}
        return {
            "requested": True,
            "accepted": bool(payload.get("accepted")),
            "latency_ms": latency_ms,
            "status_code": status,
            "result": payload,
        }
