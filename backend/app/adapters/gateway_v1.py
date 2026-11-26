"""Read-only HTTP client for the jcheng-system ``/v1`` gateway.

The Studio is an L4 operator console. This client is the ecosystem-facing
transport: HTTP only, Bearer authentication, and no DDS/RMW, device or motion
path. It implements the read-only slice of the published contract
(``jcheng-interfaces`` ``jcheng-gateway.v1.yaml``):

- ``GET /v1/health``      unauthenticated liveness
- ``GET /v1/contract``    deployed contract identity
- ``GET /v1/state``       latest robot state
- ``GET /v1/safety``      current safety state

There is deliberately no POST, lease, motion or stop method. The Studio never
holds a control lease and never calls the motion-execution endpoint.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

# Pinned with jcheng-interfaces v0.1.0. `tests/test_contract.py` checks that
# this value still matches `capability.yaml`; a contract change updates both.
EXPECTED_CONTRACT_HASH = (
    "sha256:0b8d72ced7f54d444d6851d8b99a51a78e2bed3f687324f51a15f76557f24b42"
)

HEALTH_STATUSES = {"ok", "degraded", "fault"}
SAFETY_STATES = {
    "DISARMED",
    "ARMED_IDLE",
    "MOTION_ACTIVE",
    "STOP_LATCHED",
    "FAULT",
}
STATE_TRUTHS = {"measured", "commanded"}
STATE_VERIFICATIONS = {"bus", "timed", "open_loop"}


class GatewayV1Error(RuntimeError):
    """A failed read against the ``/v1`` gateway."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "code": self.code or "GATEWAY_V1_ERROR",
            "message": str(self),
            "status": self.status,
        }


@dataclass(frozen=True)
class GatewayV1Probe:
    """One read-only health/contract/state/safety probe."""

    configured: bool
    reachable: bool
    authenticated: bool
    read_only: bool
    ready: bool
    health_status: str | None = None
    contract_hash: str | None = None
    contract_match: bool | None = None
    state_fresh: bool | None = None
    state_truth: str | None = None
    state_seq: int | None = None
    joint_count: int | None = None
    battery_voltage: float | None = None
    safety_state: str | None = None
    stop_latched: bool | None = None
    violation_codes: list[str] = field(default_factory=list)
    system_version: str | None = None
    profile_hash: str | None = None
    latency_ms: int | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def not_configured(cls) -> "GatewayV1Probe":
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
                    "endpoint": None,
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
            "health_status": self.health_status,
            "contract_hash": self.contract_hash,
            "contract_match": self.contract_match,
            "state_fresh": self.state_fresh,
            "state_truth": self.state_truth,
            "state_seq": self.state_seq,
            "joint_count": self.joint_count,
            "battery_voltage": self.battery_voltage,
            "safety_state": self.safety_state,
            "stop_latched": self.stop_latched,
            "violation_codes": list(self.violation_codes),
            "system_version": self.system_version,
            "profile_hash": self.profile_hash,
            "latency_ms": self.latency_ms,
            "errors": list(self.errors),
        }


class GatewayV1Client:
    """Read-only ``/v1`` client. No method mutates gateway state."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout_s: float = 2.0,
        expected_contract_hash: str | None = EXPECTED_CONTRACT_HASH,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token.strip() if token else None
        self.timeout_s = float(timeout_s)
        self.expected_contract_hash = expected_contract_hash
        # Do not let an operator HTTP proxy silently answer a local gateway.
        self._opener = build_opener(ProxyHandler({}))

    # ------------------------------------------------------------ public reads
    def health(self) -> dict[str, Any]:
        payload = self._get("/v1/health", authenticated=False)
        self._validate_health(payload)
        return payload

    def contract(self) -> dict[str, Any]:
        payload = self._get("/v1/contract")
        self._validate_contract(payload)
        return payload

    def state(self) -> dict[str, Any]:
        payload = self._get("/v1/state")
        self._validate_state(payload)
        return payload

    def safety(self) -> dict[str, Any]:
        payload = self._get("/v1/safety")
        # The read-only gateway wraps the state in
        # {"safety": SafetyState, "authoritative": false}; the motion gateway
        # returns the bare SafetyState. Normalise both to the contract shape.
        if isinstance(payload.get("safety"), dict):
            payload = payload["safety"]
        self._validate_safety(payload)
        return payload

    # ------------------------------------------------------------------- probe
    def probe(self) -> GatewayV1Probe:
        started = time.monotonic()
        errors: list[dict[str, Any]] = []

        health = self._attempt(self.health, errors, "/v1/health")
        if health is None:
            return GatewayV1Probe(
                configured=True,
                reachable=False,
                authenticated=False,
                read_only=True,
                ready=False,
                latency_ms=self._elapsed_ms(started),
                errors=errors,
            )

        health_status = str(health.get("status"))
        health_contract = health.get("contract") or {}
        health_safety = health.get("safety") or {}

        contract = self._attempt(self.contract, errors, "/v1/contract")
        state = self._attempt(self.state, errors, "/v1/state")
        safety = self._attempt(self.safety, errors, "/v1/safety")

        contract_hash = None
        system_version = None
        profile_hash = None
        if isinstance(contract, dict):
            contract_body = contract.get("contract")
            if isinstance(contract_body, dict):
                contract_hash = str(contract_body.get("contract_hash", "")) or None
            system_version = contract.get("system_version")
            profile_hash = contract.get("profile_hash")
        if contract_hash is None and isinstance(health_contract, dict):
            contract_hash = str(health_contract.get("contract_hash", "")) or None

        contract_match: bool | None = None
        if contract_hash is not None and self.expected_contract_hash:
            contract_match = contract_hash == self.expected_contract_hash
        elif contract_hash is not None:
            contract_match = None
        if contract_match is False:
            errors.append(
                {
                    "code": "CONTRACT_MISMATCH",
                    "message": (
                        "gateway contract does not match the pinned interface "
                        f"hash {self.expected_contract_hash}"
                    ),
                    "status": None,
                    "endpoint": "/v1/contract",
                }
            )

        state_fresh = None
        state_truth = None
        state_seq = None
        joint_count = None
        battery_voltage = None
        if isinstance(state, dict):
            state_fresh = bool(state.get("fresh"))
            state_truth = str(state.get("truth")) if state.get("truth") else None
            seq = state.get("seq")
            state_seq = int(seq) if isinstance(seq, int) else None
            names = state.get("joint_names")
            joint_count = len(names) if isinstance(names, list) else None
            voltage = state.get("battery_voltage")
            battery_voltage = float(voltage) if isinstance(voltage, (int, float)) else None

        safety_payload = safety if isinstance(safety, dict) else health_safety
        safety_state = None
        stop_latched = None
        violation_codes: list[str] = []
        if isinstance(safety_payload, dict):
            safety_state = (
                str(safety_payload.get("state"))
                if safety_payload.get("state") is not None
                else None
            )
            stop_latched = (
                bool(safety_payload.get("stop_latched"))
                if safety_payload.get("stop_latched") is not None
                else None
            )
            codes = safety_payload.get("violation_codes")
            if isinstance(codes, list):
                violation_codes = [str(item) for item in codes]

        authenticated = contract is not None and state is not None and safety is not None
        ready = (
            health_status == "ok"
            and authenticated
            and contract_match is True
            and state_fresh is True
            and stop_latched is False
            and safety_state not in {"STOP_LATCHED", "FAULT"}
        )

        return GatewayV1Probe(
            configured=True,
            reachable=True,
            authenticated=authenticated,
            read_only=True,
            ready=ready,
            health_status=health_status,
            contract_hash=contract_hash,
            contract_match=contract_match,
            state_fresh=state_fresh,
            state_truth=state_truth,
            state_seq=state_seq,
            joint_count=joint_count,
            battery_voltage=battery_voltage,
            safety_state=safety_state,
            stop_latched=stop_latched,
            violation_codes=violation_codes,
            system_version=str(system_version) if system_version is not None else None,
            profile_hash=str(profile_hash) if profile_hash is not None else None,
            latency_ms=self._elapsed_ms(started),
            errors=errors,
        )

    # ---------------------------------------------------------------- transport
    def _get(self, path: str, *, authenticated: bool = True) -> dict[str, Any]:
        if authenticated and not self.token:
            raise GatewayV1Error(
                "gateway v1 token is not configured",
                code="TOKEN_MISSING",
            )
        headers = {"Accept": "application/json"}
        if authenticated:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(f"{self.base_url}{path}", headers=headers, method="GET")
        try:
            with self._opener.open(request, timeout=self.timeout_s) as response:
                raw = response.read()
        except HTTPError as exc:
            code, message = self._error_payload(exc)
            raise GatewayV1Error(
                message or f"gateway returned HTTP {exc.code} for {path}",
                status=exc.code,
                code=code or f"HTTP_{exc.code}",
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise GatewayV1Error(
                f"gateway request failed for {path}: {exc}",
                code="UNREACHABLE",
            ) from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GatewayV1Error(
                f"gateway returned invalid JSON for {path}",
                code="INVALID_JSON",
            ) from exc
        if not isinstance(payload, dict):
            raise GatewayV1Error(
                f"gateway returned a non-object JSON payload for {path}",
                code="INVALID_PAYLOAD",
            )
        return payload

    @staticmethod
    def _error_payload(exc: HTTPError) -> tuple[str | None, str | None]:
        try:
            data = json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, OSError):
            return None, None
        error = data.get("error") if isinstance(data, dict) else None
        if not isinstance(error, dict):
            return None, None
        code = error.get("code")
        message = error.get("message")
        return (
            str(code) if code is not None else None,
            str(message) if message is not None else None,
        )

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return int(round((time.monotonic() - started) * 1000))

    def _attempt(
        self,
        read: Callable[[], dict[str, Any]],
        errors: list[dict[str, Any]],
        endpoint: str,
    ) -> dict[str, Any] | None:
        try:
            return read()
        except GatewayV1Error as exc:
            item = exc.to_public_dict()
            item["endpoint"] = endpoint
            errors.append(item)
            return None

    # ---------------------------------------------------------------- validation
    @staticmethod
    def _require(payload: dict[str, Any], keys: tuple[str, ...], label: str) -> None:
        missing = [key for key in keys if key not in payload]
        if missing:
            raise GatewayV1Error(
                f"{label} payload is missing {missing}",
                code="INVALID_PAYLOAD",
            )

    @classmethod
    def _validate_health(cls, payload: dict[str, Any]) -> None:
        cls._require(payload, ("status", "contract", "safety"), "health")
        if payload.get("status") not in HEALTH_STATUSES:
            raise GatewayV1Error("health.status is not a known value", code="INVALID_PAYLOAD")
        if not isinstance(payload.get("contract"), dict):
            raise GatewayV1Error("health.contract must be an object", code="INVALID_PAYLOAD")
        if not isinstance(payload["contract"].get("contract_hash"), str):
            raise GatewayV1Error(
                "health.contract.contract_hash is required",
                code="INVALID_PAYLOAD",
            )
        if not isinstance(payload.get("safety"), dict):
            raise GatewayV1Error("health.safety must be an object", code="INVALID_PAYLOAD")

    @classmethod
    def _validate_contract(cls, payload: dict[str, Any]) -> None:
        cls._require(payload, ("contract", "profile_hash", "system_version"), "contract")
        if not isinstance(payload.get("contract"), dict):
            raise GatewayV1Error("contract.contract must be an object", code="INVALID_PAYLOAD")
        if not isinstance(payload["contract"].get("contract_hash"), str):
            raise GatewayV1Error(
                "contract.contract.contract_hash is required",
                code="INVALID_PAYLOAD",
            )

    @classmethod
    def _validate_state(cls, payload: dict[str, Any]) -> None:
        cls._require(
            payload,
            (
                "header",
                "seq",
                "source",
                "fresh",
                "joint_names",
                "positions",
                "velocities",
                "gripper_position",
                "base_duty",
                "battery_voltage",
                "profile_hash",
                "truth",
                "verification",
            ),
            "state",
        )
        if not isinstance(payload.get("fresh"), bool):
            raise GatewayV1Error("state.fresh must be a boolean", code="INVALID_PAYLOAD")
        if payload.get("truth") not in STATE_TRUTHS:
            raise GatewayV1Error("state.truth is not a known value", code="INVALID_PAYLOAD")
        if payload.get("verification") not in STATE_VERIFICATIONS:
            raise GatewayV1Error(
                "state.verification is not a known value",
                code="INVALID_PAYLOAD",
            )
        if not isinstance(payload.get("joint_names"), list) or not isinstance(
            payload.get("positions"), list
        ):
            raise GatewayV1Error(
                "state.joint_names and state.positions must be arrays",
                code="INVALID_PAYLOAD",
            )

    @classmethod
    def _validate_safety(cls, payload: dict[str, Any]) -> None:
        cls._require(
            payload,
            (
                "header",
                "seq",
                "state",
                "stop_latched",
                "violation_codes",
                "violations",
                "config_hash",
                "profile_hash",
                "last_valid_state_at",
            ),
            "safety",
        )
        if payload.get("state") not in SAFETY_STATES:
            raise GatewayV1Error("safety.state is not a known value", code="INVALID_PAYLOAD")
        if not isinstance(payload.get("stop_latched"), bool):
            raise GatewayV1Error(
                "safety.stop_latched must be a boolean",
                code="INVALID_PAYLOAD",
            )
        if not isinstance(payload.get("violation_codes"), list):
            raise GatewayV1Error(
                "safety.violation_codes must be an array",
                code="INVALID_PAYLOAD",
            )
