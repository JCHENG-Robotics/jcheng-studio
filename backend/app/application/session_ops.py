"""M2 operator-session operations: sessions plus explicit intent forwarding.

The session store is lazy: existing read-only/mock tests do not create a
session database. M2a has no motion; M2b forwards one joint/gripper intent only
when the session is authorized and the operator supplies a system-issued lease.
The Studio never acquires, renews or releases a lease.
"""

from __future__ import annotations

import json
import math
import os
import uuid
from pathlib import Path
from typing import Any

from ..adapters.gateway_v1 import EXPECTED_CONTRACT_HASH
from ..analysis import SessionAnalysisError, analysis_hash, analyze_session
from ..adapters.gateway_v1_intent import (
    GatewayV1IntentClient,
    GatewayV1IntentError,
    gripper_command,
    joint_step_command,
)
from ..domain.visual import load_visual_config
from ..errors import ServiceError
from ..sessions import SessionStore, SessionStoreError


class SessionOperations:
    def _session_store(self) -> SessionStore:
        store = getattr(self, "_sessions", None)
        if store is None:
            calibration: dict[str, Any] = {}
            try:
                config = load_visual_config(self.settings.visual_model_path)
                camera = config.get("camera", {}) if isinstance(config, dict) else {}
                calibration["camera"] = camera.get("calibration_version")
                calibration["camera_frame"] = camera.get("frame")
            except Exception:  # noqa: BLE001 - calibration metadata is advisory
                calibration["camera"] = None
            task = getattr(self.takeover, "task", None) if self.takeover else None
            if task is not None:
                calibration["task"] = Path(task.hand_eye_path).name
            # Measured MotionResult positions follow the profile joint order.
            # This makes an exported session replayable without the gateway.
            calibration["joint_order"] = [
                name for name in self.mapping.joints if name != "r_joint"
            ]
            calibration["gripper_joint"] = (
                "r_joint" if "r_joint" in self.mapping.joints else None
            )
            store = SessionStore(
                self.settings.session_db_path,
                self.settings.session_export_dir,
                studio_version=self.settings.studio_version,
                studio_commit=self.settings.studio_commit,
                robot_id=self.settings.robot_id,
                gateway_url=self.settings.gateway_v1_url,
                calibration=calibration,
            )
            store.close_stale_sessions()
            self._sessions = store
        return store

    @staticmethod
    def _session_failure(exc: SessionStoreError) -> ServiceError:
        return ServiceError(exc.code, exc.message, status_code=exc.status_code)

    def session_open(
        self,
        operator: str,
        purpose: str,
        robot_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            record = self._session_store().open_session(
                operator=operator,
                purpose=purpose,
                robot_id=robot_id,
            )
        except SessionStoreError as exc:
            raise self._session_failure(exc) from exc
        return {"session": record}

    def session_current(self) -> dict[str, Any]:
        try:
            record = self._session_store().current()
        except SessionStoreError as exc:
            raise self._session_failure(exc) from exc
        return {"session": record}

    def session_get(self, session_id: str) -> dict[str, Any]:
        try:
            record = self._session_store().get(session_id)
        except SessionStoreError as exc:
            raise self._session_failure(exc) from exc
        return {"session": record}

    def session_list(self, limit: int = 20) -> dict[str, Any]:
        try:
            records = self._session_store().list_sessions(limit=limit)
        except SessionStoreError as exc:
            raise self._session_failure(exc) from exc
        return {"sessions": records}

    def session_authorize(self, session_id: str) -> dict[str, Any]:
        status = self.gateway_v1_status()
        ready = (
            status.get("configured") is True
            and status.get("reachable") is True
            and status.get("authenticated") is True
            and status.get("contract_match") is True
            and status.get("health_status") == "ok"
            and status.get("state_fresh") is True
            and status.get("stop_latched") is False
        )
        store = self._session_store()
        if not ready:
            try:
                record = store.reject_authorization(
                    session_id,
                    reason="gateway is not ready for an authorized session",
                    errors=status.get("errors", []),
                )
            except SessionStoreError as exc:
                raise self._session_failure(exc) from exc
            raise ServiceError(
                "SESSION_AUTHORIZATION_REJECTED",
                "gateway is not ready; the session is degraded",
                status_code=409,
                details={"status": status, "session": record},
            )
        try:
            record = store.authorize(
                session_id,
                contract_hash=status.get("contract_hash"),
                profile_hash=status.get("profile_hash"),
                gateway_status=status,
            )
        except SessionStoreError as exc:
            raise self._session_failure(exc) from exc
        return {"session": record}

    def session_degrade(self, session_id: str, reason: str) -> dict[str, Any]:
        try:
            record = self._session_store().degrade(session_id, reason=reason)
        except SessionStoreError as exc:
            raise self._session_failure(exc) from exc
        return {"session": record}

    def session_close(self, session_id: str, reason: str) -> dict[str, Any]:
        try:
            record = self._session_store().close_session(session_id, reason=reason)
        except SessionStoreError as exc:
            raise self._session_failure(exc) from exc
        return {"session": record}

    def session_export(self, session_id: str) -> dict[str, Any]:
        try:
            return self._session_store().export(session_id)
        except SessionStoreError as exc:
            raise self._session_failure(exc) from exc

    # ------------------------------------------------------- M3 replay/export
    def session_analysis(self, session_id: str) -> dict[str, Any]:
        try:
            record = self._session_store().get(session_id)
        except SessionStoreError as exc:
            raise self._session_failure(exc) from exc
        try:
            analysis = analyze_session(record)
        except SessionAnalysisError as exc:
            raise ServiceError(exc.code, exc.message, status_code=exc.status_code) from exc
        return {"analysis": analysis}

    def session_analysis_export(self, session_id: str) -> dict[str, Any]:
        analysis = self.session_analysis(session_id)["analysis"]
        digest = analysis_hash(analysis)
        export_dir = Path(self.settings.session_export_dir)
        export_dir.mkdir(parents=True, exist_ok=True)
        path = export_dir / f"{session_id}_analysis.json"
        payload = {
            "schema_version": 1,
            "session_id": session_id,
            "analysis_hash": digest,
            "analysis": analysis,
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp, path)
        return {
            "session_id": session_id,
            "export_path": str(path),
            "analysis_hash": digest,
        }

    # ------------------------------------------------------- M2b intent path
    def _authorized_session(self, session_id: str) -> dict[str, Any]:
        try:
            record = self._session_store().get(session_id)
        except SessionStoreError as exc:
            raise self._session_failure(exc) from exc
        if record["state"] != "authorized":
            raise ServiceError(
                "SESSION_NOT_AUTHORIZED",
                f"session {session_id} is {record['state']}; authorize it first",
                status_code=409,
            )
        if record.get("contract_hash") != EXPECTED_CONTRACT_HASH:
            raise ServiceError(
                "SESSION_CONTRACT_MISMATCH",
                "the authorized session contract does not match the pinned interface hash",
                status_code=409,
            )
        return record

    def _intent_client(self) -> GatewayV1IntentClient:
        url = self.settings.gateway_v1_url
        token = self.settings.gateway_v1_token
        if not url or not token:
            raise ServiceError(
                "GATEWAY_V1_UNAVAILABLE",
                "ARMPI_GATEWAY_V1_URL and ARMPI_GATEWAY_V1_TOKEN are required",
                status_code=503,
            )
        return GatewayV1IntentClient(
            url,
            token=token,
            timeout_s=self.settings.gateway_v1_timeout_s,
        )

    def _record_intent(
        self, session_id: str, kind: str, payload: dict[str, Any]
    ) -> None:
        try:
            self._session_store().record_intent(session_id, kind, payload)
        except SessionStoreError as exc:
            raise self._session_failure(exc) from exc

    @staticmethod
    def _intent_ids() -> tuple[str, str]:
        return (
            f"intent-{uuid.uuid4().hex[:12]}",
            f"idem-{uuid.uuid4().hex[:12]}",
        )

    def _run_intent(
        self, session_id: str, command: dict[str, Any]
    ) -> dict[str, Any]:
        client = self._intent_client()
        self._record_intent(
            session_id, "intent_requested", {"command": command}
        )
        try:
            validation = client.validate_motion(command)
        except GatewayV1IntentError as exc:
            self._record_intent(
                session_id,
                "intent_rejected",
                {"command": command, "error": exc.to_public_dict()},
            )
            raise ServiceError(
                "GATEWAY_V1_ERROR",
                str(exc),
                status_code=503 if exc.status is None else 409,
                details={"error": exc.to_public_dict()},
            ) from exc
        if not validation.get("approved"):
            self._record_intent(
                session_id,
                "intent_rejected",
                {"command": command, "validation": validation},
            )
            raise ServiceError(
                "INTENT_REJECTED",
                str(validation.get("reason_code") or "validation rejected"),
                status_code=409,
                details={"validation": validation},
            )
        try:
            result = client.submit_intent(
                command, idempotency_key=command["idempotency_key"]
            )
        except GatewayV1IntentError as exc:
            self._record_intent(
                session_id,
                "intent_unknown",
                {
                    "command": command,
                    "validation": validation,
                    "error": exc.to_public_dict(),
                },
            )
            try:
                self._session_store().degrade(
                    session_id, reason="motion outcome unknown: gateway error"
                )
            except SessionStoreError:
                pass
            raise ServiceError(
                "INTENT_OUTCOME_UNKNOWN",
                str(exc),
                status_code=503,
                details={
                    "validation": validation,
                    "error": exc.to_public_dict(),
                },
            ) from exc
        event = (
            "intent_unknown"
            if result.get("outcome") == "UNKNOWN"
            else "intent_executed"
        )
        self._record_intent(
            session_id,
            event,
            {"command": command, "validation": validation, "result": result},
        )
        if event == "intent_unknown":
            try:
                self._session_store().degrade(
                    session_id, reason="motion outcome unknown"
                )
            except SessionStoreError:
                pass
        return {
            "session_id": session_id,
            "validation": validation,
            "result": result,
        }

    def session_intent_joint(
        self,
        session_id: str,
        lease_id: str,
        joint: str,
        target_rad: float,
        duration_s: float,
        max_velocity_rad_s: float,
    ) -> dict[str, Any]:
        self._authorized_session(session_id)
        joint = joint.strip()
        if not joint or len(joint) > 64:
            raise ServiceError("INTENT_INVALID", "joint must be 1..64 chars", status_code=400)
        if not lease_id.strip() or len(lease_id) > 128:
            raise ServiceError("INTENT_INVALID", "lease_id must be 1..128 chars", status_code=400)
        if not all(
            math.isfinite(value)
            for value in (target_rad, duration_s, max_velocity_rad_s)
        ):
            raise ServiceError("INTENT_INVALID", "target/duration/velocity must be finite", status_code=400)
        if duration_s <= 0 or max_velocity_rad_s <= 0:
            raise ServiceError("INTENT_INVALID", "duration and velocity must be positive", status_code=400)
        intent_id, idempotency_key = self._intent_ids()
        command = joint_step_command(
            intent_id=intent_id,
            idempotency_key=idempotency_key,
            lease_id=lease_id.strip(),
            source=f"studio:{session_id}",
            joint=joint,
            target_rad=target_rad,
            duration_s=duration_s,
            max_velocity_rad_s=max_velocity_rad_s,
        )
        return self._run_intent(session_id, command)

    def session_intent_gripper(
        self,
        session_id: str,
        lease_id: str,
        position_rad: float,
        duration_s: float,
        max_velocity_rad_s: float,
    ) -> dict[str, Any]:
        self._authorized_session(session_id)
        if not lease_id.strip() or len(lease_id) > 128:
            raise ServiceError("INTENT_INVALID", "lease_id must be 1..128 chars", status_code=400)
        if not all(
            math.isfinite(value)
            for value in (position_rad, duration_s, max_velocity_rad_s)
        ):
            raise ServiceError("INTENT_INVALID", "position/duration/velocity must be finite", status_code=400)
        if duration_s <= 0 or max_velocity_rad_s <= 0:
            raise ServiceError("INTENT_INVALID", "duration and velocity must be positive", status_code=400)
        intent_id, idempotency_key = self._intent_ids()
        command = gripper_command(
            intent_id=intent_id,
            idempotency_key=idempotency_key,
            lease_id=lease_id.strip(),
            source=f"studio:{session_id}",
            position_rad=position_rad,
            duration_s=duration_s,
            max_velocity_rad_s=max_velocity_rad_s,
        )
        return self._run_intent(session_id, command)
