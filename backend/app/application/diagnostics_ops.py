"""Read-only diagnostics and safety-report operations.

The methods are moved verbatim as a structural boundary; they still use the
service facade for state, connection, supervisor and store.
"""

from __future__ import annotations

import platform
from typing import Any

from ..domain.enums import ConnectionState, ErrorCode, Mode
from ..domain.models import jsonable
from ..errors import ServiceError, StorageError


KNOWN_DEFECTS = [
    {
        "id": "B-01",
        "summary": "get_torque_state 调用不存在的方法",
        "source": "ros_robot_controller_node.py:243",
        "impact": "扭矩诊断抛 AttributeError",
    },
    {
        "id": "B-02",
        "summary": "按键发布者引用未导入的 PacketReportKeyEvents",
        "source": "ros_robot_controller_node.py:291",
        "impact": "触发按键路径时抛 NameError",
    },
    {
        "id": "B-03",
        "summary": "/joint_controller 回调参数类型不匹配",
        "source": "controller_manager.py:91-95",
        "impact": "该控制入口不可用",
    },
    {
        "id": "B-04",
        "summary": "FollowJointTrajectory action 不发送反馈",
        "source": "joint_trajectory_action_controller.py",
        "impact": "轨迹执行期间没有进度证据",
    },
    {
        "id": "B-05",
        "summary": "轨迹 action 未检查实际位置就报告成功",
        "source": "joint_trajectory_action_controller.py:173",
        "impact": "成功不代表机械臂到达目标",
    },
    {
        "id": "B-06",
        "summary": "joint_states 是最后下发的脉冲，不是传感器反馈",
        "source": "controller_manager.py:97-114",
        "impact": "必须标记为 truth=commanded，而不是 measured",
    },
]


class DiagnosticsOperations:
    def safety_report(
        self,
        *,
        lease_id: str | None = None,
        session_id: str | None = None,
        require_lease: bool = False,
    ) -> dict[str, Any]:
        state = self._state()
        report = self.supervisor.evaluate(
            state=state,
            connection=self._connection(),
            lease_id=lease_id,
            session_id=session_id,
            require_lease=require_lease,
            executing=state.connection_state is ConnectionState.EXECUTING,
            supervised_motion=self.settings.supervised_motion,
            motion_limits=self.motion_limits,
            motion_scope=self.motion_limits.scope,
        )
        return jsonable(report)

    def diagnostics(self) -> dict[str, Any]:
        state = self._state()
        connection = self._connection()
        safety = self.supervisor.evaluate(
            state=state,
            connection=connection,
            require_lease=False,
            executing=state.connection_state is ConnectionState.EXECUTING,
            supervised_motion=self.settings.supervised_motion,
            motion_limits=self.motion_limits,
            motion_scope=self.motion_limits.scope,
        )
        mock_faults = (
            self.adapter.active_faults()
            if self.settings.mode is Mode.MOCK and hasattr(self.adapter, "active_faults")
            else []
        )
        return {
            "mode": self.settings.mode.value,
            "connection": jsonable(connection),
            "mapping": self.mapping.to_public_dict(),
            "safety": jsonable(safety),
            "capabilities": jsonable(state.capabilities),
            "known_defects": KNOWN_DEFECTS,
            "probe_available": self.settings.mode is Mode.MOCK,
            "environment": {
                "robot_id": self.settings.robot_id,
                "backend_version": self.settings.api_version,
                "python": platform.python_version(),
                "mapping_version": self.mapping.mapping_version,
                "mapping_hash": self.mapping.mapping_hash,
                "mapping_verified": self.mapping.verified,
                "limit_version": self.mapping.verification_status,
                "bind_host": self.settings.bind_host,
                "hardware_motion_enabled": self.settings.hardware_motion_enabled,
                "supervised_motion": self.motion_limits.supervised,
                "motion_mode": self.motion_limits.mode_label,
                "motion_scope": self.motion_limits.scope,
                "motion_max_step_rad": self.motion_limits.max_step_rad,
                "motion_max_velocity_rad_s": self.motion_limits.max_velocity_rad_s,
                "motion_min_duration_s": self.motion_limits.min_duration_s,
                "motion_single_joint_only": self.motion_limits.single_joint_only,
                "gripper_control": self.motion_limits.gripper_enabled,
                "takeover_config": (
                    self.takeover.config_hash if self.takeover is not None else None
                ),
            },
            "mock_faults": mock_faults,
            "storage_error": self._storage_error,
        }

    def events(
        self,
        *,
        limit: int = 200,
        stage: str | None = None,
        source: str | None = None,
        command_id: str | None = None,
        after_seq: int = 0,
    ) -> dict[str, Any]:
        try:
            items = self.store.list_events(
                limit=limit,
                stage=stage,
                source=source,
                command_id=command_id,
                after_seq=after_seq,
            )
            last_seq = self.store.last_seq()
        except StorageError as exc:
            self._storage_error = str(exc)
            raise ServiceError(
                ErrorCode.STORAGE_WRITE_FAILED.value,
                "event store read failed",
                status_code=503,
                details={"storage_error": str(exc)},
            ) from exc
        return {"items": items, "last_seq": last_seq, "schema_version": 1}

    def export_csv(self) -> str:
        try:
            return self.store.export_csv()
        except StorageError as exc:
            self._storage_error = str(exc)
            raise ServiceError(
                ErrorCode.STORAGE_WRITE_FAILED.value,
                "event export failed",
                status_code=503,
                details={"storage_error": str(exc)},
            ) from exc
