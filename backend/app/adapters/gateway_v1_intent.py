"""M2b intent client: validate and execute one joint step or gripper command.

This is deliberately separate from the read-only client. It has exactly two
mutation methods and never acquires, renews or releases a lease. The operator
supplies a lease ID issued by the system layer; the system layer remains the
only motion authority.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from .gateway_v1 import EXPECTED_CONTRACT_HASH, GatewayV1Error

CONTRACT_VERSION = {
    "major": 0,
    "minor": 1,
    "patch": 0,
    "contract_hash": EXPECTED_CONTRACT_HASH,
}
MOTION_OUTCOMES = {"COMPLETED", "REJECTED", "ABORTED", "TIMEOUT", "UNKNOWN"}


class GatewayV1IntentError(GatewayV1Error):
    """A failed intent validation or execution."""


def joint_step_command(
    *,
    intent_id: str,
    idempotency_key: str,
    lease_id: str,
    source: str,
    joint: str,
    target_rad: float,
    duration_s: float,
    max_velocity_rad_s: float,
) -> dict[str, Any]:
    return {
        "intent_id": intent_id,
        "idempotency_key": idempotency_key,
        "lease_id": lease_id,
        "source": source,
        "kind": "JOINT_STEP",
        "profile": "step",
        "contract": dict(CONTRACT_VERSION),
        "joint_names": [joint],
        "target_positions": [float(target_rad)],
        "max_velocity_rad_s": float(max_velocity_rad_s),
        "duration_s": float(duration_s),
        "verify_bus": True,
        "require_measured_state": True,
    }


def gripper_command(
    *,
    intent_id: str,
    idempotency_key: str,
    lease_id: str,
    source: str,
    position_rad: float,
    duration_s: float,
    max_velocity_rad_s: float,
) -> dict[str, Any]:
    return {
        "intent_id": intent_id,
        "idempotency_key": idempotency_key,
        "lease_id": lease_id,
        "source": source,
        "kind": "GRIPPER",
        "profile": "gripper",
        "contract": dict(CONTRACT_VERSION),
        "gripper": {
            "position_rad": float(position_rad),
            "max_velocity_rad_s": float(max_velocity_rad_s),
            "duration_s": float(duration_s),
            "verify_bus": True,
        },
        "verify_bus": True,
        "require_measured_state": True,
    }


class GatewayV1IntentClient:
    """POST-only client for the two motion-intent endpoints.

    There is intentionally no lease method and no stop method. The Studio never
    acquires a lease; the operator supplies one issued by the system layer.
    """

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout_s: float = 5.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token.strip() if token else None
        self.timeout_s = float(timeout_s)
        self._opener = build_opener(ProxyHandler({}))

    # --------------------------------------------------------------- validate
    def validate_motion(self, command: dict[str, Any]) -> dict[str, Any]:
        payload = self._post("/v1/motion/validate", command)
        self._validate_report(payload)
        return payload

    # ---------------------------------------------------------------- execute
    def submit_intent(
        self,
        command: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        payload = self._post(
            "/v1/motion/execute",
            command,
            headers={"Idempotency-Key": idempotency_key},
        )
        self._validate_result(payload)
        return payload

    # ---------------------------------------------------------------- transport
    def _post(
        self,
        path: str,
        body: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if not self.token:
            raise GatewayV1IntentError(
                "gateway v1 token is not configured",
                code="TOKEN_MISSING",
            )
        all_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        if headers:
            all_headers.update(headers)
        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
            headers=all_headers,
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.timeout_s) as response:
                raw = response.read()
        except HTTPError as exc:
            code, message = self._error_payload(exc)
            raise GatewayV1IntentError(
                message or f"gateway returned HTTP {exc.code} for {path}",
                status=exc.code,
                code=code or f"HTTP_{exc.code}",
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise GatewayV1IntentError(
                f"gateway request failed for {path}: {exc}",
                code="UNREACHABLE",
            ) from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GatewayV1IntentError(
                f"gateway returned invalid JSON for {path}",
                code="INVALID_JSON",
            ) from exc
        if not isinstance(payload, dict):
            raise GatewayV1IntentError(
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

    # ---------------------------------------------------------------- validation
    @staticmethod
    def _validate_report(payload: dict[str, Any]) -> None:
        if not isinstance(payload.get("approved"), bool):
            raise GatewayV1IntentError(
                "validation.approved must be a boolean",
                code="INVALID_PAYLOAD",
            )
        if not isinstance(payload.get("reason_code"), str):
            raise GatewayV1IntentError(
                "validation.reason_code is required",
                code="INVALID_PAYLOAD",
            )
        if not isinstance(payload.get("violations", []), list):
            raise GatewayV1IntentError(
                "validation.violations must be a list",
                code="INVALID_PAYLOAD",
            )

    @staticmethod
    def _validate_result(payload: dict[str, Any]) -> None:
        required = (
            "intent_id",
            "outcome",
            "reason_code",
            "message",
            "safety",
            "measured_positions",
            "bus_verified",
            "calibration_invalidated",
            "completed_at",
            "evidence_id",
        )
        missing = [key for key in required if key not in payload]
        if missing:
            raise GatewayV1IntentError(
                f"motion result is missing {missing}",
                code="INVALID_PAYLOAD",
            )
        if payload.get("outcome") not in MOTION_OUTCOMES:
            raise GatewayV1IntentError(
                "motion result outcome is not a known value",
                code="INVALID_PAYLOAD",
            )
        if not isinstance(payload.get("bus_verified"), bool):
            raise GatewayV1IntentError(
                "motion result bus_verified must be a boolean",
                code="INVALID_PAYLOAD",
            )
        if not isinstance(payload.get("measured_positions"), list):
            raise GatewayV1IntentError(
                "motion result measured_positions must be a list",
                code="INVALID_PAYLOAD",
            )
