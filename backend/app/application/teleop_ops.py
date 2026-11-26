"""M5 teleop panel operations: status read and the single system stop write.

Status is a loopback GET. The stop is the only non-GET call the console makes
to the ecosystem gateway; it calls `/v1/motion/stop` (which latches the system
safety stop) and never touches the studio-local adapter or a lease. Both are
opt-in through settings and loopback-only.
"""

from __future__ import annotations

from typing import Any

from ..adapters.system_stop import SystemStopClient
from ..adapters.teleop_status import TeleopStatusClient, TeleopStatusProbe
from ..errors import ServiceError


class TeleopOperations:
    def teleop_status(self) -> dict[str, Any]:
        url = self.settings.teleop_status_url
        if not url:
            return TeleopStatusProbe.not_configured().to_public_dict()
        try:
            client = TeleopStatusClient(
                url, timeout_s=self.settings.teleop_status_timeout_s
            )
        except ValueError as exc:
            probe = TeleopStatusProbe.failed("NON_LOOPBACK", str(exc))
            return probe.to_public_dict()
        return client.probe().to_public_dict()

    def system_stop(self, *, operator_id: str, reason: str = "") -> dict[str, Any]:
        if not self.settings.system_stop_enabled:
            raise ServiceError(
                "SYSTEM_STOP_DISABLED",
                "the system stop is not enabled; set ARMPI_SYSTEM_STOP=1 and "
                "configure the /v1 gateway",
                status_code=409,
            )
        url = self.settings.gateway_v1_url
        if not url:
            raise ServiceError(
                "SYSTEM_STOP_DISABLED",
                "ARMPI_GATEWAY_V1_URL is not configured",
                status_code=409,
            )
        try:
            client = SystemStopClient(
                url, self.settings.gateway_v1_token, timeout_s=2.0
            )
        except ValueError as exc:
            raise ServiceError(
                "SYSTEM_STOP_CONFIG", str(exc), status_code=409
            ) from exc
        result = client.request(operator_id, reason)
        storage_error = None
        try:
            self._append(
                self._event(
                    "SYSTEM_STOP",
                    f"stop_{operator_id}"[:64],
                    "api",
                    "accepted" if result.get("accepted") else "rejected",
                    None if result.get("accepted") else "SYSTEM_STOP_REJECTED",
                    {
                        "operator_id": operator_id,
                        "reason": reason,
                        "latency_ms": result.get("latency_ms"),
                        "status_code": result.get("status_code"),
                        "error": result.get("error"),
                    },
                )
            )
        except ServiceError as exc:
            storage_error = exc.details
        return {**result, "storage_error": storage_error}
