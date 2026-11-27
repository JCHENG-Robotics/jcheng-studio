"""Capability registry: every Studio surface with a live status.

The registry has a fixed shape. A feature never disappears from the console
because a dependency is missing; only its status and detail change. That way an
operator can always see what the console can do and why a feature is not ready.

Statuses are deliberately small and machine-readable:

``available``       usable now
``read_only``       visible and readable, execution disabled by the mode
``degraded``        configured but stale, unreachable or missing an asset
``not_configured``  an optional integration is not set up
``blocked``         the safety gate refuses execution right now
``offline``         the data source has no samples
"""

from __future__ import annotations

from typing import Any

from ..domain.enums import Mode
from ..domain.models import utc_now_iso

AVAILABLE = "available"
READ_ONLY = "read_only"
DEGRADED = "degraded"
NOT_CONFIGURED = "not_configured"
BLOCKED = "blocked"
OFFLINE = "offline"

STATUS_ORDER = (
    AVAILABLE,
    READ_ONLY,
    DEGRADED,
    NOT_CONFIGURED,
    BLOCKED,
    OFFLINE,
)

# The catalog is the source of truth for the capability page. Routes must match
# the SPA router so a row can link straight to the surface it describes.
FEATURE_CATALOG: tuple[dict[str, str], ...] = (
    {
        "id": "overview",
        "title": "总览",
        "route": "/",
        "category": "页面",
        "description": "连接链路、安全门禁、关节/相机/电池快照与最近事件",
    },
    {
        "id": "joints",
        "title": "关节控制",
        "route": "/joints",
        "category": "页面",
        "description": "单关节/姿态预览与执行；受硬件运动能力和安全门禁约束",
    },
    {
        "id": "teleop",
        "title": "遥操作",
        "route": "/teleop",
        "category": "页面",
        "description": "只读遥操作状态与系统软件停止（M5）",
    },
    {
        "id": "task",
        "title": "任务接管",
        "route": "/task",
        "category": "页面",
        "description": "G8 快路径：任务规划、预览与接管执行",
    },
    {
        "id": "diagnostics",
        "title": "诊断",
        "route": "/diagnostics",
        "category": "页面",
        "description": "ROS 图、串口、已知缺陷、/v1 探针与会话回放",
    },
    {
        "id": "trajectory",
        "title": "轨迹检查",
        "route": "/trajectory",
        "category": "页面",
        "description": "离线轨迹数据导入、指标与图表",
    },
    {
        "id": "experiments",
        "title": "实验台",
        "route": "/experiments",
        "category": "页面",
        "description": "离线实验记录、试验与统计",
    },
    {
        "id": "visual",
        "title": "可视化工作台",
        "route": "/visual",
        "category": "页面",
        "description": "URDF/FK 与相机参考的可视化快照",
    },
    {
        "id": "events",
        "title": "事件时间线",
        "route": "/events",
        "category": "页面",
        "description": "命令/状态事件的只读时间线",
    },
    {
        "id": "settings",
        "title": "设置",
        "route": "/settings",
        "category": "页面",
        "description": "只读能力、运动范围与版本信息",
    },
    {
        "id": "motion",
        "title": "运动执行",
        "route": "/joints",
        "category": "集成",
        "description": "运动意图的下发能力；只读模式下永远不可执行",
    },
    {
        "id": "session",
        "title": "操作会话",
        "route": "/diagnostics",
        "category": "集成",
        "description": "M2a 会话记录与授权；需要 /v1 网关就绪",
    },
    {
        "id": "replay",
        "title": "会话回放",
        "route": "/diagnostics",
        "category": "集成",
        "description": "M3 离线 commanded/measured 分析，不接触机器人",
    },
    {
        "id": "gateway_v1",
        "title": "/v1 网关诊断",
        "route": "/diagnostics",
        "category": "集成",
        "description": "只读 health/contract/state/safety",
    },
    {
        "id": "system_stop",
        "title": "系统停止",
        "route": "/teleop",
        "category": "集成",
        "description": "M5 系统软件停止代理（锁存，不是急停）",
    },
    {
        "id": "joints_data",
        "title": "关节反馈",
        "route": "/joints",
        "category": "数据",
        "description": "关节命令回显 + 伺服状态（经只读网关）",
    },
    {
        "id": "battery_data",
        "title": "电池",
        "route": "/",
        "category": "数据",
        "description": "电池电压/状态（经只读网关）",
    },
    {
        "id": "camera_rgb",
        "title": "相机 · RGB",
        "route": "/",
        "category": "数据",
        "description": "/depth_cam/rgb/image_raw",
    },
    {
        "id": "camera_aux",
        "title": "相机 · 辅助",
        "route": "/",
        "category": "数据",
        "description": "/camera/color/image_raw",
    },
)


class CapabilityOperations:
    """Build the capability registry from the live service state."""

    def capabilities(self) -> dict[str, Any]:
        state = self._state()
        mode = self.settings.mode
        caps = state.capabilities
        diagnostics = self.diagnostics()
        safety = diagnostics.get("safety") or {}
        storage_error = diagnostics.get("storage_error")
        motion_enabled = bool(caps.hardware_motion)
        read_only = mode is Mode.HARDWARE and not motion_enabled
        gv1 = self.gateway_v1_status()
        teleop = self.teleop_status()
        visual = self.visual_snapshot()
        statuses: dict[str, tuple[str, str]] = {}

        # Pages -----------------------------------------------------------------
        statuses["overview"] = (
            (AVAILABLE, "只读视图可用")
            if not storage_error
            else (DEGRADED, f"事件存储错误：{storage_error}")
        )
        page_status = (
            (READ_ONLY, "硬件只读：预览可用，执行被硬件运动能力关闭")
            if read_only
            else (AVAILABLE, "可预览；执行仍受安全门禁约束")
        )
        statuses["joints"] = page_status
        statuses["task"] = page_status
        statuses["teleop"] = self._teleop_status(teleop)
        statuses["diagnostics"] = (AVAILABLE, "只读诊断可用")
        statuses["trajectory"] = (AVAILABLE, "离线分析可用")
        statuses["experiments"] = (AVAILABLE, "离线实验可用")
        statuses["visual"] = self._visual_status(visual)
        statuses["events"] = (
            (AVAILABLE, "只读时间线可用")
            if not storage_error
            else (DEGRADED, f"事件存储错误：{storage_error}")
        )
        statuses["settings"] = (AVAILABLE, "只读设置可用")

        # Integrations ----------------------------------------------------------
        statuses["motion"] = self._motion_status(
            mode=mode,
            safety=safety,
            motion_enabled=motion_enabled,
            read_only=read_only,
            demo=self.settings.demo,
        )
        gv1_status = self._gateway_v1_status(gv1)
        statuses["gateway_v1"] = gv1_status
        statuses["session"] = gv1_status
        statuses["replay"] = (AVAILABLE, "离线回放可用")
        statuses["system_stop"] = self._system_stop_status(gv1)

        # Data ------------------------------------------------------------------
        statuses["joints_data"] = self._joints_data_status(state)
        statuses["battery_data"] = self._battery_status()
        statuses["camera_rgb"] = self._camera_status(
            self.camera_status(), "相机 · RGB"
        )
        statuses["camera_aux"] = self._camera_status(
            self.aux_camera_status(), "相机 · 辅助"
        )

        features = []
        for entry in FEATURE_CATALOG:
            status, detail = statuses[entry["id"]]
            features.append({**entry, "status": status, "detail": detail})
        summary = {
            key: sum(1 for feature in features if feature["status"] == key)
            for key in STATUS_ORDER
        }
        return {
            "schema_version": 1,
            "generated_at": utc_now_iso(),
            "mode": mode.value,
            "demo": self.settings.demo,
            "source_label": state.source_label.value,
            "features": features,
            "summary": summary,
        }

    # ------------------------------------------------------------------ helpers
    def _motion_status(
        self,
        *,
        mode: Mode,
        safety: dict[str, Any],
        motion_enabled: bool,
        read_only: bool,
        demo: bool,
    ) -> tuple[str, str]:
        if demo:
            return READ_ONLY, "演示模式：不会下发运动"
        if mode is Mode.MOCK:
            return AVAILABLE, "Mock 完整演示：可预览与执行"
        if read_only:
            return READ_ONLY, "ARMPI_HARDWARE_MOTION=0（硬件只读）"
        if not motion_enabled:
            return READ_ONLY, "运动未启用"
        blockers = [
            check.get("check_id")
            for check in safety.get("checks", [])
            if check.get("blocking") and not check.get("passed")
        ]
        if blockers:
            return BLOCKED, "安全门禁阻止：" + ", ".join(blockers)
        return AVAILABLE, "安全门禁通过"

    def _teleop_status(self, teleop: dict[str, Any]) -> tuple[str, str]:
        if not teleop.get("configured"):
            return NOT_CONFIGURED, "JCHENG_TELEOP_STATUS_URL 未配置"
        if teleop.get("degraded") or teleop.get("stale"):
            reason = self._first_error(teleop) or "样本过期或不可达"
            return DEGRADED, reason
        phase = teleop.get("phase") or "running"
        return AVAILABLE, f"phase={phase}"

    def _gateway_v1_status(self, gv1: dict[str, Any]) -> tuple[str, str]:
        if not gv1.get("configured"):
            return NOT_CONFIGURED, "ARMPI_GATEWAY_V1_URL 未配置"
        if gv1.get("ready"):
            return AVAILABLE, "只读诊断已连接"
        reason = self._first_error(gv1) or "网关未就绪"
        if gv1.get("reachable"):
            return DEGRADED, reason
        return OFFLINE, reason

    def _system_stop_status(self, gv1: dict[str, Any]) -> tuple[str, str]:
        if not self.settings.system_stop_enabled:
            return NOT_CONFIGURED, "ARMPI_SYSTEM_STOP=0"
        if not gv1.get("ready"):
            return DEGRADED, "系统停止已启用，但 /v1 网关未就绪"
        return AVAILABLE, "系统软件停止代理可用（锁存，不是急停）"

    def _visual_status(self, visual: dict[str, Any]) -> tuple[str, str]:
        urdf = visual.get("urdf") or {}
        if urdf.get("available"):
            return AVAILABLE, f"URDF 已加载（{urdf.get('links', 0)} links）"
        return DEGRADED, "URDF 未配置；设置 ARMPI_URDF_PATH"

    @staticmethod
    def _first_error(payload: dict[str, Any]) -> str | None:
        errors = payload.get("errors") or []
        if not errors:
            return None
        first = errors[0]
        if isinstance(first, dict):
            code = first.get("code") or "ERROR"
            message = first.get("message") or ""
            return f"{code}: {message}" if message else str(code)
        return str(first)

    @staticmethod
    def _joints_data_status(state: Any) -> tuple[str, str]:
        joints = list(state.joints)
        valid = [
            joint
            for joint in joints
            if str(getattr(joint.validity, "value", joint.validity)) == "valid"
        ]
        if valid:
            return AVAILABLE, f"{len(valid)}/{len(joints)} 关节有有效样本"
        return OFFLINE, "没有有效关节样本"

    def _battery_status(self) -> tuple[str, str]:
        battery = self.battery()
        if battery.get("available"):
            voltage = battery.get("voltage_v")
            return AVAILABLE, f"{voltage} V"
        notes = battery.get("notes") or []
        return OFFLINE, notes[0] if notes else "尚未收到电池消息"

    @staticmethod
    def _camera_status(status: dict[str, Any], label: str) -> tuple[str, str]:
        if status.get("available"):
            return AVAILABLE, f"{status.get('fps', 0)} fps"
        notes = status.get("notes") or []
        return OFFLINE, notes[0] if notes else f"{label} 没有帧"
