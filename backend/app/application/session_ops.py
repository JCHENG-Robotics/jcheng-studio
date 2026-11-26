"""M2 operator-session operations: open, authorize, degrade, close, export.

The session store is lazy: existing read-only/mock tests do not create a
session database. A session never holds a control lease or sends motion.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
