import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { StopResult } from "../api/types";
import { formatAge, formatClock, highestFaultLevel, maxFeedbackAge } from "../format";
import { connectionLabel, modeLabel, severityLabel, stopStateLabel } from "../labels";
import { usePreferences, useRobotStore } from "../store/robotStore";
import { getSessionId } from "../session";
import { Button, SourceBadge, StatusChip } from "./ui";

const NAV_ITEMS = [
  { to: "/", label: "总览", end: true },
  { to: "/joints", label: "关节控制" },
  { to: "/task", label: "任务接管" },
  { to: "/diagnostics", label: "诊断" },
  { to: "/trajectory", label: "轨迹检查" },
  { to: "/experiments", label: "实验台" },
  { to: "/visual", label: "可视化工作台" },
  { to: "/events", label: "事件时间线" },
  { to: "/settings", label: "设置" },
];

function connectionTone(state: string | undefined): "ok" | "warning" | "fault" | "neutral" {
  if (!state) return "neutral";
  if (state === "Ready" || state === "Simulation" || state === "ReadOnly") return "ok";
  if (state === "Executing" || state === "Connecting" || state === "Degraded") return "warning";
  return "fault";
}

export function AppShell() {
  const robot = useRobotStore((store) => store.state);
  const diagnostics = useRobotStore((store) => store.diagnostics);
  const events = useRobotStore((store) => store.events);
  const lease = useRobotStore((store) => store.lease);
  const wsConnected = useRobotStore((store) => store.wsConnected);
  const replayActive = useRobotStore((store) => store.replayActive);
  const error = useRobotStore((store) => store.error);
  const setLease = useRobotStore((store) => store.setLease);
  const preferences = usePreferences();
  const [stopResult, setStopResult] = useState<StopResult | null>(null);
  const [stopBusy, setStopBusy] = useState(false);
  const [stopError, setStopError] = useState<string | null>(null);
  const sessionId = getSessionId();

  useEffect(() => {
    if (!lease) {
      return;
    }
    const timer = window.setInterval(() => {
      void api
        .heartbeat(lease.lease_id, sessionId)
        .catch(() => setLease(null));
    }, 20000);
    return () => window.clearInterval(timer);
  }, [lease, sessionId, setLease]);

  const handleStop = async () => {
    setStopBusy(true);
    setStopError(null);
    try {
      const result = await api.stop({
        sessionId,
        leaseId: lease?.lease_id,
      });
      setStopResult(result);
    } catch (caught) {
      setStopError(
        caught instanceof ApiError ? `${caught.code}: ${caught.message}` : String(caught),
      );
    } finally {
      setStopBusy(false);
    }
  };

  const feedbackAge = robot ? maxFeedbackAge(robot.joints) : null;
  const faultLevel = robot ? highestFaultLevel(robot.faults) : null;
  const lastEvent = events[0] ?? null;
  const mode = robot?.mode ?? diagnostics?.mode ?? "mock";
  const liveSourceLabel = robot?.source_label ?? (mode === "mock" ? "SIMULATION" : "LIVE");
  const sourceLabel = replayActive ? "REPLAY" : liveSourceLabel;
  const ready = diagnostics?.safety.ready ?? false;

  return (
    <div className={`app-shell density-${preferences.density}`}>
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">ARM</span>
          <div>
            <strong>ArmPi 调试工作台</strong>
            <span className="brand-sub">安全调试控制台 · G8 快路径</span>
          </div>
        </div>

        <div className="topbar-status">
          <div className="topbar-field">
            <span className="topbar-label">机器人</span>
            <strong>{robot?.robot_id ?? "—"}</strong>
          </div>
          <div className="topbar-field">
            <span className="topbar-label">来源</span>
            <SourceBadge source={sourceLabel} />
          </div>
          <div className="topbar-field">
            <span className="topbar-label">连接</span>
            <StatusChip
              tone={connectionTone(robot?.connection_state)}
              label={robot?.connection_state ? connectionLabel(robot.connection_state) : "未连接"}
              detail={robot?.connection_state}
            />
          </div>
          <div className="topbar-field">
            <span className="topbar-label">控制权</span>
            <strong className="mono">
              {lease ? lease.session_id.slice(0, 8) : robot?.control_owner ? robot.control_owner.slice(0, 8) : "无"}
            </strong>
          </div>
          <div className="topbar-field">
            <span className="topbar-label">反馈时延</span>
            <StatusChip
              tone={feedbackAge === null ? "neutral" : feedbackAge > 500 ? "fault" : "ok"}
              label={formatAge(feedbackAge)}
            />
          </div>
          <div className="topbar-field">
            <span className="topbar-label">故障</span>
            <StatusChip
              tone={
                faultLevel === "critical"
                  ? "critical"
                  : faultLevel === "fault"
                    ? "fault"
                    : faultLevel === "warning"
                      ? "warning"
                      : "ok"
              }
              label={faultLevel ? `${severityLabel(faultLevel)} (${robot?.faults.length ?? 0})` : "无"}
              detail={faultLevel ?? undefined}
            />
          </div>
          <div className="topbar-field">
            <span className="topbar-label">安全门禁</span>
            <StatusChip
              tone={ready ? "ok" : "warning"}
              label={ready ? "就绪" : "已阻止"}
              detail={diagnostics?.safety.blockers.join(", ")}
            />
          </div>
          <Button
            variant="danger"
            onClick={() => void handleStop()}
            disabled={stopBusy}
            title="软件停止请求，非急停；物理断电是最终防护"
          >
            {stopBusy ? "停止请求中…" : "停止请求"}
          </Button>
        </div>
      </header>

      {!wsConnected && (
        <div className="ws-banner">
          状态 WebSocket 未连接；页面会使用 HTTP 轮询回退。请检查后端是否运行。
        </div>
      )}
      {error && <div className="global-error">{error}</div>}
      {stopError && (
        <div className="error-banner">
          <span>停止请求失败：{stopError}</span>
          <button type="button" className="link-button" onClick={() => setStopError(null)}>
            关闭
          </button>
        </div>
      )}
      {stopResult && (
        <div className="stop-banner">
          <StatusChip
            tone={
              stopResult.state === "StopObserved"
                ? "ok"
                : stopResult.state === "StopUnknown"
                  ? "fault"
                  : "warning"
            }
            label={stopStateLabel(stopResult.state)}
          />
          <span>{stopResult.detail}</span>
          <span className="muted">{stopResult.evidence}</span>
          <button type="button" className="link-button" onClick={() => setStopResult(null)}>
            关闭
          </button>
        </div>
      )}

      <div className="layout">
        <nav className="left-nav" aria-label="主导航">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => `nav-link${isActive ? " nav-link-active" : ""}`}
            >
              {item.label}
            </NavLink>
          ))}
          <div className="nav-note">
            <strong>G1–G8.1 范围</strong>
            <p>
              Mock 完整演示；硬件默认只读，运动需显式接管（G6 单关节 / G7 姿态 / G8 任务）。
              G8 快路径一次运行完成底盘接近 + 7 个臂阶段 + 放回。
            </p>
          </div>
        </nav>

        <main className="page-content">
          <Outlet />
        </main>
      </div>

      <footer className="statusbar">
        <span>模式：{modeLabel(mode)}</span>
        <span>映射：{robot?.mapping_version ?? "—"}</span>
        <span>限位：{robot?.limit_version ?? "—"}</span>
        <span>最近事件：{lastEvent ? formatClock(lastEvent.wall_time, preferences.timeMode) : "—"}</span>
        <span>后端：{String(diagnostics?.environment.backend_version ?? "—")}</span>
        <span>时钟：{preferences.timeMode === "utc" ? "UTC" : "本地"}</span>
      </footer>
    </div>
  );
}
