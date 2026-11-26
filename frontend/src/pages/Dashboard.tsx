import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type { CameraStatus, Peripherals } from "../api/types";
import {
  Button,
  Card,
  DataAge,
  EmptyState,
  ErrorBanner,
  KeyValue,
  MetricValue,
  SourceBadge,
  StatusChip,
  TruthBadge,
  ValidityBadge,
} from "../components/ui";
import { formatAge, formatAngle, formatClock, formatNumber, maxFeedbackAge } from "../format";
import { batteryLabel, connectionLabel, segmentStateLabel, severityLabel, stageLabel, validityLabel } from "../labels";
import { getSessionId } from "../session";
import { usePreferences, useRobotStore } from "../store/robotStore";

function CameraPanel({
  title,
  subtitle,
  camera,
  snapshotUrl,
  alt,
  overlay,
  loading,
  failed,
  onLoad,
  onError,
}: {
  title: string;
  subtitle: string;
  camera: CameraStatus | null;
  snapshotUrl: string;
  alt: string;
  overlay: string;
  loading: boolean;
  failed: boolean;
  onLoad: () => void;
  onError: () => void;
}) {
  return (
    <Card
      title={title}
      subtitle={subtitle}
      actions={
        <>
          {camera && <TruthBadge truth={camera.truth} />}
          <StatusChip
            tone={camera && camera.available ? (camera.validity === "valid" ? "ok" : "warning") : "neutral"}
            label={camera && camera.available ? validityLabel(camera.validity) : "不可用"}
            detail={camera?.validity}
          />
        </>
      }
    >
      {loading ? (
        <EmptyState title="正在读取相机…">等待 /api/peripherals 返回相机状态。</EmptyState>
      ) : camera && camera.available ? (
        <>
          <div className="camera-frame">
            {failed ? (
              <div className="camera-fallback">快照不可用</div>
            ) : (
              <img src={snapshotUrl} alt={alt} onLoad={onLoad} onError={onError} />
            )}
            <span className="camera-overlay">{overlay}</span>
          </div>
          <div className="camera-meta">
            <span>
              {camera.width ?? "—"}×{camera.height ?? "—"}
            </span>
            <span>{camera.fps !== null ? `${camera.fps.toFixed(1)} fps` : "—"}</span>
            <span>{camera.encoding ?? "—"}</span>
            <span>时延 {formatAge(camera.sample_age_ms)}</span>
            <span>帧数 {camera.frame_count}</span>
          </div>
          {camera.notes.length > 0 && <p className="footnote">{camera.notes.join(" ")}</p>}
        </>
      ) : (
        <EmptyState title="相机不可用">
          {camera?.notes?.length
            ? camera.notes.join(" ")
            : "尚未收到相机帧，或网关未配置。"}
        </EmptyState>
      )}
    </Card>
  );
}

export function Dashboard() {
  const robot = useRobotStore((store) => store.state);
  const diagnostics = useRobotStore((store) => store.diagnostics);
  const events = useRobotStore((store) => store.events);
  const lease = useRobotStore((store) => store.lease);
  const setLease = useRobotStore((store) => store.setLease);
  const preferences = usePreferences();
  const sessionId = getSessionId();
  const [leaseBusy, setLeaseBusy] = useState(false);
  const [leaseError, setLeaseError] = useState<string | null>(null);
  const [peripherals, setPeripherals] = useState<Peripherals | null>(null);
  const [peripheralError, setPeripheralError] = useState<string | null>(null);
  const [cameraTick, setCameraTick] = useState(0);
  const [cameraFailed, setCameraFailed] = useState(false);
  const [auxCameraFailed, setAuxCameraFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await api.peripherals();
        if (!cancelled) {
          setPeripherals(data);
          setPeripheralError(null);
        }
      } catch (error) {
        if (!cancelled) {
          setPeripheralError(
            error instanceof ApiError ? `${error.code}: ${error.message}` : String(error),
          );
        }
      }
    };
    void load();
    const timer = window.setInterval(load, 1000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setCameraTick((tick) => tick + 1);
      setCameraFailed(false);
      setAuxCameraFailed(false);
    }, 300);
    return () => window.clearInterval(timer);
  }, []);

  if (!robot || !diagnostics) {
    return (
      <EmptyState title="正在连接后端…">
        页面会通过 HTTP/WebSocket 读取状态。请确认 backend 已启动，并打开浏览器控制台查看错误。
      </EmptyState>
    );
  }

  const safety = diagnostics.safety;
  const feedbackAge = maxFeedbackAge(robot.joints);
  const jointTruth = robot.joints[0]?.truth;
  const feedbackHint =
    robot.mode === "mock"
      ? "Mock 内模拟"
      : jointTruth === "commanded"
        ? "指令回显，不是编码器反馈"
        : "实测反馈";
  const activeFaults = robot.faults;
  const latestEvents = events.slice(0, 8);
  const battery = peripherals?.battery ?? null;
  const camera = peripherals?.camera ?? null;
  const auxCamera = peripherals?.aux_camera ?? null;
  const batteryTone =
    battery?.status === "ok"
      ? "ok"
      : battery?.status === "low"
        ? "warning"
        : battery?.status === "critical"
          ? "fault"
          : "neutral";
  const batteryPercent = Math.max(0, Math.min(100, battery?.percent_estimate ?? 0));

  const acquireLease = async () => {
    setLeaseBusy(true);
    setLeaseError(null);
    try {
      setLease(await api.acquire(sessionId));
    } catch (error) {
      setLeaseError(error instanceof ApiError ? `${error.code}: ${error.message}` : String(error));
    } finally {
      setLeaseBusy(false);
    }
  };

  const releaseLease = async () => {
    if (!lease) {
      return;
    }
    setLeaseBusy(true);
    setLeaseError(null);
    try {
      await api.release(lease.lease_id, sessionId);
      setLease(null);
    } catch (error) {
      setLeaseError(error instanceof ApiError ? `${error.code}: ${error.message}` : String(error));
    } finally {
      setLeaseBusy(false);
    }
  };

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1 className="page-title">总览</h1>
          <p className="page-subtitle">
            连接链路、安全门禁、关节快照与最近事件。默认只读；运动需要显式控制权与预览确认。
          </p>
        </div>
        <div className="page-actions">
          <SourceBadge source={robot.source_label} />
          <StatusChip
            tone={safety.ready ? "ok" : "warning"}
            label={safety.ready ? "安全门禁就绪" : "安全门禁已阻止"}
            detail={safety.blockers.join(", ")}
          />
        </div>
      </header>

      {robot.source_label === "SIMULATION" && (
        <div className="banner banner-simulation">
          <strong>模拟</strong>
          <span>当前数据来自 Mock 适配器，不是硬件反馈。没有打开串口，也没有启动 ROS 节点。</span>
        </div>
      )}
      {robot.mode === "hardware" && (
        <div className="banner banner-warning">
          <strong>{robot.capabilities.hardware_motion ? "硬件运动已启用" : "硬件模式 · 只读"}</strong>
          <span>
            {robot.capabilities.hardware_motion
              ? "运动经姿态/任务网关执行；每次仍需预览、二次确认与总线校验。无急停，手边保持可断电。"
              : "默认只读；运动需显式设置 ARMPI_HARDWARE_MOTION=1，并经 G6 单关节、G7 姿态或 G8 任务接管确认后才可能下发。"}
          </span>
        </div>
      )}

      <div className="grid grid-4">
        <MetricValue
          label="连接"
          value={connectionLabel(robot.connection_state)}
          tone={robot.connection_state === "Simulation" ? "simulation" : "neutral"}
          hint={robot.mode === "mock" ? "进程内 Mock" : "网关读路径"}
        />
        <MetricValue
          label="安全阻塞项"
          value={safety.blockers.length}
          tone={safety.blockers.length === 0 ? "ok" : "warning"}
          hint={safety.blockers.join(", ") || "无"}
        />
        <MetricValue
          label="关节"
          value={robot.joints.length}
          unit="个"
          hint="夹爪不计入（Q-08）"
        />
        <MetricValue
          label="反馈时延"
          value={feedbackAge === null ? "—" : `${feedbackAge} ms`}
          tone={feedbackAge === null ? "neutral" : feedbackAge > 500 ? "fault" : "ok"}
          hint={feedbackHint}
        />
      </div>

      {peripheralError && (
        <ErrorBanner message={peripheralError} onDismiss={() => setPeripheralError(null)} />
      )}

      <div className="grid grid-camera">
        <CameraPanel
          title="相机 · RGB"
          subtitle={camera?.topic ?? "/depth_cam/rgb/image_raw"}
          camera={camera}
          snapshotUrl={api.cameraSnapshotUrl(cameraTick)}
          alt="ArmPi 相机"
          overlay={robot.source_label === "SIMULATION" ? "模拟" : "实机"}
          loading={!peripherals}
          failed={cameraFailed}
          onLoad={() => setCameraFailed(false)}
          onError={() => setCameraFailed(true)}
        />

        <CameraPanel
          title="相机 · 辅助"
          subtitle={auxCamera?.topic ?? "gateway-aux-camera"}
          camera={auxCamera}
          snapshotUrl={api.auxCameraSnapshotUrl(cameraTick)}
          alt="ArmPi 辅助相机"
          overlay={auxCamera?.truth === "simulated" ? "辅助 · 模拟" : "D435i · UVC"}
          loading={!peripherals}
          failed={auxCameraFailed}
          onLoad={() => setAuxCameraFailed(false)}
          onError={() => setAuxCameraFailed(true)}
        />

        <Card
          className="battery-card"
          title="电池 · 2S"
          subtitle={battery?.topic ?? "gateway-battery"}
          actions={
            <>
              {battery && <TruthBadge truth={battery.truth} />}
              <StatusChip
                tone={batteryTone}
                label={battery?.status ? batteryLabel(battery.status) : "不可用"}
                detail={battery?.status}
              />
            </>
          }
        >
          {!peripherals ? (
            <EmptyState title="正在读取电池…">等待 /api/peripherals 返回电池状态。</EmptyState>
          ) : battery && battery.available && battery.voltage_v !== null ? (
            <>
              <div className="battery-readout">
                <span className="battery-voltage mono">
                  {battery.voltage_v.toFixed(2)}
                  <span className="battery-unit">V</span>
                </span>
                <div className="battery-gauge">
                  <div
                    className={`battery-fill battery-${battery.status}`}
                    style={{ width: `${batteryPercent}%` }}
                  />
                </div>
                <span className="battery-percent mono">
                  {battery.percent_estimate !== null
                    ? `${battery.percent_estimate.toFixed(0)}%`
                    : "—"}
                </span>
              </div>
              <div className="kv-list">
                <KeyValue label="电芯">{battery.cell_count}S</KeyValue>
                <KeyValue label="时延">{formatAge(battery.sample_age_ms)}</KeyValue>
                <KeyValue label="来源">{battery.source}</KeyValue>
                <KeyValue label="有效性">
                  <ValidityBadge validity={battery.validity} />
                </KeyValue>
              </div>
              {battery.status !== "ok" && (
                <p className="battery-warning">
                  {battery.status === "critical"
                    ? "电压严重偏低，停止运动并充电。"
                    : "电压偏低，长时运动前请充电。"}
                </p>
              )}
              {battery.notes.length > 0 && <p className="footnote">{battery.notes.join(" ")}</p>}
            </>
          ) : (
            <EmptyState title="电池数据不可用">
              {battery?.notes?.length
                ? battery.notes.join(" ")
                : "尚未收到电池数据，或网关未配置。"}
            </EmptyState>
          )}
        </Card>
      </div>

      <div className="grid grid-2">
        <Card title="连接链路" subtitle="串口 → 驱动 → 网关 → 反馈">
          <div className="chain">
            {diagnostics.connection.segments.map((segment) => (
              <div className="chain-segment" key={segment.name}>
                <div className="chain-head">
                  <strong>{segment.name}</strong>
                  <StatusChip
                    tone={
                      segment.state === "simulated"
                        ? "simulation"
                        : segment.state === "online"
                          ? "ok"
                          : segment.state === "offline"
                            ? "fault"
                            : segment.state === "unavailable"
                              ? "warning"
                              : "neutral"
                    }
                    label={segmentStateLabel(segment.state)}
                    detail={segment.state}
                  />
                </div>
                <p>{segment.detail}</p>
                <span className="evidence">证据：{segment.evidence}</span>
              </div>
            ))}
          </div>
          {diagnostics.connection.notes.length > 0 && (
            <ul className="note-list">
              {diagnostics.connection.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
        </Card>

        <Card
          title="控制权租约"
          subtitle="唯一控制权；预览不需要租约，执行需要"
          actions={
            lease ? (
              <Button variant="secondary" onClick={() => void releaseLease()} disabled={leaseBusy}>
                释放控制权
              </Button>
            ) : (
              <Button variant="primary" onClick={() => void acquireLease()} disabled={leaseBusy}>
                {leaseBusy ? "申请中…" : "申请控制权"}
              </Button>
            )
          }
        >
          {leaseError && <ErrorBanner message={leaseError} onDismiss={() => setLeaseError(null)} />}
          <div className="lease-panel">
            <KeyValue label="会话">{sessionId.slice(0, 12)}…</KeyValue>
            {lease ? (
              <>
                <KeyValue label="租约">{lease.lease_id.slice(0, 12)}…</KeyValue>
                <KeyValue label="到期">{formatClock(lease.expires_at, preferences.timeMode)}</KeyValue>
                <KeyValue label="TTL">{lease.ttl_s} s</KeyValue>
                <StatusChip tone="ok" label="本浏览器持有控制权" />
              </>
            ) : (
              <EmptyState title="当前没有控制权">
                申请租约后才能执行运动命令；页面默认只读。
              </EmptyState>
            )}
          </div>
        </Card>
      </div>

      <Card
        title="安全门禁"
        subtitle={`${safety.blockers.length} 个阻塞 · ${safety.warnings.length} 个警告`}
        actions={<StatusChip tone={safety.ready ? "ok" : "warning"} label={safety.ready ? "就绪" : "已阻止"} />}
      >
        <div className="check-grid">
          {safety.checks.map((check) => (
            <div
              className={`check-item ${check.passed ? "check-pass" : check.blocking ? "check-block" : "check-warn"}`}
              key={check.check_id}
            >
              <div className="check-head">
                <span className="check-id">{check.check_id}</span>
                <StatusChip
                  tone={check.passed ? "ok" : check.blocking ? "fault" : "warning"}
                  label={check.passed ? "通过" : check.blocking ? "阻止" : "警告"}
                />
              </div>
              <strong>{check.label}</strong>
              <p>{check.detail}</p>
            </div>
          ))}
        </div>
      </Card>

      <Card title="关节快照" subtitle="目标 / 读回 / 来源 / 时延；不把模拟值当作硬件反馈">
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>关节</th>
                <th>舵机</th>
                <th>目标</th>
                <th>读回</th>
                <th>真值</th>
                <th>有效性</th>
                <th>时延</th>
                <th>限位</th>
              </tr>
            </thead>
            <tbody>
              {robot.joints.map((joint) => (
                <tr key={joint.name}>
                  <td className="mono">{joint.name}</td>
                  <td className="mono">{joint.servo_id}</td>
                  <td className="mono">{formatAngle(joint.target_rad, preferences.unit, preferences.decimals)}</td>
                  <td className="mono">{formatAngle(joint.position_rad, preferences.unit, preferences.decimals)}</td>
                  <td><TruthBadge truth={joint.truth} /></td>
                  <td><ValidityBadge validity={joint.validity} /></td>
                  <td><DataAge ageMs={joint.sample_age_ms} maxAgeMs={500} /></td>
                  <td className="mono muted">
                    {formatNumber(joint.limit_min_rad, 2)} … {formatNumber(joint.limit_max_rad, 2)} rad
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="footnote">
          限位已按 2026-11-19 的手动普查在 2026-11-20 重载到运行栈（<code>mapping_verified=true</code>）。
          停机点是随姿态变化的自碰撞限位，不是单一机械极限；不要据此推断未测姿态仍安全。
        </p>
      </Card>

      <div className="grid grid-2">
        <Card title="活动故障" subtitle="只有有证据时才显示故障/严重">
          {activeFaults.length === 0 ? (
            <EmptyState title="无活动故障">
              注意：没有故障不等于硬件已验证；Mock 数据始终是模拟值。
            </EmptyState>
          ) : (
            <div className="fault-list">
              {activeFaults.map((fault) => (
                <div className="fault-item" key={fault.fault_id}>
                  <div className="fault-head">
                    <StatusChip
                      tone={fault.level === "critical" ? "critical" : fault.level === "fault" ? "fault" : "warning"}
                      label={severityLabel(fault.level)}
                      detail={fault.level}
                    />
                    <strong>{fault.code}</strong>
                  </div>
                  <p>{fault.message}</p>
                  <span className="evidence">{fault.remediation}</span>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card title="最新事件" subtitle="最近 8 条命令/状态事件">
          {latestEvents.length === 0 ? (
            <EmptyState title="还没有事件">执行一次预览或故障注入后，这里会出现审计记录。</EmptyState>
          ) : (
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>阶段</th>
                    <th>来源</th>
                    <th>结果</th>
                  </tr>
                </thead>
                <tbody>
                  {latestEvents.map((event) => (
                    <tr key={event.event_id}>
                      <td className="mono">{formatClock(event.wall_time, preferences.timeMode)}</td>
                      <td>
                        <StatusChip
                          tone={event.error_code ? "fault" : "info"}
                          label={stageLabel(event.stage)}
                          detail={event.stage}
                        />
                      </td>
                      <td>{event.source}</td>
                      <td className="ellipsis" title={event.result}>{event.result}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
