import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type { CommandRecord, PreviewResult, StopResult } from "../api/types";
import {
  Button,
  Card,
  DataAge,
  EmptyState,
  ErrorBanner,
  KeyValue,
  Modal,
  StatusChip,
  TruthBadge,
  ValidityBadge,
} from "../components/ui";
import { formatAngle, formatClock, formatNumber } from "../format";
import { connectionLabel, stageLabel, stopStateLabel } from "../labels";
import { getSessionId } from "../session";
import { usePreferences, useRobotStore } from "../store/robotStore";
import { PoseTakeover } from "./PoseTakeover";

function errorText(error: unknown): string {
  return error instanceof ApiError ? `${error.code}: ${error.message}` : String(error);
}

export function JointControl() {
  const robot = useRobotStore((store) => store.state);
  const diagnostics = useRobotStore((store) => store.diagnostics);
  const lease = useRobotStore((store) => store.lease);
  const events = useRobotStore((store) => store.events);
  const setLease = useRobotStore((store) => store.setLease);
  const preferences = usePreferences();
  const sessionId = getSessionId();

  const [selected, setSelected] = useState("joint1");
  const [targetRad, setTargetRad] = useState("0.000");
  const [duration, setDuration] = useState("1.0");
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [command, setCommand] = useState<CommandRecord | null>(null);
  const [stopResult, setStopResult] = useState<StopResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const current = robot?.joints.find((joint) => joint.name === selected);
    setTargetRad((current?.target_rad ?? 0).toFixed(3));
    setPreview(null);
    setCommand(null);
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  if (robot?.capabilities.motion_scope === "pose") {
    return <PoseTakeover />;
  }

  if (!robot || !diagnostics) {
    return <EmptyState title="正在读取关节状态…">需要后端 /api/state 与 /api/diagnostics。</EmptyState>;
  }

  const joint = robot.joints.find((item) => item.name === selected) ?? robot.joints[0] ?? null;
  if (!joint) {
    return <EmptyState title="没有可用关节">检查 joint mapping 配置。</EmptyState>;
  }

  const hardwareBlocked = robot.mode === "hardware" && !robot.capabilities.hardware_motion;
  const safetyReady = diagnostics.safety.ready;
  const previewExpired = preview?.expires_at ? new Date(preview.expires_at).getTime() <= now : false;
  const canExecute = Boolean(
    preview?.valid &&
      preview.preview_token &&
      lease &&
      !hardwareBlocked &&
      safetyReady &&
      !previewExpired,
  );

  const supervised = robot.capabilities.supervised_motion;
  const disabledReason = (() => {
    if (hardwareBlocked) return "硬件运动未启用（后端或网关已关闭运动）";
    if (!preview) return "先完成预览";
    if (!preview.valid) return "预览未通过，检查错误列表";
    if (!preview.preview_token) return "预览未签发令牌";
    if (!lease) return "需要控制权租约";
    if (previewExpired) return "预览已过期，请重新预览";
    if (!safetyReady) return `安全门禁阻止：${diagnostics.safety.blockers.join(", ")}`;
    return "";
  })();

  const latestStage = command
    ? events.find((event) => event.command_id === command.command_id)?.stage ?? command.status
    : null;

  const commandEvents = command
    ? events
        .filter((event) => event.command_id === command.command_id)
        .slice()
        .sort((a, b) => a.seq - b.seq)
    : [];
  const displayStages = commandEvents.length
    ? commandEvents.map((event) => ({
        stage: event.stage,
        at: event.wall_time,
        error_code: event.error_code,
      }))
    : command?.stage_history ?? [];

  const handlePreview = async () => {
    const numericTarget = Number(targetRad);
    const numericDuration = Number(duration);
    if (!Number.isFinite(numericTarget) || !Number.isFinite(numericDuration)) {
      setError("目标角度与时长必须是有限数值。");
      return;
    }
    setBusy(true);
    setError(null);
    setCommand(null);
    try {
      const result = await api.preview(sessionId, [
        { joint: joint.name, target_rad: numericTarget, duration_s: numericDuration },
      ]);
      setPreview(result);
    } catch (caught) {
      setPreview(null);
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  };

  const handleAcquire = async () => {
    setBusy(true);
    setError(null);
    try {
      setLease(await api.acquire(sessionId));
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  };

  const confirmExecute = async () => {
    if (!preview?.preview_token || !lease) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await api.execute({
        sessionId,
        leaseId: lease.lease_id,
        previewToken: preview.preview_token,
        commandId: preview.command_id,
        targets: preview.targets.map((target) => ({
          joint: target.joint,
          target_rad: target.target_rad,
          duration_s: target.duration_s,
        })),
        idempotencyKey: crypto.randomUUID(),
      });
      setCommand(result);
      setPreview(null);
      setConfirmOpen(false);
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  };

  const handleStop = async () => {
    setBusy(true);
    setError(null);
    try {
      setStopResult(await api.stop({ sessionId, leaseId: lease?.lease_id }));
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  };

  const sliderValue = Number.isFinite(Number(targetRad)) ? Number(targetRad) : 0;

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1 className="page-title">关节控制</h1>
          <p className="page-subtitle">
            单关节目标。严格流程：输入 → 预览 → 二次确认 → 执行。滑块只更新本地输入，不发送命令。
          </p>
        </div>
        <div className="page-actions">
          <StatusChip
            tone={robot.source_label === "SIMULATION" ? "simulation" : "ok"}
            label={robot.source_label === "SIMULATION" ? "模拟" : "实机"}
            detail={robot.source_label}
          />
          <StatusChip tone={lease ? "ok" : "warning"} label={lease ? "持有控制权" : "无控制权"} />
        </div>
      </header>

      {hardwareBlocked && (
        <div className="banner banner-warning">
          <strong>硬件运动未启用</strong>
          <span>后端或网关未启用运动。执行会被拒绝，不会伪造运动结果。</span>
        </div>
      )}
      {supervised && !hardwareBlocked && (
        <div className="banner banner-warning">
          <strong>受控运动模式</strong>
          <span>
            每次只允许一个关节，最大步长 {formatNumber(robot.capabilities.max_step_rad, 3)} rad，时长 ≥{" "}
            {robot.capabilities.min_duration_s} s。限位映射已验证，但停机点随姿态变化；
            停止只是“保持指令已发送”，不是物理确认，也不是急停。
          </span>
        </div>
      )}
      {!safetyReady && (
        <div className="banner banner-fault">
          <strong>安全门禁已阻止</strong>
          <span>{diagnostics.safety.blockers.join(", ")}</span>
        </div>
      )}
      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}
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
            detail={stopResult.state}
          />
          <span>{stopResult.detail}</span>
          <span className="muted">{stopResult.evidence}</span>
          <button type="button" className="link-button" onClick={() => setStopResult(null)}>
            关闭
          </button>
        </div>
      )}

      <div className="grid grid-2">
        <Card title="关节" subtitle="单关节步进模式只暴露 5 个臂关节；夹爪在姿态接管页">
          <div className="joint-selector">
            {robot.joints.map((item) => (
              <button
                type="button"
                key={item.name}
                className={`joint-option${item.name === joint.name ? " joint-option-active" : ""}`}
                onClick={() => setSelected(item.name)}
              >
                <span className="joint-name mono">{item.name}</span>
                <span className="joint-meta">舵机 {item.servo_id}</span>
                <span className="joint-target mono">
                  {formatAngle(item.target_rad, preferences.unit, preferences.decimals)}
                </span>
                <TruthBadge truth={item.truth} />
                <ValidityBadge validity={item.validity} />
                <DataAge ageMs={item.sample_age_ms} maxAgeMs={500} />
              </button>
            ))}
          </div>
          <p className="footnote">
            限位已按 2026-11-19 手动普查并在 2026-11-20 重载；停机点随姿态变化，不是单一机械极限。
          </p>
        </Card>

        <Card
          title={`目标 · ${joint.name}`}
          subtitle={`舵机 ${joint.servo_id} · 限位${joint.limit_verified ? "已验证" : "暂定"}`}
          actions={
            <StatusChip
              tone="neutral"
              label={connectionLabel(robot.connection_state)}
              detail={robot.connection_state}
            />
          }
        >
          <div className="joint-readout">
            <KeyValue label="当前目标">
              <span className="mono">{formatAngle(joint.target_rad, preferences.unit, preferences.decimals)}</span>
            </KeyValue>
            <KeyValue label="读回">
              <span className="mono">{formatAngle(joint.position_rad, preferences.unit, preferences.decimals)}</span>
            </KeyValue>
            <KeyValue label="原始值">
              <span className="mono">{joint.raw_position ?? "不可用"}</span>
            </KeyValue>
            <KeyValue label="真值">
              <TruthBadge truth={joint.truth} />
            </KeyValue>
            <KeyValue label="限位">
              <span className="mono">
                {formatNumber(joint.limit_min_rad, 3)} … {formatNumber(joint.limit_max_rad, 3)} rad
              </span>
            </KeyValue>
          </div>

          <div className="form-grid">
            <label className="field">
              <span>目标 ({preferences.unit})</span>
              <input
                className="input mono"
                type="number"
                step="0.001"
                value={targetRad}
                onChange={(event) => {
                  setTargetRad(event.target.value);
                  setPreview(null);
                }}
              />
            </label>
            <label className="field">
              <span>时长 (s)</span>
              <input
                className="input mono"
                type="number"
                step="0.1"
                min={robot.capabilities.min_duration_s}
                max={robot.capabilities.max_duration_s}
                value={duration}
                onChange={(event) => {
                  setDuration(event.target.value);
                  setPreview(null);
                }}
              />
            </label>
          </div>

          <label className="field">
            <span>保守滑块（仅本地输入）</span>
            <input
              className="range"
              type="range"
              min={joint.limit_min_rad}
              max={joint.limit_max_rad}
              step={0.01}
              value={sliderValue}
              onChange={(event) => {
                setTargetRad(Number(event.target.value).toFixed(3));
                setPreview(null);
              }}
            />
            <span className="hint">拖动滑块只改变本地输入，不会发送命令。</span>
          </label>

          <div className="button-row">
            <Button variant="primary" onClick={() => void handlePreview()} disabled={busy}>
              {busy ? "预览中…" : "预览"}
            </Button>
            <Button
              variant="danger"
              onClick={() => setConfirmOpen(true)}
              disabled={!canExecute || busy}
              title={disabledReason || "确认后发送一次运动命令"}
            >
              执行（需确认）
            </Button>
            <Button variant="secondary" onClick={() => void handleStop()} disabled={busy}>
              停止请求
            </Button>
            {!lease && (
              <Button variant="ghost" onClick={() => void handleAcquire()} disabled={busy}>
                申请控制权
              </Button>
            )}
          </div>
          {disabledReason && <p className="hint">{disabledReason}</p>}
        </Card>
      </div>

      {preview && (
        <Card
          title="预览结果"
          subtitle={`${preview.valid ? "有效" : "已拒绝"} · 命令 ${preview.command_id.slice(0, 10)}…`}
          actions={
            <StatusChip
              tone={preview.valid ? "ok" : "fault"}
              label={preview.valid ? "有效" : "已拒绝"}
            />
          }
        >
          {preview.valid ? (
            <>
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>关节</th>
                      <th>起点</th>
                      <th>终点</th>
                      <th>变化量</th>
                      <th>原始值</th>
                      <th>时长</th>
                      <th>限位验证</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.targets.map((target) => (
                      <tr key={target.joint}>
                        <td className="mono">{target.joint}</td>
                        <td className="mono">{formatNumber(target.from_rad, 3)}</td>
                        <td className="mono">{formatNumber(target.target_rad, 3)}</td>
                        <td className="mono">{formatNumber(target.delta_rad, 3)}</td>
                        <td className="mono">{target.target_raw}</td>
                        <td className="mono">{target.duration_s} s</td>
                        <td>{target.limit_verified ? "已验证" : "暂定"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <KeyValue label="预览令牌">
                <span className="mono">{preview.preview_token?.slice(0, 16)}…</span>
              </KeyValue>
              <KeyValue label="到期">
                <span className="mono">{formatClock(preview.expires_at, preferences.timeMode)}</span>
              </KeyValue>
              {preview.warnings.length > 0 && (
                <ul className="warning-list">
                  {preview.warnings.map((warning) => (
                    <li key={`${warning.code}-${warning.joint ?? ""}`}>{warning.message}</li>
                  ))}
                </ul>
              )}
            </>
          ) : (
            <ul className="error-list">
              {preview.errors.map((issue) => (
                <li key={`${issue.code}-${issue.joint ?? ""}`}>
                  <strong>{issue.code}</strong> {issue.message}
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}

      {command && (
        <Card
          title="命令状态"
          subtitle={`${command.command_id} · ${latestStage ? stageLabel(latestStage) : stageLabel(command.status)}`}
          actions={
            <StatusChip
              tone={
                (latestStage ?? command.status) === "Completed"
                  ? "ok"
                  : (latestStage ?? command.status) === "Failed" ||
                      (latestStage ?? command.status) === "Unknown"
                    ? "fault"
                    : "warning"
              }
              label={stageLabel(latestStage ?? command.status)}
              detail={latestStage ?? command.status}
            />
          }
        >
          {command.error_code && <p className="error-text">错误：{command.error_code}</p>}
          <div className="stage-history">
            {displayStages.map((stage, index) => (
              <div className="stage-item" key={`${stage.stage}-${index}`}>
                <StatusChip
                  tone={index === displayStages.length - 1 ? "info" : "neutral"}
                  label={stageLabel(stage.stage)}
                  detail={stage.stage}
                />
                <span className="mono">{formatClock(stage.at, preferences.timeMode)}</span>
                {stage.error_code && <span className="error-text">{stage.error_code}</span>}
              </div>
            ))}
          </div>
          <p className="footnote">
            命令状态只由明确证据推进；厂商 SDK 没有 ACK，因此不会出现 <code>Acknowledged</code>。
          </p>
        </Card>
      )}

      <Modal
        open={confirmOpen}
        title="确认执行"
        onClose={() => setConfirmOpen(false)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmOpen(false)} disabled={busy}>
              取消
            </Button>
            <Button variant="danger" onClick={() => void confirmExecute()} disabled={busy}>
              {busy ? "执行中…" : "确认执行"}
            </Button>
          </>
        }
      >
        {preview && (
          <>
            <p>
              即将向 <strong className="mono">{preview.targets.map((target) => target.joint).join(", ")}</strong>{" "}
              发送一次目标命令。请再次核对角度、方向和时长。
            </p>
            <div className="preview-summary">
              {preview.targets.map((target) => (
                <KeyValue key={target.joint} label={target.joint}>
                  <span className="mono">
                    {formatNumber(target.from_rad, 3)} → {formatNumber(target.target_rad, 3)} rad
                    （{target.target_raw} raw）
                  </span>
                </KeyValue>
              ))}
            </div>
            <p className="muted">
              command_id: <span className="mono">{preview.command_id}</span>
            </p>
            <p className="muted">
              这是软件运动命令，不是急停。物理断电始终是独立于本系统的最终防护。
            </p>
          </>
        )}
      </Modal>
    </div>
  );
}
