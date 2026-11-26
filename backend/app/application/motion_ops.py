"""G6/G7/G8 motion operations, split out of RobotService.

The methods are moved verbatim as a structural boundary; they still use the
service facade for validation helpers, state, the lock, events and the adapter.
"""

from __future__ import annotations

import uuid
from typing import Any

from ..adapters.base import AdapterError, HardwareMotionDisabled
from ..domain.enums import CommandStage, ConnectionState, ErrorCode, Mode, Truth
from ..domain.models import (
    CommandRecord,
    MotionTarget,
    PreviewResult,
    StopResult,
    jsonable,
    utc_now_iso,
)
from ..domain.profile import ProfileParams
from ..domain.state_machine import transition
from ..domain.takeover import ARM_JOINTS
from ..domain.visual import VisualModelError
from ..errors import ServiceError
from ..safety.pose_gate import PoseGate, mapping_targets_valid


class MotionOperations:
    # ------------------------------------------------------------------- motion
    def preview(
        self,
        session_id: str,
        targets: list[MotionTarget],
        *,
        profile: ProfileParams | None = None,
        fast: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            command_id = f"cmd_{uuid.uuid4().hex[:16]}"
            state = self._state()
            outcome = self._validate_targets(
                state, targets, profile=profile, fast=fast
            )
            if outcome.valid and profile is not None:
                self._check_profile(state, outcome.normalized, profile, outcome)
            pose_details: dict[str, Any] | None = None
            if outcome.valid:
                pose_details, pose_errors = self._pose_check(state, outcome.normalized)
                if pose_errors:
                    outcome.errors.extend(pose_errors)
                elif pose_details and pose_details.get("warning"):
                    outcome.warnings.append(
                        self._issue(
                            "START_OUTSIDE_GATE",
                            pose_details["warning"],
                        )
                    )
                if pose_details and any(
                    joint.truth is Truth.COMMANDED for joint in state.joints
                ):
                    outcome.warnings.append(
                        self._issue(
                            "START_ECHO",
                            "the start pose is the vendor command cache (the "
                            "actual registers can differ by ~8 pulses / 0.03 rad); "
                            "the move is bus-verified after completion",
                        )
                    )
            self._append(
                self._event(
                    CommandStage.REQUESTED,
                    command_id,
                    "api",
                    "preview requested",
                    None,
                    {
                        "kind": "preview",
                        "session_id": session_id,
                        "targets": [jsonable(t) for t in targets],
                    },
                )
            )

            if not outcome.valid:
                record = CommandRecord(
                    command_id=command_id,
                    session_id=session_id,
                    status=CommandStage.FAILED,
                    targets=outcome.normalized,
                    created_at=utc_now_iso(),
                    updated_at=utc_now_iso(),
                    error_code=outcome.errors[0].code,
                    details={
                        "errors": [jsonable(e) for e in outcome.errors],
                        "pose": pose_details,
                    },
                    stage_history=[
                        {"stage": CommandStage.REQUESTED.value, "at": utc_now_iso()},
                        {"stage": CommandStage.FAILED.value, "at": utc_now_iso()},
                    ],
                )
                self._save(record)
                self._append(
                    self._event(
                        CommandStage.FAILED,
                        command_id,
                        "api",
                        "preview rejected",
                        outcome.errors[0].code,
                        {
                            "errors": [jsonable(e) for e in outcome.errors],
                            "warnings": [jsonable(w) for w in outcome.warnings],
                        },
                    )
                )
                return jsonable(
                    PreviewResult(
                        command_id=command_id,
                        session_id=session_id,
                        valid=False,
                        targets=outcome.normalized,
                        errors=outcome.errors,
                        warnings=outcome.warnings,
                        preview_token=None,
                        expires_at=None,
                        mapping_version=self.mapping.mapping_version,
                        evaluated_at=utc_now_iso(),
                        pose=pose_details,
                    )
                )

            payload = self._preview_payload(
                command_id,
                session_id,
                outcome.normalized,
                profile=profile,
                fast=fast,
            )
            token, expires_at = self.tokens.issue(payload, self.mapping.safety.preview_ttl_s)
            record = CommandRecord(
                command_id=command_id,
                session_id=session_id,
                status=CommandStage.VALIDATED,
                targets=outcome.normalized,
                created_at=utc_now_iso(),
                updated_at=utc_now_iso(),
                details={
                    "warnings": [jsonable(w) for w in outcome.warnings],
                    "pose": pose_details,
                },
                stage_history=[
                    {"stage": CommandStage.REQUESTED.value, "at": utc_now_iso()},
                    {"stage": CommandStage.VALIDATED.value, "at": utc_now_iso()},
                ],
            )
            self._save(record)
            self._append(
                self._event(
                    CommandStage.VALIDATED,
                    command_id,
                    "api",
                    "preview validated; token issued",
                    None,
                    {
                        "targets": outcome.normalized,
                        "warnings": [jsonable(w) for w in outcome.warnings],
                        "expires_at": expires_at,
                        "pose": pose_details,
                    },
                )
            )
            return jsonable(
                PreviewResult(
                    command_id=command_id,
                    session_id=session_id,
                    valid=True,
                    targets=outcome.normalized,
                    errors=[],
                    warnings=outcome.warnings,
                    preview_token=token,
                    expires_at=expires_at,
                    mapping_version=self.mapping.mapping_version,
                    evaluated_at=utc_now_iso(),
                    pose=pose_details,
                )
            )

    def execute(
        self,
        *,
        session_id: str,
        lease_id: str,
        preview_token: str,
        command_id: str,
        targets: list[MotionTarget],
        idempotency_key: str,
        profile: ProfileParams | None = None,
        fast: bool = False,
    ) -> dict[str, Any]:
        if not idempotency_key or not idempotency_key.strip():
            raise ServiceError(
                ErrorCode.BAD_REQUEST.value,
                "Idempotency-Key is required",
                status_code=400,
            )
        request_hash = self._hash(
            {
                "session_id": session_id,
                "lease_id": lease_id,
                "preview_token": preview_token,
                "command_id": command_id,
                "targets": [jsonable(t) for t in targets],
                "profile": profile.to_public_dict() if profile else None,
                "fast": bool(fast),
            }
        )
        with self._lock:
            cached = self._idempotency_get(idempotency_key, request_hash)
            if cached is not None:
                return cached

            lease = self.leases.check(lease_id, session_id)
            if not lease.ok:
                raise ServiceError(
                    lease.code or ErrorCode.NO_LEASE.value,
                    lease.detail,
                    status_code=403,
                )

            state = self._state()
            outcome = self._validate_targets(
                state, targets, profile=profile, fast=fast
            )
            if outcome.valid and profile is not None:
                self._check_profile(state, outcome.normalized, profile, outcome)
            pose_details: dict[str, Any] | None = None
            if outcome.valid:
                pose_details, pose_errors = self._pose_check(state, outcome.normalized)
                if pose_errors:
                    outcome.errors.extend(pose_errors)
            if not outcome.valid:
                record = self._load_record(command_id)
                if record is not None and record.status is CommandStage.VALIDATED:
                    transition(
                        record,
                        CommandStage.FAILED,
                        error_code=outcome.errors[0].code,
                        details={
                            "errors": [jsonable(e) for e in outcome.errors],
                            "pose": pose_details,
                        },
                    )
                    self._save(record)
                    self._append(
                        self._event(
                            CommandStage.FAILED,
                            command_id,
                            "api",
                            "execute revalidation rejected",
                            outcome.errors[0].code,
                            {
                                "errors": [jsonable(e) for e in outcome.errors],
                                "pose": pose_details,
                            },
                        )
                    )
                raise ServiceError(
                    outcome.errors[0].code,
                    outcome.errors[0].message,
                    status_code=400,
                    details={
                        "errors": [jsonable(e) for e in outcome.errors],
                        "pose": pose_details,
                    },
                )

            payload = self._preview_payload(
                command_id,
                session_id,
                outcome.normalized,
                profile=profile,
                fast=fast,
            )
            token_check = self.tokens.consume(preview_token, payload)
            if not token_check.ok:
                raise ServiceError(
                    token_check.code or ErrorCode.PREVIEW_INVALID.value,
                    token_check.detail,
                    status_code=403,
                )

            record = self._load_record(command_id)
            if record is None:
                raise ServiceError(
                    ErrorCode.PREVIEW_INVALID.value,
                    "command_id does not match a preview",
                    status_code=400,
                )
            if record.status is not CommandStage.VALIDATED:
                raise ServiceError(
                    ErrorCode.PREVIEW_REUSED.value,
                    f"command is already {record.status.value}",
                    status_code=409,
                )
            if record.session_id != session_id:
                raise ServiceError(
                    ErrorCode.LEASE_CONFLICT.value,
                    "command belongs to another session",
                    status_code=403,
                )

            safety = self.supervisor.evaluate(
                state=state,
                connection=self._connection(),
                lease_id=lease_id,
                session_id=session_id,
                require_lease=True,
                executing=state.connection_state is ConnectionState.EXECUTING,
                supervised_motion=self.settings.supervised_motion,
                motion_limits=self.motion_limits,
                motion_scope=self.motion_limits.scope,
            )
            if not safety.ready:
                self._append(
                    self._event(
                        CommandStage.FAILED,
                        command_id,
                        "safety",
                        "safety gate rejected execution",
                        "GATE_REJECTED",
                        {"blockers": safety.blockers, "checks": jsonable(safety.checks)},
                    )
                )
                raise ServiceError(
                    "GATE_REJECTED",
                    "safety gate rejected execution",
                    status_code=403,
                    details={"blockers": safety.blockers},
                )

            try:
                if profile is not None:
                    points = self._build_profile_points(
                        state, outcome.normalized, profile
                    )
                    dispatch = self.adapter.dispatch_profile(
                        outcome.normalized, points, command_id
                    )
                else:
                    dispatch = self.adapter.dispatch(
                        outcome.normalized, command_id, fast=fast
                    )
            except HardwareMotionDisabled as exc:
                transition(
                    record,
                    CommandStage.FAILED,
                    error_code=ErrorCode.HARDWARE_MOTION_DISABLED.value,
                )
                self._save(record)
                self._append(
                    self._event(
                        CommandStage.FAILED,
                        command_id,
                        "adapter",
                        "hardware motion disabled",
                        ErrorCode.HARDWARE_MOTION_DISABLED.value,
                    )
                )
                raise ServiceError(
                    ErrorCode.HARDWARE_MOTION_DISABLED.value,
                    str(exc),
                    status_code=503,
                ) from exc

            if dispatch.status is CommandStage.FAILED:
                transition(
                    record,
                    CommandStage.FAILED,
                    error_code=dispatch.error_code,
                    details={"adapter_detail": dispatch.detail},
                )
                self._save(record)
                self._append(
                    self._event(
                        CommandStage.FAILED,
                        command_id,
                        "adapter",
                        dispatch.detail,
                        dispatch.error_code,
                        dispatch.details,
                    )
                )
                raise ServiceError(
                    dispatch.error_code or ErrorCode.INTERNAL_ERROR.value,
                    dispatch.detail,
                    status_code=503,
                )

            transition(
                record,
                CommandStage.DISPATCHED,
                details={
                    "safety": {"ready": safety.ready, "warnings": safety.warnings},
                    "adapter": dispatch.details,
                    "pose": pose_details,
                },
            )
            self._save(record)
            self._append(
                self._event(
                    CommandStage.DISPATCHED,
                    command_id,
                    "adapter",
                    dispatch.detail,
                    None,
                    {**dict(dispatch.details or {}), "pose": pose_details},
                )
            )

            if dispatch.status is CommandStage.UNKNOWN:
                transition(
                    record,
                    CommandStage.UNKNOWN,
                    error_code=dispatch.error_code,
                    details={"reason": dispatch.detail},
                )
                self._save(record)
                self._append(
                    self._event(
                        CommandStage.UNKNOWN,
                        command_id,
                        "adapter",
                        "command outcome is unknown; no retry",
                        dispatch.error_code,
                        dispatch.details,
                    )
                )

            response = self._record_dict(record)
            self._idempotency_put(idempotency_key, request_hash, response)
            return response

    def stop(self, *, session_id: str, lease_id: str | None = None, command_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            stop_command_id = command_id or f"stop_{uuid.uuid4().hex[:12]}"
            lease_detail = None
            if lease_id:
                lease_check = self.leases.check(lease_id, session_id)
                lease_detail = {"ok": lease_check.ok, "detail": lease_check.detail}
            result: StopResult = self.adapter.stop(stop_command_id)
            # A stop invalidates any plan built from the pre-stop pose; a
            # later task/verify must re-locate instead of accepting stale data.
            self._last_task_plan = None
            storage_error = None
            try:
                self._append(
                    self._event(
                        result.state.value,
                        stop_command_id,
                        "api",
                        result.detail,
                        None,
                        {
                            "session_id": session_id,
                            "lease": lease_detail,
                            "evidence": result.evidence,
                            "stop_state": result.state.value,
                        },
                    )
                )
            except ServiceError as exc:
                storage_error = exc.details
            return {
                **jsonable(result),
                "storage_error": storage_error,
            }

    # ------------------------------------------------------------ chassis
    def chassis_pulse(
        self,
        *,
        session_id: str,
        lease_id: str,
        vx: float,
        vy: float,
        duty: float,
        duration_s: float,
    ) -> dict[str, Any]:
        """Pulse the open-loop chassis toward the cube, then re-measure.

        Only allowed with the arm at the task view: the pulse is computed from
        that view's measurement, and the next locate must see the same scene.
        The gateway caps each pulse; the caller is responsible for the loop.
        """
        takeover = self.takeover
        task = takeover.task if takeover is not None else None
        approach = task.approach if task is not None else None
        if approach is None or not approach.enabled:
            raise ServiceError(
                ErrorCode.NOT_IMPLEMENTED.value,
                "the chassis approach is disabled in the takeover contract",
                status_code=503,
            )
        if not (0.0 < float(duty) <= approach.duty):
            raise ServiceError(
                ErrorCode.BAD_REQUEST.value,
                f"duty must be in (0, {approach.duty}]",
                status_code=400,
            )
        if not (
            approach.min_duration_s
            <= float(duration_s)
            <= approach.max_duration_s
        ):
            raise ServiceError(
                ErrorCode.BAD_REQUEST.value,
                f"duration must be in "
                f"[{approach.min_duration_s}, {approach.max_duration_s}] s",
                status_code=400,
            )
        if self.settings.mode is Mode.MOCK:
            return {
                "status": "Completed",
                "verification": "simulated",
                "session_id": session_id,
                "pulse": {
                    "vx": float(vx),
                    "vy": float(vy),
                    "duty": float(duty),
                    "duration_s": float(duration_s),
                },
            }
        if (
            self.settings.mode is not Mode.HARDWARE
            or not self.settings.hardware_motion_enabled
        ):
            raise ServiceError(
                ErrorCode.HARDWARE_MOTION_DISABLED.value,
                "chassis motion requires hardware mode and ARMPI_HARDWARE_MOTION=1",
                status_code=403,
            )
        with self._lock:
            lease = self.leases.check(lease_id, session_id)
            if not lease.ok:
                raise ServiceError(
                    lease.code or ErrorCode.NO_LEASE.value,
                    lease.detail,
                    status_code=403,
                )
            state = self._state()
            view = takeover.preset(task.view_preset) if task is not None else None
            if view is None:
                raise ServiceError(
                    ErrorCode.NOT_IMPLEMENTED.value,
                    f"task view preset {task.view_preset!r} is missing",
                    status_code=503,
                )
            current = {
                joint.name: joint.position_rad
                for joint in state.joints
                if joint.position_rad is not None
            }
            if set(current) != set(ARM_JOINTS):
                raise ServiceError(
                    "TASK_STATE_UNAVAILABLE",
                    "not all five arm joints have a position",
                    status_code=409,
                )
            deviation = max(
                abs(float(current[name]) - float(view.joints[name]))
                for name in ARM_JOINTS
            )
            if deviation > 0.12:
                raise ServiceError(
                    "TASK_NOT_AT_VIEW",
                    "the arm is not at the task view pose; prepare the view first",
                    status_code=409,
                )
            payload = {
                "vx": float(vx),
                "vy": float(vy),
                "duty": float(duty),
                "duration_s": float(duration_s),
            }
            try:
                response = self.adapter.chassis_pulse(payload)
            except AdapterError as exc:
                raise ServiceError(
                    exc.code,
                    str(exc),
                    status_code=502,
                    details=exc.details,
                ) from exc
            return {
                "status": "Completed",
                "verification": (response or {}).get(
                    "verification", "open_loop"
                ),
                "session_id": session_id,
                "pulse": payload,
                "gateway": response,
            }

    # ------------------------------------------------------------ pose preset
    def motion_presets(self) -> dict[str, Any]:
        """Return the takeover contract and the hardware-validated poses.

        Every preset is checked statically against the joint limits and the FK
        gate. A path to a preset is still re-checked at preview/execute time
        from the measured start, because the straight interpolation can differ.
        """
        state = self._state()
        base: dict[str, Any] = {
            "schema_version": 1,
            "generated_at": utc_now_iso(),
            "scope": self.motion_limits.scope,
            "available": self.takeover is not None,
            "contract": (
                self.takeover.to_public_dict() if self.takeover is not None else None
            ),
            "presets": [],
            "current": {
                "joints": {
                    joint.name: joint.position_rad
                    for joint in state.joints
                    if joint.position_rad is not None
                },
                "gripper": jsonable(state.gripper) if state.gripper is not None else None,
                "tcp_m": None,
                "measured": all(
                    joint.truth in (Truth.MEASURED, Truth.MEASURED_ON_DEMAND)
                    for joint in state.joints
                ),
                "source": (
                    "measured_on_demand"
                    if any(
                        joint.truth is Truth.MEASURED_ON_DEMAND
                        for joint in state.joints
                    )
                    else (
                        "commanded"
                        if any(joint.truth is Truth.COMMANDED for joint in state.joints)
                        else "mixed"
                    )
                ),
            },
            "gate_error": None,
        }
        if self.takeover is None:
            return base

        gate: PoseGate | None
        try:
            gate = self._pose_gate_for()
        except VisualModelError as exc:
            gate = None
            base["gate_error"] = exc.issues

        current_joints = base["current"]["joints"]
        if gate is not None and set(current_joints) == set(self.mapping.joints):
            tcp = gate.tcp(current_joints)
            base["current"]["tcp_m"] = (
                [round(value, 6) for value in tcp] if tcp is not None else None
            )

        presets: list[dict[str, Any]] = []
        for preset in self.takeover.presets:
            bad = mapping_targets_valid(self.mapping, preset.joints)
            raw = {}
            for name, value in preset.joints.items():
                joint = self.mapping.get_joint(name)
                raw[name] = int(round(joint.rad_to_raw(value))) if joint else None
            entry = {
                **preset.to_public_dict(),
                "target_raw": raw,
                "limit_ok": not bad,
                "bad_limits": bad,
                "pose_check": (
                    gate.check_pose(preset.joints).to_public_dict()
                    if gate is not None
                    else None
                ),
            }
            presets.append(entry)
        base["presets"] = presets
        return base
