"""Safety gate orchestration.

The supervisor is deliberately independent of FastAPI and of the adapter. It
takes a state snapshot and returns a structured report with blocking checks.
"""

from __future__ import annotations

from ..domain.enums import (
    CommandStage,
    ConnectionState,
    ErrorCode,
    FaultLevel,
    Severity,
    SourceLabel,
    StopState,
    Truth,
    Validity,
)
from ..domain.mapping import MappingConfig
from ..domain.motion import EffectiveMotionLimits
from ..domain.models import (
    ConnectionReport,
    RobotState,
    SafetyCheck,
    SafetyReport,
    utc_now_iso,
)
from .lease import LeaseManager


class SafetySupervisor:
    def __init__(self, mapping: MappingConfig, leases: LeaseManager) -> None:
        self.mapping = mapping
        self.leases = leases

    def evaluate(
        self,
        *,
        state: RobotState,
        connection: ConnectionReport,
        lease_id: str | None = None,
        session_id: str | None = None,
        require_lease: bool = True,
        executing: bool = False,
        supervised_motion: bool = False,
        motion_limits: EffectiveMotionLimits | None = None,
        motion_scope: str = "step",
    ) -> SafetyReport:
        checks: list[SafetyCheck] = []

        def add(
            check_id: str,
            label: str,
            passed: bool,
            detail: str,
            *,
            blocking: bool = True,
            severity: Severity = Severity.FAULT,
        ) -> None:
            checks.append(
                SafetyCheck(
                    check_id=check_id,
                    label=label,
                    passed=passed,
                    blocking=blocking,
                    detail=detail,
                    severity=severity,
                )
            )

        add(
            "mode_supported",
            "运行模式",
            True,
            f"mode={state.mode.value}",
            severity=Severity.INFO,
        )
        add(
            "gateway_online",
            "网关在线",
            connection.gateway_online,
            "Mock 适配器（无 ROS 进程）" if state.mode.value == "mock" else "网关探针",
        )
        add(
            "driver_online",
            "Driver 在线",
            connection.driver_online,
            "Mock 驱动（无厂商节点）" if state.mode.value == "mock" else "驱动探针",
        )

        if state.mode.value == "mock":
            feedback_ok = True
            feedback_detail = "模拟数据；不是硬件反馈"
        else:
            ages = [j.sample_age_ms for j in state.joints if j.sample_age_ms is not None]
            feedback_ok = bool(ages) and max(ages) <= self.mapping.safety.feedback_max_age_ms
            feedback_detail = (
                f"最大样本时延 {max(ages)} ms（阈值 {self.mapping.safety.feedback_max_age_ms} ms）"
                if ages
                else "没有反馈样本"
            )
        add(
            "feedback_fresh",
            "反馈新鲜度",
            feedback_ok,
            feedback_detail,
            blocking=state.mode.value != "mock",
            severity=Severity.WARNING if state.mode.value == "mock" else Severity.FAULT,
        )

        add(
            "mapping_loaded",
            "关节映射",
            bool(self.mapping.mapping_version and self.mapping.joints),
            f"version={self.mapping.mapping_version}",
            severity=Severity.INFO,
        )

        mapping_verified = self.mapping.verified or supervised_motion
        if self.mapping.verified:
            mapping_detail = "限位已验证"
        elif supervised_motion:
            step = motion_limits.max_step_rad if motion_limits else "?"
            mapping_detail = (
                "受监督调试下接受暂定限位"
                f"（单关节，最大步长 {step} rad）"
            )
        else:
            mapping_detail = (
                "Mock 接受暂定限位；硬件运动被阻止"
            )
        add(
            "mapping_verified",
            "限位验证",
            mapping_verified,
            mapping_detail,
            blocking=state.mode.value != "mock" and not supervised_motion,
            severity=(
                Severity.WARNING
                if supervised_motion or state.mode.value == "mock"
                else Severity.FAULT
            ),
        )

        if supervised_motion:
            step = motion_limits.max_step_rad if motion_limits else None
            floor = motion_limits.min_duration_s if motion_limits else None
            add(
                "supervised_commissioning",
                "受监督调试模式",
                True,
                (
                    "操作员确认已提供；"
                    f"单关节，最大步长 {step} rad，时长 >= {floor} s"
                ),
                blocking=False,
                severity=Severity.WARNING,
            )

        if motion_scope == "pose" and motion_limits is not None:
            add(
                "pose_takeover",
                "姿态接管",
                True,
                (
                    "整组五关节姿态或单条夹爪命令；"
                    f"速度 <= {motion_limits.max_velocity_rad_s} rad/s；"
                    "FK 工作区 + 桌面 + 低位偏航门禁生效；"
                    "无自碰撞模型"
                ),
                blocking=False,
                severity=Severity.WARNING,
            )

        if require_lease:
            lease = self.leases.check(lease_id, session_id)
            add(
                "control_lease",
                "控制权租约",
                lease.ok,
                lease.detail,
                severity=Severity.FAULT,
            )
        else:
            add(
                "control_lease",
                "控制权租约",
                True,
                "预览不需要租约",
                blocking=False,
                severity=Severity.INFO,
            )

        active = [f for f in state.faults if f.level in (FaultLevel.FAULT, FaultLevel.CRITICAL)]
        add(
            "no_active_fault",
            "活动故障",
            not active,
            "无活动故障" if not active else "; ".join(f.code for f in active),
        )

        add(
            "not_executing",
            "执行互锁",
            not executing,
            "空闲" if not executing else "已有运动在执行",
        )

        hardware_motion = state.capabilities.hardware_motion
        if state.mode.value == "mock":
            motion_detail = "仅 Mock"
        elif hardware_motion:
            motion_detail = f"网关运动已启用；mode={state.capabilities.motion_mode}"
        else:
            motion_detail = "hardware_motion=false（运动未启用）"
        add(
            "hardware_motion_enabled",
            "硬件运动能力",
            state.mode.value == "mock" or hardware_motion,
            motion_detail,
        )

        if state.mode.value != "mock" and hardware_motion:
            bad = [
                j.name
                for j in state.joints
                if j.truth
                not in (Truth.MEASURED, Truth.MEASURED_ON_DEMAND, Truth.COMMANDED)
                or j.validity in (Validity.INVALID, Validity.UNAVAILABLE)
            ]
            commanded = [
                j.name
                for j in state.joints
                if j.truth is Truth.COMMANDED
                and j.validity not in (Validity.INVALID, Validity.UNAVAILABLE)
            ]
            if bad:
                detail = "no position: " + ", ".join(bad)
            elif commanded:
                detail = (
                    "position is the vendor command cache for "
                    + ", ".join(commanded)
                    + "; each move is verified by a GetBusServoState read"
                )
            else:
                detail = "actual bus-servo registers (measured_on_demand)"
            add(
                "measured_feedback",
                "位置来源",
                not bad,
                detail,
                severity=(
                    Severity.WARNING
                    if commanded or state.mode.value == "mock"
                    else Severity.FAULT
                ),
            )

        if (
            state.mode.value != "mock"
            and hardware_motion
            and motion_limits is not None
            and motion_limits.gripper_enabled
        ):
            gripper = state.gripper
            age = gripper.sample_age_ms if gripper is not None else None
            fresh = bool(
                gripper is not None
                and gripper.available
                and gripper.truth
                in (Truth.MEASURED, Truth.MEASURED_ON_DEMAND, Truth.COMMANDED)
                and gripper.validity not in (Validity.INVALID, Validity.UNAVAILABLE)
                and (age is None or age <= self.mapping.safety.feedback_max_age_ms)
            )
            add(
                "gripper_feedback",
                "夹爪反馈",
                fresh,
                (
                    f"measured r_joint, sample age {age} ms"
                    if fresh
                    else "gripper measured position is unavailable or stale; "
                    "gripper commands are rejected"
                ),
                blocking=False,
                severity=Severity.WARNING,
            )

        unavailable = [
            j.name
            for j in state.joints
            if j.validity in (Validity.INVALID, Validity.UNAVAILABLE)
        ]
        add(
            "joints_available",
            "关节数据可用",
            not unavailable,
            "all joints available" if not unavailable else f"unavailable: {', '.join(unavailable)}",
        )

        stop_clear = state.stop_state not in (
            StopState.STOP_REQUESTED,
            StopState.STOP_DISPATCHED,
            StopState.STOP_UNKNOWN,
        )
        add(
            "stop_state_clear",
            "停止状态",
            stop_clear,
            "no stop in progress" if stop_clear else f"stop_state={state.stop_state.value if state.stop_state else None}",
        )

        blockers = [c.check_id for c in checks if not c.passed and c.blocking]
        warnings = [c.check_id for c in checks if not c.passed and not c.blocking]
        return SafetyReport(
            ready=not blockers,
            checks=checks,
            blockers=blockers,
            warnings=warnings,
            mode=state.mode,
            generated_at=utc_now_iso(),
        )
