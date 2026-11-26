"""Read-only WebSocket client for the jcheng-system ``/v1/ws/state`` stream.

The Stream is the live half of the read-only gateway contract: one subscription
receives a ``hello`` envelope followed by ``state`` and ``safety`` envelopes.
It is HTTP-only from the Studio's side (no DDS/RMW, no device, no motion) and
never sends an intent.

The client uses the ``websockets`` sync API and a bounded reconnect loop. A
normal close or a transport failure reconnects with linear backoff up to
``max_attempts``; an authentication failure or a contract violation fails
closed without retrying.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Iterator
from urllib.parse import urlunparse, urlparse

from websockets.exceptions import (
    ConnectionClosed,
    InvalidStatus,
    InvalidURI,
    WebSocketException,
)
from websockets.sync.client import connect

from .gateway_v1 import EXPECTED_CONTRACT_HASH, GatewayV1Client, GatewayV1Error

WS_PATH = "/v1/ws/state"
WS_TYPES = {
    "hello",
    "state",
    "safety",
    "lease",
    "motion",
    "event",
    "ping",
    "pong",
}


class GatewayV1StreamError(GatewayV1Error):
    """A failed or invalid read-only WebSocket stream."""


@dataclass(frozen=True)
class GatewayV1StreamProbe:
    configured: bool
    reachable: bool
    authenticated: bool
    read_only: bool
    ready: bool
    contract_hash: str | None = None
    contract_match: bool | None = None
    state_truth: str | None = None
    state_fresh: bool | None = None
    state_seq: int | None = None
    joint_count: int | None = None
    safety_state: str | None = None
    stop_latched: bool | None = None
    message_types: list[str] = field(default_factory=list)
    latency_ms: int | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def not_configured(cls) -> "GatewayV1StreamProbe":
        return cls(
            configured=False,
            reachable=False,
            authenticated=False,
            read_only=True,
            ready=False,
            errors=[
                {
                    "code": "NOT_CONFIGURED",
                    "message": "ARMPI_GATEWAY_V1_URL is not configured",
                    "status": None,
                }
            ],
        )

    @classmethod
    def token_missing(cls) -> "GatewayV1StreamProbe":
        return cls(
            configured=True,
            reachable=False,
            authenticated=False,
            read_only=True,
            ready=False,
            errors=[
                {
                    "code": "TOKEN_MISSING",
                    "message": "gateway v1 token is not configured",
                    "status": None,
                }
            ],
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "reachable": self.reachable,
            "authenticated": self.authenticated,
            "read_only": self.read_only,
            "ready": self.ready,
            "contract_hash": self.contract_hash,
            "contract_match": self.contract_match,
            "state_truth": self.state_truth,
            "state_fresh": self.state_fresh,
            "state_seq": self.state_seq,
            "joint_count": self.joint_count,
            "safety_state": self.safety_state,
            "stop_latched": self.stop_latched,
            "message_types": list(self.message_types),
            "latency_ms": self.latency_ms,
            "errors": list(self.errors),
        }


class GatewayV1Stream:
    """A read-only subscription to ``/v1/ws/state``."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout_s: float = 2.0,
        backoff_s: float = 0.5,
        max_attempts: int = 3,
        expected_contract_hash: str | None = EXPECTED_CONTRACT_HASH,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token.strip() if token else None
        self.timeout_s = float(timeout_s)
        self.backoff_s = float(backoff_s)
        self.max_attempts = max(1, int(max_attempts))
        self.expected_contract_hash = expected_contract_hash

    @property
    def url(self) -> str:
        parsed = urlparse(self.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return urlunparse((scheme, parsed.netloc, WS_PATH, "", "", ""))

    def _connect(self):
        if not self.token:
            raise GatewayV1StreamError(
                "gateway v1 token is not configured",
                code="TOKEN_MISSING",
            )
        return connect(
            self.url,
            additional_headers={"Authorization": f"Bearer {self.token}"},
            open_timeout=self.timeout_s,
            close_timeout=1.0,
            ping_interval=None,
            proxy=None,
        )

    # ------------------------------------------------------------------ stream
    def stream(self, *, max_messages: int | None = None) -> Iterator[dict[str, Any]]:
        """Yield validated envelopes, reconnecting with bounded backoff.

        A contract violation or an authentication failure is raised, not
        retried. A normal close or transport error reconnects until
        ``max_attempts`` is exhausted.
        """
        for attempt in range(self.max_attempts):
            try:
                with self._connect() as websocket:
                    first = True
                    received = 0
                    while max_messages is None or received < max_messages:
                        raw = websocket.recv(timeout=self.timeout_s)
                        envelope = self._decode(raw)
                        self._validate(envelope, first=first)
                        first = False
                        received += 1
                        yield envelope
                    return
            except InvalidStatus as exc:
                status = getattr(exc.response, "status_code", None)
                raise GatewayV1StreamError(
                    "gateway websocket rejected the handshake",
                    status=status,
                    code="UNAUTHORIZED" if status in (401, 403) else "HANDSHAKE_REJECTED",
                ) from exc
            except (InvalidURI, ValueError) as exc:
                raise GatewayV1StreamError(
                    f"invalid gateway websocket URL: {exc}",
                    code="INVALID_URL",
                ) from exc
            except (ConnectionClosed, TimeoutError, OSError) as exc:
                if attempt >= self.max_attempts - 1:
                    raise GatewayV1StreamError(
                        "gateway websocket disconnected",
                        code="STREAM_CLOSED",
                    ) from exc
                time.sleep(self.backoff_s * (attempt + 1))
            except GatewayV1Error as exc:
                # A payload/contract error is not a transport blip.
                if isinstance(exc, GatewayV1StreamError):
                    raise
                raise GatewayV1StreamError(
                    str(exc),
                    code="STREAM_CONTRACT",
                ) from exc
            except WebSocketException as exc:
                raise GatewayV1StreamError(
                    f"gateway websocket error: {exc}",
                    code="STREAM_ERROR",
                ) from exc

    # ------------------------------------------------------------------- probe
    def probe(self, *, count: int = 3) -> GatewayV1StreamProbe:
        """Open one short subscription and validate the first ``count`` envelopes."""
        if not self.token:
            return GatewayV1StreamProbe.token_missing()
        started = time.monotonic()
        messages: list[str] = []
        hello: dict[str, Any] | None = None
        state: dict[str, Any] | None = None
        safety: dict[str, Any] | None = None
        errors: list[dict[str, Any]] = []
        reachable = False
        try:
            with self._connect() as websocket:
                reachable = True
                deadline = time.monotonic() + self.timeout_s
                while len(messages) < count and time.monotonic() < deadline:
                    raw = websocket.recv(
                        timeout=max(0.01, deadline - time.monotonic())
                    )
                    envelope = self._decode(raw)
                    self._validate(envelope, first=len(messages) == 0)
                    messages.append(str(envelope["type"]))
                    if envelope["type"] == "hello":
                        hello = envelope["payload"]
                    elif envelope["type"] == "state":
                        state = envelope["payload"]
                    elif envelope["type"] == "safety":
                        safety = envelope["payload"]
        except InvalidStatus as exc:
            status = getattr(exc.response, "status_code", None)
            return GatewayV1StreamProbe(
                configured=True,
                reachable=True,
                authenticated=False,
                read_only=True,
                ready=False,
                latency_ms=self._elapsed_ms(started),
                errors=[
                    {
                        "code": "UNAUTHORIZED" if status in (401, 403) else "HANDSHAKE_REJECTED",
                        "message": "gateway websocket rejected the handshake",
                        "status": status,
                    }
                ],
            )
        except (ConnectionClosed, TimeoutError, OSError) as exc:
            return GatewayV1StreamProbe(
                configured=True,
                reachable=reachable,
                authenticated=False,
                read_only=True,
                ready=False,
                message_types=messages,
                latency_ms=self._elapsed_ms(started),
                errors=[
                    {
                        "code": "STREAM_CLOSED",
                        "message": str(exc) or "gateway websocket disconnected",
                        "status": None,
                    }
                ],
            )
        except (InvalidURI, ValueError) as exc:
            return GatewayV1StreamProbe(
                configured=True,
                reachable=False,
                authenticated=False,
                read_only=True,
                ready=False,
                latency_ms=self._elapsed_ms(started),
                errors=[
                    {
                        "code": "INVALID_URL",
                        "message": str(exc),
                        "status": None,
                    }
                ],
            )
        except GatewayV1Error as exc:
            errors.append(
                {
                    "code": "STREAM_CONTRACT",
                    "message": str(exc),
                    "status": None,
                }
            )
        except WebSocketException as exc:
            errors.append(
                {
                    "code": "STREAM_ERROR",
                    "message": str(exc),
                    "status": None,
                }
            )

        if hello is None:
            errors.append(
                {
                    "code": "STREAM_INCOMPLETE",
                    "message": "stream did not start with a hello envelope",
                    "status": None,
                }
            )
        if state is None:
            errors.append(
                {
                    "code": "STREAM_INCOMPLETE",
                    "message": "no state envelope received",
                    "status": None,
                }
            )
        if safety is None:
            errors.append(
                {
                    "code": "STREAM_INCOMPLETE",
                    "message": "no safety envelope received",
                    "status": None,
                }
            )

        contract_hash = None
        contract_match: bool | None = None
        if isinstance(hello, dict):
            contract = hello.get("contract")
            if isinstance(contract, dict):
                value = contract.get("contract_hash")
                contract_hash = str(value) if value else None
        if contract_hash is not None and self.expected_contract_hash:
            contract_match = contract_hash == self.expected_contract_hash
            if contract_match is False:
                errors.append(
                    {
                        "code": "CONTRACT_MISMATCH",
                        "message": (
                            "stream contract does not match the pinned interface "
                            f"hash {self.expected_contract_hash}"
                        ),
                        "status": None,
                    }
                )

        state_fresh = None
        state_truth = None
        state_seq = None
        joint_count = None
        if isinstance(state, dict):
            state_fresh = bool(state.get("fresh"))
            state_truth = str(state["truth"]) if state.get("truth") else None
            seq = state.get("seq")
            state_seq = int(seq) if isinstance(seq, int) else None
            names = state.get("joint_names")
            joint_count = len(names) if isinstance(names, list) else None

        safety_state = None
        stop_latched = None
        if isinstance(safety, dict):
            safety_state = (
                str(safety.get("state")) if safety.get("state") is not None else None
            )
            stop_latched = (
                bool(safety.get("stop_latched"))
                if safety.get("stop_latched") is not None
                else None
            )

        ready = (
            reachable
            and hello is not None
            and state_fresh is True
            and stop_latched is False
            and safety_state not in {"STOP_LATCHED", "FAULT"}
            and contract_match is not False
        )
        return GatewayV1StreamProbe(
            configured=True,
            reachable=reachable,
            authenticated=hello is not None,
            read_only=True,
            ready=ready,
            contract_hash=contract_hash,
            contract_match=contract_match,
            state_truth=state_truth,
            state_fresh=state_fresh,
            state_seq=state_seq,
            joint_count=joint_count,
            safety_state=safety_state,
            stop_latched=stop_latched,
            message_types=messages,
            latency_ms=self._elapsed_ms(started),
            errors=errors,
        )

    # ---------------------------------------------------------------- validation
    @staticmethod
    def _decode(raw: str | bytes) -> dict[str, Any]:
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GatewayV1StreamError(
                "gateway websocket returned invalid JSON",
                code="STREAM_CONTRACT",
            ) from exc
        if not isinstance(value, dict):
            raise GatewayV1StreamError(
                "gateway websocket returned a non-object envelope",
                code="STREAM_CONTRACT",
            )
        return value

    @classmethod
    def _validate(cls, envelope: dict[str, Any], *, first: bool = False) -> None:
        if envelope.get("v") != 1:
            raise GatewayV1StreamError(
                "websocket envelope v must be 1",
                code="STREAM_CONTRACT",
            )
        kind = envelope.get("type")
        if kind not in WS_TYPES:
            raise GatewayV1StreamError(
                f"unknown websocket envelope type {kind!r}",
                code="STREAM_CONTRACT",
            )
        if not isinstance(envelope.get("id"), str) or not envelope["id"]:
            raise GatewayV1StreamError(
                "websocket envelope id is required",
                code="STREAM_CONTRACT",
            )
        if not isinstance(envelope.get("ts"), str) or not envelope["ts"]:
            raise GatewayV1StreamError(
                "websocket envelope ts is required",
                code="STREAM_CONTRACT",
            )
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            raise GatewayV1StreamError(
                "websocket envelope payload must be an object",
                code="STREAM_CONTRACT",
            )
        if first and kind != "hello":
            raise GatewayV1StreamError(
                "websocket stream must start with a hello envelope",
                code="STREAM_CONTRACT",
            )
        if kind == "hello":
            contract = payload.get("contract")
            if not isinstance(contract, dict) or not isinstance(
                contract.get("contract_hash"), str
            ):
                raise GatewayV1StreamError(
                    "hello payload must carry contract.contract_hash",
                    code="STREAM_CONTRACT",
                )
        elif kind == "state":
            GatewayV1Client._validate_state(payload)
        elif kind == "safety":
            GatewayV1Client._validate_safety(payload)

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return int(round((time.monotonic() - started) * 1000))
