"""Application service: orchestration, safety gates, audit, and state.

This layer is framework-agnostic. It is called by FastAPI handlers and can be
tested directly with a fake clock.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from dataclasses import replace
from typing import Any, Callable

from .adapters.base import RobotAdapter
from .application.diagnostics_ops import DiagnosticsOperations
from .application.experiment_ops import ExperimentOperations
from .application.motion_ops import MotionOperations
# Re-exported for the existing task tests; the implementation now lives in
# application/task_ops.py.
from .application.task_ops import (
    TaskOperations,
    _locate_rejection_text,
)
from .application.trajectory_ops import TrajectoryOperations
from .application.visual_ops import VisualOperations
from .config import Settings
from .domain.enums import (
    CommandStage,
    ErrorCode,
    FaultCode,
    FaultLevel,
    Mode,
    Truth,
)
from .domain.mapping import MappingConfig
from .domain.motion import effective_motion_limits
from .domain.profile import ProfileParams, plan_move, validate_params as validate_profile_params
from .domain.takeover import (
    ARM_JOINTS,
    TakeoverConfig,
    load_takeover_config,
)
from .domain.models import (
    CommandRecord,
    Event,
    Fault,
    Health,
    MotionTarget,
    RobotState,
    ValidationIssue,
    jsonable,
    utc_now_iso,
)
from .domain.trajectory import (
    TrajectoryDataError,
    alignment_csv,
    analyze,
    build_demo_dataset,
    prepare_dataset,
    summary,
)
from .domain.visual import VisualModelError
from .errors import ServiceError, StorageError
from .persistence.store import EventStore
from .safety.lease import LeaseConflict, LeaseManager
from .safety.pose_gate import PoseGate
from .safety.supervisor import SafetySupervisor
from .safety.tokens import PreviewTokenManager
from .safety.validation import validate_targets

__all__ = ["RobotService", "_locate_rejection_text"]

IDEMPOTENCY_TTL_S = 300

# The gateway is a separate process/package; keep its evidence notes intact but
# show the operator a Chinese explanation. Unknown notes pass through unchanged.
class RobotService(
    TaskOperations,
    ExperimentOperations,
    TrajectoryOperations,
    MotionOperations,
    VisualOperations,
    DiagnosticsOperations,
):
    def __init__(
        self,
        settings: Settings,
        mapping: MappingConfig,
        adapter: RobotAdapter,
        store: EventStore,
        *,
        clock: Callable[[], float] | None = None,
        takeover: TakeoverConfig | None = None,
    ) -> None:
        self.settings = settings
        self.mapping = mapping
        self.adapter = adapter
        self.store = store
        self._clock = clock or time.monotonic
        self.leases = LeaseManager(self._clock)
        self.tokens = PreviewTokenManager(self._clock)
        self.supervisor = SafetySupervisor(mapping, self.leases)
        self.takeover = (
            takeover
            if takeover is not None
            else (
                load_takeover_config(settings.takeover_config_path)
                if settings.motion_scope == "pose"
                else None
            )
        )
        self.motion_limits = effective_motion_limits(
            mapping,
            supervised=settings.supervised_motion,
            takeover=self.takeover,
        )
        self._pose_gate: PoseGate | None = None
        self._last_task_plan: dict[str, Any] | None = None
        self._lock = threading.RLock()
        self._storage_error: str | None = None
        self._idempotency: dict[str, dict[str, Any]] = {}
        self._visual_bundle: tuple[Any, dict[str, Any]] | None = None
        self._visual_error: list[str] | None = None

    # ------------------------------------------------------------------ helpers
    def _storage_fault(self) -> Fault:
        now = utc_now_iso()
        return Fault(
            fault_id="storage-write-failed",
            level=FaultLevel.CRITICAL,
            code=FaultCode.STORAGE_WRITE_FAILED.value,
            message="事件存储写入失败；修复前运动被锁定",
            source="persistence",
            first_seen=now,
            last_seen=now,
            remediation="检查 DB_PATH 权限/磁盘并重启后端",
        )

    def _drain_adapter_events(self) -> None:
        for raw in self.adapter.drain_events():
            event = Event(
                event_id=raw.get("event_id") or f"evt_{uuid.uuid4().hex}",
                command_id=raw.get("command_id"),
                stage=str(raw.get("stage", "Unknown")),
                wall_time=raw.get("wall_time") or utc_now_iso(),
                monotonic_ms=float(raw.get("monotonic_ms", self._clock() * 1000.0)),
                source=str(raw.get("source", "adapter")),
                result=str(raw.get("result", "")),
                error_code=raw.get("error_code"),
                details=raw.get("details") or {},
                schema_version=int(raw.get("schema_version", 1)),
            )
            try:
                self._append(event)
            except ServiceError:
                # _append already latched the storage fault. Never let a
                # completion event crash the state endpoint.
                continue

    def _state(self) -> RobotState:
        state = self.adapter.snapshot()
        self._drain_adapter_events()
        state.control_owner = self.leases.owner()
        if self._storage_error:
            state.faults.append(self._storage_fault())
        return state

    def tick(self) -> None:
        """Advance the adapter and persist adapter-originated events."""
        try:
            self._state()
        except ServiceError:
            return

    def _connection(self):
        return self.adapter.connection_report()

    def _current_targets(self, state: RobotState) -> dict[str, float]:
        """Baseline for step/pose validation.

        For measured hardware feedback the actual position is the only honest
        baseline: two consecutive commands can each be within one step of the
        commanded target while the arm is still far behind. Mock/simulated and
        commanded-only data keep the previous target baseline.
        """
        targets = {
            joint.name: (
                joint.position_rad
                if joint.truth is Truth.MEASURED and joint.position_rad is not None
                else (joint.target_rad if joint.target_rad is not None else 0.0)
            )
            for joint in state.joints
        }
        gripper = state.gripper
        if gripper is not None and gripper.position_rad is not None:
            targets[gripper.joint] = gripper.position_rad
        return targets

    def _fast_gripper(self):
        """The fast task gripper contract, or None when the fast path is off."""
        if self.takeover is None or self.takeover.task is None:
            return None
        fast = self.takeover.task.fast
        if not fast.enabled:
            return None
        return replace(
            self.takeover.gripper,
            speed_raw_per_s=fast.gripper_speed_raw_per_s,
            min_duration_s=fast.gripper_min_duration_s,
            max_duration_s=max(self.takeover.gripper.max_duration_s, 5.0),
        )

    def _validate_targets(
        self,
        state: RobotState,
        targets: list[MotionTarget],
        *,
        profile: ProfileParams | None = None,
        fast: bool = False,
    ):
        min_duration = self.motion_limits.min_duration_s
        max_velocity = self.motion_limits.max_velocity_rad_s
        gripper = self.takeover.gripper if self.takeover is not None else None
        if profile is not None:
            # A dense profile is validated endpoint-by-endpoint against the
            # profile's own peak velocity; its total can be shorter than the
            # single-command duration floor.
            min_duration = 0.0
            max_velocity = profile.vmax_rad_s
        elif fast:
            # Fast task gripper steps use the measured 0.35-0.8 s raw durations.
            min_duration = 0.0
        if fast or profile is not None:
            fast_gripper = self._fast_gripper()
            if fast_gripper is not None:
                gripper = fast_gripper
        outcome = validate_targets(
            self.mapping,
            targets,
            current_targets=self._current_targets(state),
            max_step_rad=self.motion_limits.max_step_rad,
            min_duration_s=min_duration,
            max_duration_s=self.motion_limits.max_duration_s,
            single_joint_only=self.motion_limits.single_joint_only,
            scope="pose" if self.motion_limits.takeover else "step",
            max_velocity_rad_s=max_velocity,
            gripper=gripper,
        )
        if self.motion_limits.takeover and self.takeover is not None:
            gripper_name = self.takeover.gripper.joint
            if any((target.joint or "").strip() == gripper_name for target in targets):
                gripper = state.gripper
                if (
                    gripper is None
                    or not gripper.available
                    or gripper.position_rad is None
                    or gripper.truth
                    not in (
                        Truth.MEASURED,
                        Truth.MEASURED_ON_DEMAND,
                        Truth.COMMANDED,
                        Truth.SIMULATED,
                    )
                ):
                    outcome.errors.append(
                        self._issue(
                            ErrorCode.FEEDBACK_STALE,
                            "gripper measured position is unavailable; a gripper "
                            "command needs measured feedback for its baseline",
                            gripper_name,
                        )
                    )
        return outcome

    def _check_profile(
        self,
        state: RobotState,
        normalized: list[dict[str, Any]],
        profile: ProfileParams,
        outcome,
    ) -> None:
        """Validate the fast profile against the contract and the live start."""
        if self.takeover is None or self.takeover.task is None:
            outcome.errors.append(
                self._issue(
                    "PROFILE_DISABLED",
                    "the fast profile needs a task block in takeover.yaml",
                )
            )
            return
        fast = self.takeover.task.fast
        if not fast.enabled:
            outcome.errors.append(
                self._issue(
                    "PROFILE_DISABLED",
                    "task.fast.enabled is false in configs/takeover.yaml",
                )
            )
            return
        try:
            validate_profile_params(profile)
        except ValueError as exc:
            outcome.errors.append(self._issue(ErrorCode.BAD_REQUEST, str(exc)))
            return
        if profile.vmax_rad_s > fast.vmax_rad_s + 1e-9:
            outcome.errors.append(
                self._issue(
                    "PROFILE_LIMIT",
                    f"profile vmax {profile.vmax_rad_s} rad/s exceeds the "
                    f"contract's {fast.vmax_rad_s} rad/s",
                )
            )
            return
        if profile.amax_rad_s2 > fast.amax_rad_s2 + 1e-9:
            outcome.errors.append(
                self._issue(
                    "PROFILE_LIMIT",
                    f"profile amax {profile.amax_rad_s2} rad/s^2 exceeds the "
                    f"contract's {fast.amax_rad_s2} rad/s^2",
                )
            )
            return
        target = {item["joint"]: float(item["target_rad"]) for item in normalized}
        if set(target) != set(ARM_JOINTS):
            outcome.errors.append(
                self._issue(
                    ErrorCode.POSE_JOINT_SET,
                    "a dense profile needs exactly the five arm joints",
                )
            )
            return
        start = self._current_targets(state)
        try:
            generated = plan_move(
                {name: float(start[name]) for name in ARM_JOINTS},
                target,
                profile,
            )
        except (KeyError, ValueError) as exc:
            outcome.errors.append(
                self._issue(
                    "PROFILE_INVALID",
                    f"the profile could not be built: {exc}",
                )
            )
            return
        if generated.total_s > fast.max_total_duration_s + 1e-9:
            outcome.errors.append(
                self._issue(
                    "PROFILE_LIMIT",
                    f"profile total {generated.total_s:.3f} s exceeds the "
                    f"contract's {fast.max_total_duration_s:.3f} s",
                )
            )
            return
        if generated.segments > fast.max_points:
            outcome.errors.append(
                self._issue(
                    "PROFILE_LIMIT",
                    f"profile has {generated.segments} segments; the cap is "
                    f"{fast.max_points}",
                )
            )
            return
        # Show the real S-curve time in the preview instead of the plan-time
        # estimate (the live start differs by a few pulses after every move).
        for item in normalized:
            item["duration_s"] = generated.total_s

    def _build_profile_points(
        self,
        state: RobotState,
        normalized: list[dict[str, Any]],
        profile: ProfileParams,
    ) -> list[dict[str, Any]]:
        """Build the concrete waypoints from the fresh start pose."""
        start = self._current_targets(state)
        target = {item["joint"]: float(item["target_rad"]) for item in normalized}
        generated = plan_move(
            {name: float(start[name]) for name in ARM_JOINTS},
            target,
            profile,
        )
        by_joint = {item["joint"]: item for item in normalized}
        points: list[dict[str, Any]] = []
        for duration, pose in zip(generated.durations_s, generated.points):
            targets = []
            for name in ARM_JOINTS:
                joint = self.mapping.joints[name]
                targets.append(
                    {
                        "joint": name,
                        "target_rad": float(pose[name]),
                        "target_raw": int(round(joint.rad_to_raw(float(pose[name])))),
                    }
                )
            points.append({"duration_s": float(duration), "targets": targets})
        # The gateway requires the last waypoint to equal the previewed
        # endpoint exactly; use the validated raw pulses there.
        for item in points[-1]["targets"]:
            source = by_joint[item["joint"]]
            item["target_rad"] = float(source["target_rad"])
            item["target_raw"] = int(source["target_raw"])
        return points

    def _issue(self, code: ErrorCode | str, message: str, joint: str | None = None, **details):
        code_value = code.value if isinstance(code, ErrorCode) else str(code)
        return ValidationIssue(code=code_value, message=message, joint=joint, details=details)

    def _pose_gate_for(self) -> PoseGate:
        if self._pose_gate is None:
            model, _config = self._load_visual_bundle()
            if self.takeover is None:  # guarded by callers
                raise VisualModelError(["takeover contract is not loaded"])
            self._pose_gate = PoseGate(self.takeover, model)
        return self._pose_gate

    def _pose_check(
        self,
        state: RobotState,
        normalized: list[dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, list[Any]]:
        """Run the FK gate for a five-joint pose.

        Returns ``(pose_details, errors)``. The gripper is not a pose and the
        gate never runs for it.
        """
        if self.takeover is None or self.motion_limits.scope != "pose":
            return None, []
        target = {item["joint"]: float(item["target_rad"]) for item in normalized}
        if set(target) != set(self.mapping.joints):
            return None, []
        try:
            gate = self._pose_gate_for()
        except VisualModelError as exc:
            return None, [
                self._issue(
                    ErrorCode.FK_UNAVAILABLE,
                    "URDF/FK is unavailable; the pose gate fails closed: "
                    + "; ".join(exc.issues),
                )
            ]
        start = self._current_targets(state)
        try:
            outcome = gate.check(start, target)
        except Exception as exc:  # noqa: BLE001 - fail closed, never crash a preview
            return None, [
                self._issue(
                    ErrorCode.FK_UNAVAILABLE,
                    f"pose gate failed to evaluate the path: {exc}",
                )
            ]
        details = outcome.to_public_dict()
        errors = [
            self._issue(
                violation.code,
                violation.message,
                violation.joint,
                **violation.details,
            )
            for violation in outcome.violations
        ]
        if not outcome.start_compliant:
            details["warning"] = (
                "the measured start is outside the gate envelope; it is exempt "
                "so the arm can be commanded back, but check the arm first"
            )
        return details, errors

    def _event(
        self,
        stage: CommandStage | str,
        command_id: str | None,
        source: str,
        result: str,
        error_code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> Event:
        return Event(
            event_id=f"evt_{uuid.uuid4().hex}",
            command_id=command_id,
            stage=stage.value if isinstance(stage, CommandStage) else str(stage),
            wall_time=utc_now_iso(),
            monotonic_ms=self._clock() * 1000.0,
            source=source,
            result=result,
            error_code=error_code,
            details=details or {},
        )

    def _append(self, event: Event) -> None:
        try:
            self.store.append_event(event)
        except StorageError as exc:
            self._storage_error = str(exc)
            raise ServiceError(
                ErrorCode.STORAGE_WRITE_FAILED.value,
                "event store write failed; motion is locked out",
                status_code=503,
                details={"storage_error": str(exc)},
            ) from exc

    def _save(self, record: CommandRecord) -> None:
        try:
            self.store.upsert_command(record)
        except StorageError as exc:
            self._storage_error = str(exc)
            raise ServiceError(
                ErrorCode.STORAGE_WRITE_FAILED.value,
                "command store write failed; motion is locked out",
                status_code=503,
                details={"storage_error": str(exc)},
            ) from exc

    def _load_record(self, command_id: str) -> CommandRecord | None:
        try:
            raw = self.store.get_command(command_id)
        except StorageError as exc:
            self._storage_error = str(exc)
            raise ServiceError(
                ErrorCode.STORAGE_WRITE_FAILED.value,
                "command store read failed",
                status_code=503,
                details={"storage_error": str(exc)},
            ) from exc
        if raw is None:
            return None
        return CommandRecord(
            command_id=raw["command_id"],
            session_id=raw["session_id"],
            status=CommandStage(raw["status"]),
            targets=raw["targets"],
            created_at=raw["created_at"],
            updated_at=raw["updated_at"],
            error_code=raw["error_code"],
            details=raw["details"],
            stage_history=raw["stage_history"],
        )

    @staticmethod
    def _record_dict(record: CommandRecord) -> dict[str, Any]:
        return jsonable(record)

    def _token_targets(
        self,
        normalized: list[dict[str, Any]],
        *,
        profile: ProfileParams | None = None,
    ) -> list[dict[str, Any]]:
        # ``from_rad`` is deliberately excluded: it comes from the live state
        # read and can change by small amounts between preview and execute
        # (hardware command echo). Binding it would turn a genuinely safe
        # preview into PREVIEW_PAYLOAD_MISMATCH instead of letting the safety
        # gate evaluate the command against the fresh state.
        # A profile duration is also excluded: it is computed from that live
        # start, so it changes by a few milliseconds between preview and
        # execute even when the target is identical.
        return [
            {
                "joint": item["joint"],
                "target_rad": item["target_rad"],
                "target_raw": item["target_raw"],
                **(
                    {}
                    if profile is not None
                    else {"duration_s": item["duration_s"]}
                ),
            }
            for item in normalized
        ]

    def _preview_payload(
        self,
        command_id: str,
        session_id: str,
        normalized: list[dict[str, Any]],
        *,
        profile: ProfileParams | None = None,
        fast: bool = False,
    ) -> dict[str, Any]:
        payload = {
            "command_id": command_id,
            "session_id": session_id,
            "mapping_version": self.mapping.mapping_version,
            "mapping_hash": self.mapping.mapping_hash,
            "targets": self._token_targets(normalized, profile=profile),
        }
        if profile is not None:
            payload["profile"] = profile.to_public_dict()
        if fast:
            payload["fast"] = True
        return payload

    @staticmethod
    def _hash(value: Any) -> str:
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _idempotency_get(self, key: str, request_hash: str) -> dict[str, Any] | None:
        now = self._clock()
        expired = [k for k, v in self._idempotency.items() if v["expires"] <= now]
        for k in expired:
            self._idempotency.pop(k, None)
        cached = self._idempotency.get(key)
        if cached is None:
            return None
        if cached["request_hash"] != request_hash:
            raise ServiceError(
                ErrorCode.IDEMPOTENCY_CONFLICT.value,
                "Idempotency-Key was reused with a different request",
                status_code=409,
            )
        return cached["response"]

    def _idempotency_put(self, key: str, request_hash: str, response: dict[str, Any]) -> None:
        now = self._clock()
        expired = [k for k, v in self._idempotency.items() if v["expires"] <= now]
        for k in expired:
            self._idempotency.pop(k, None)
        self._idempotency[key] = {
            "request_hash": request_hash,
            "expires": now + IDEMPOTENCY_TTL_S,
            "response": response,
        }

    # -------------------------------------------------------------------- reads
    def health(self) -> dict[str, Any]:
        state = self._state()
        return jsonable(
            Health(
                status="ok" if not self._storage_error else "degraded",
                mode=self.settings.mode,
                version=self.settings.api_version,
                schema_version=self.settings.schema_version,
                source_label=state.source_label,
                capabilities=state.capabilities,
            )
        )

    def get_state(self) -> dict[str, Any]:
        return jsonable(self._state())

    def connection_report(self) -> dict[str, Any]:
        return jsonable(self._connection())

    def battery(self) -> dict[str, Any]:
        return jsonable(self.adapter.battery())

    def camera_status(self) -> dict[str, Any]:
        return jsonable(self.adapter.camera_status())

    def camera_snapshot(self) -> tuple[bytes, str]:
        return self._snapshot(self.adapter.camera_snapshot(), "camera")

    def aux_camera_status(self) -> dict[str, Any]:
        return jsonable(self.adapter.aux_camera_status())

    def aux_camera_snapshot(self) -> tuple[bytes, str]:
        return self._snapshot(self.adapter.aux_camera_snapshot(), "aux camera")

    def _snapshot(
        self, snapshot: tuple[bytes, str] | None, label: str
    ) -> tuple[bytes, str]:
        if snapshot is None:
            raise ServiceError(
                ErrorCode.NOT_IMPLEMENTED.value,
                f"{label} snapshot is unavailable",
                status_code=503,
                details={
                    "mode": self.settings.mode.value,
                    "hint": "Configure ARMPI_GATEWAY_URL and run armpi_debug_gateway on the robot",
                },
            )
        return snapshot

    def peripherals(self) -> dict[str, Any]:
        return {
            "battery": self.battery(),
            "camera": self.camera_status(),
            "aux_camera": self.aux_camera_status(),
        }

    # -------------------------------------------------------------- control ops
    def acquire_lease(self, session_id: str, ttl_s: int | None = None) -> dict[str, Any]:
        ttl = ttl_s or self.mapping.safety.lease_ttl_s
        with self._lock:
            try:
                lease = self.leases.acquire(session_id, ttl)
            except LeaseConflict as exc:
                raise ServiceError(
                    ErrorCode.LEASE_CONFLICT.value,
                    "control lease is held by another session",
                    status_code=409,
                    details={"owner": exc.owner},
                ) from exc
            try:
                self._append(
                    self._event(
                        "LeaseAcquired",
                        None,
                        "api",
                        "control lease acquired",
                        None,
                        {"session_id": session_id, "ttl_s": ttl, "lease_id": lease.lease_id},
                    )
                )
            except ServiceError:
                self.leases.release(lease.lease_id, session_id)
                raise
            return {
                "lease_id": lease.lease_id,
                "session_id": lease.session_id,
                "acquired_at": lease.acquired_at,
                "expires_at": lease.expires_at,
                "ttl_s": lease.ttl_s,
            }

    def heartbeat(self, lease_id: str, session_id: str, ttl_s: int | None = None) -> dict[str, Any]:
        with self._lock:
            check = self.leases.heartbeat(lease_id, session_id, ttl_s)
            if not check.ok:
                raise ServiceError(
                    check.code or ErrorCode.NO_LEASE.value,
                    check.detail,
                    status_code=403,
                )
            self._append(
                self._event(
                    "LeaseHeartbeat",
                    None,
                    "api",
                    "control lease renewed",
                    None,
                    {"session_id": session_id, "lease_id": lease_id},
                )
            )
            assert check.lease is not None
            return {
                "lease_id": check.lease.lease_id,
                "session_id": check.lease.session_id,
                "expires_at": check.lease.expires_at,
                "ttl_s": check.lease.ttl_s,
            }

    def release_lease(self, lease_id: str, session_id: str) -> dict[str, Any]:
        with self._lock:
            check = self.leases.release(lease_id, session_id)
            if not check.ok:
                raise ServiceError(
                    check.code or ErrorCode.LEASE_CONFLICT.value,
                    check.detail,
                    status_code=409,
                )
            storage_error = None
            try:
                self._append(
                    self._event(
                        "LeaseReleased",
                        None,
                        "api",
                        "control lease released",
                        None,
                        {"session_id": session_id, "lease_id": lease_id},
                    )
                )
            except ServiceError as exc:
                storage_error = exc.details
            return {"released": True, "storage_error": storage_error}

    def inject_fault(self, kind: str, joint: str | None = None) -> dict[str, Any]:
        if self.settings.mode is not Mode.MOCK:
            raise ServiceError(
                ErrorCode.NOT_IMPLEMENTED.value,
                "fault injection is only available in mock mode",
                status_code=503,
            )
        inject = getattr(self.adapter, "inject", None)
        if inject is None:
            raise ServiceError(ErrorCode.NOT_IMPLEMENTED.value, "adapter has no fault injection", status_code=503)
        try:
            fault = inject(kind, joint=joint)
        except Exception as exc:  # adapter validation
            raise ServiceError(ErrorCode.BAD_REQUEST.value, str(exc), status_code=400) from exc
        storage_error = None
        try:
            self._append(
                self._event(
                    "FaultInjected",
                    None,
                    "mock_adapter",
                    f"mock fault injected: {kind}",
                    None,
                    {"kind": kind, "joint": joint},
                )
            )
        except ServiceError as exc:
            storage_error = exc.details
        return {**jsonable(fault), "storage_error": storage_error}

    def reset_mock(self) -> dict[str, Any]:
        if self.settings.mode is not Mode.MOCK:
            raise ServiceError(
                ErrorCode.NOT_IMPLEMENTED.value,
                "mock reset is only available in mock mode",
                status_code=503,
            )
        self.adapter.reset()
        self._last_task_plan = None
        storage_error = None
        try:
            self._append(
                self._event("MockReset", None, "mock_adapter", "mock state reset")
            )
        except ServiceError as exc:
            storage_error = exc.details
        return {"reset": True, "storage_error": storage_error}

    def probe(self) -> dict[str, Any]:
        if self.settings.mode is not Mode.MOCK:
            raise ServiceError(
                ErrorCode.NOT_IMPLEMENTED.value,
                "硬件反馈探针尚未实现；请用姿态网关执行后的 GetBusServoState 校验",
                status_code=503,
            )
        state = self._state()
        results = []
        for index, joint in enumerate(state.joints):
            available = joint.validity.value in {"valid", "stale"}
            results.append(
                {
                    "joint": joint.name,
                    "servo_id": joint.servo_id,
                    "ok": available,
                    "raw_position": joint.raw_position,
                    "position_rad": joint.position_rad,
                    "duration_ms": 3 + index,
                    "truth": joint.truth.value,
                    "error": None if available else joint.validity.value,
                }
            )
        self._append(
            self._event(
                "Probe",
                None,
                "mock_adapter",
                "simulated single-shot probe",
                None,
                {"results": results},
            )
        )
        return {
            "truth": "simulated",
            "note": "Mock probe only; no servo was read.",
            "results": results,
        }

    # -------------------------------------------------------- trajectories (G2)
    def list_trajectories(self, limit: int = 50) -> dict[str, Any]:
        try:
            items = self.store.list_trajectories(limit)
        except StorageError as exc:
            raise ServiceError(
                ErrorCode.STORAGE_WRITE_FAILED.value,
                "trajectory store read failed",
                status_code=503,
                details={"storage_error": str(exc)},
            ) from exc
        return {"items": items, "schema_version": 1}

    def import_trajectory(
        self, raw: Any, source: str = "import"
    ) -> dict[str, Any]:
        """Validate, analyze, and store an offline dataset.

        Duplicate datasets (same canonical SHA-256) return the existing record.
        This path never calls the adapter and never dispatches motion.
        """
        try:
            dataset, analysis, digest = prepare_dataset(raw)
        except TrajectoryDataError as exc:
            raise ServiceError(
                ErrorCode.BAD_REQUEST.value,
                "trajectory dataset is invalid",
                status_code=400,
                details={"issues": exc.issues},
            ) from exc

        try:
            existing = self.store.find_trajectory_by_hash(digest)
        except StorageError as exc:
            raise ServiceError(
                ErrorCode.STORAGE_WRITE_FAILED.value,
                "trajectory store read failed",
                status_code=503,
                details={"storage_error": str(exc)},
            ) from exc
        if existing is not None:
            return {**existing, "duplicate": True, "storage_error": None}

        record: dict[str, Any] = {
            "trajectory_id": f"trj_{uuid.uuid4().hex[:12]}",
            "created_at": utc_now_iso(),
            "name": dataset["name"],
            "source": source,
            "schema_version": dataset["schema_version"],
            "dataset_sha256": digest,
            "analysis_version": analysis["analysis_version"],
            "dataset": dataset,
            "analysis": analysis,
        }
        summary_fields = summary(record)
        try:
            inserted = self.store.insert_trajectory(
                trajectory_id=record["trajectory_id"],
                created_at=record["created_at"],
                name=record["name"],
                source=record["source"],
                schema_version=record["schema_version"],
                dataset_sha256=digest,
                analysis_version=record["analysis_version"],
                dataset=dataset,
                analysis=analysis,
                summary=summary_fields,
            )
        except StorageError as exc:
            raise ServiceError(
                ErrorCode.STORAGE_WRITE_FAILED.value,
                "trajectory store write failed",
                status_code=503,
                details={"storage_error": str(exc)},
            ) from exc
        if not inserted:
            stored = self.store.find_trajectory_by_hash(digest)
            if stored is not None:
                return {**stored, "duplicate": True, "storage_error": None}
            raise ServiceError(
                ErrorCode.INTERNAL_ERROR.value,
                "trajectory insert was ignored without a matching stored record",
                status_code=500,
            )

        storage_error = None
        try:
            self._append(
                self._event(
                    "TrajectoryImported",
                    None,
                    "trajectory",
                    f"offline dataset stored: {record['name']}",
                    None,
                    {
                        "trajectory_id": record["trajectory_id"],
                        "dataset_sha256": digest,
                        "source": source,
                        "feedback_truth": dataset["feedback"]["truth"],
                        "tracking_available": summary_fields["tracking_available"],
                    },
                )
            )
        except ServiceError as exc:
            storage_error = exc.details
        return {
            **record,
            "duplicate": False,
            "storage_error": storage_error,
        }

    def import_demo_trajectory(self) -> dict[str, Any]:
        return self.import_trajectory(build_demo_dataset(), source="demo")

    def _trajectory_or_404(self, trajectory_id: str) -> dict[str, Any]:
        try:
            record = self.store.get_trajectory(trajectory_id)
        except StorageError as exc:
            raise ServiceError(
                ErrorCode.STORAGE_WRITE_FAILED.value,
                "trajectory store read failed",
                status_code=503,
                details={"storage_error": str(exc)},
            ) from exc
        if record is None:
            raise ServiceError(
                ErrorCode.NOT_FOUND.value,
                f"trajectory {trajectory_id!r} does not exist",
                status_code=404,
            )
        return record

    def get_trajectory(self, trajectory_id: str) -> dict[str, Any]:
        return self._trajectory_or_404(trajectory_id)

    def recompute_trajectory(self, trajectory_id: str) -> dict[str, Any]:
        record = self._trajectory_or_404(trajectory_id)
        fresh = analyze(record["dataset"])
        return {
            "trajectory_id": trajectory_id,
            "analysis_version": fresh["analysis_version"],
            "stored_analysis_version": record["analysis_version"],
            "matches_stored": fresh == record["analysis"],
            "analysis": fresh,
        }

    def export_trajectory_csv(
        self, trajectory_id: str, sample: str = "feedback"
    ) -> tuple[str, str]:
        record = self._trajectory_or_404(trajectory_id)
        try:
            text = alignment_csv(record["dataset"], sample)
        except ValueError as exc:
            raise ServiceError(
                ErrorCode.BAD_REQUEST.value, str(exc), status_code=400
            ) from exc
        return f"armpi-trajectory-{trajectory_id}-{sample}.csv", text

    def export_trajectory_json(self, trajectory_id: str) -> dict[str, Any]:
        return self._trajectory_or_404(trajectory_id)

    def close(self) -> None:
        self.store.close()
