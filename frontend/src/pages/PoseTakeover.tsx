import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import type {
  CommandRecord,
  MotionPreset,
  MotionPresetList,
  PoseCheck,
  PreviewResult,
  StopResult,
} from "../api/types";
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
} from "../components/ui";
import { formatAngle, formatClock, formatNumber } from "../format";
import { connectionLabel, stageLabel, stopStateLabel, truthLabel } from "../labels";
import { getSessionId } from "../session";
import { usePreferences, useRobotStore } from "../store/robotStore";

const JOINT_NAMES = ["joint1", "joint2", "joint3", "joint4", "joint5"];

function errorText(error: unknown): string {
  return error instanceof ApiError ? `${error.code}: ${error.message}` : String(error);
}

function tcpText(value: number[] | null | undefined): string {
  if (!value || value.length !== 3) return "—";
  return `(${formatNumber(value[0], 3)}, ${formatNumber(value[1], 3)}, ${formatNumber(value[2], 3)}) m`;
}

export function PoseTakeover() {
  const robot = useRobotStore((store) => store.state);
  const diagnostics = useRobotStore((store) => store.diagnostics);
  const lease = useRobotStore((store) => store.lease);
  const events = useRobotStore((store) => store.events);
  const setLease = useRobotStore((store) => store.setLease);
  const preferences = usePreferences();
  const sessionId = getSessionId();

  const [presets, setPresets] = useState<MotionPresetList | null>(null);
  const [presetError, setPresetError] = useState<string | null>(null);
  const [editor, setEditor] = useState<Record<string, string>>(() =>
    Object.fromEntries(JOINT_NAMES.map((name) => [name, "0.000"])),
  );
  const [duration, setDuration] = useState("4.0");
  const [autoDuration, setAutoDuration] = useState(true);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [previewKind, setPreviewKind] = useState<"pose" | "grip">("pose");
  const [gripTarget, setGripTarget] = useState<{ label: string; rad: number } | null>(null);
  const [command, setCommand] = useState<CommandRecord | null>(null);
  const [stopResult, setStopResult] = useState<StopResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [now, setNow] = useState(Date.now());
  const initialized = useRef(false);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    let cancelled = false;
    api
      .motionPresets()
      .then((payload) => {
        if (!cancelled) {
          setPresets(payload);
          setPresetError(null);
        }
      })
      .catch((caught) => {
        if (!cancelled) setPresetError(errorText(caught));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const measured = useMemo(() => {
    const values: Record<string, number | null> = {};
    for (const joint of robot?.joints ?? []) {
      values[joint.name] = joint.position_rad;
    }
    return values;
  }, [robot]);

  useEffect(() => {
    if (initialized.current) return;
    if (!robot || robot.joints.length === 0) return;
    if (robot.joints.some((joint) => joint.position_rad === null)) return;
    setEditor(
      Object.fromEntries(
        JOINT_NAMES.map((name) => [name, targetText(name, measured[name] ?? 0)]),
      ),
    );
    initialized.current = true;
  }, [robot, measured]);

  const gripper = robot?.gripper ?? null;
  const vmax = robot?.capabilities.max_velocity_rad_s ?? 0.5;
  const minDuration = robot?.capabilities.min_duration_s ?? 1.0;
  const maxDuration = robot?.capabilities.max_duration_s ?? 10.0;

  // Six decimals keep a preset that sits on an adopted bound inside the
  // limit; rounding to four decimals turned -1.80117 into -1.8012 and the
  // backend correctly rejected it (OUT_OF_LIMIT).
  const targetText = (name: string, value: number) => {
    const joint = robot?.joints.find((item) => item.name === name);
    let rounded = Number(value.toFixed(6));
    if (joint) {
      rounded = Math.min(joint.limit_max_rad, Math.max(joint.limit_min_rad, rounded));
    }
    return rounded.toFixed(6);
  };

  const numericTargets = useMemo(() => {
    return Object.fromEntries(
      JOINT_NAMES.map((name) => [name, Number(editor[name])]),
    ) as Record<string, number>;
  }, [editor]);

  const suggested = useMemo(() => {
    let delta = 0;
    for (const name of JOINT_NAMES) {
      const from = measured[name];
      const target = numericTargets[name];
      if (from === null || from === undefined || !Number.isFinite(target)) continue;
      delta = Math.max(delta, Math.abs(target - from));
    }
    const value = Math.max(minDuration, Math.ceil((delta / vmax) * 10) / 10);
    return Math.min(maxDuration, value);
  }, [measured, numericTargets, minDuration, maxDuration, vmax]);

  useEffect(() => {
    if (autoDuration) {
      setDuration(suggested.toFixed(1));
    }
  }, [suggested, autoDuration]);

  if (!robot || !diagnostics) {
    return <EmptyState title="正在读取关节状态…">需要后端 /api/state 与 /api/diagnostics。</EmptyState>;
  }

  const numericDuration = Number(duration);
  const hardwareBlocked = robot.mode === "hardware" && !robot.capabilities.hardware_motion;
  const safetyReady = diagnostics.safety.ready;
  const previewExpired = preview?.expires_at ? new Date(preview.expires_at).getTime() <= now : false;
  const canExecuteBase = Boolean(
    preview?.valid &&
      preview.preview_token &&
      lease &&
      !hardwareBlocked &&
      safetyReady &&
      !previewExpired,
  );
  // A gripper button must never execute a pose preview (or the reverse); the
  // preview token is bound to the target set, so the kind must match too.
  const canExecutePose = canExecuteBase && previewKind === "pose";
  const canExecuteGrip = canExecuteBase && previewKind === "grip";

  const disabledReason = (() => {
    if (hardwareBlocked) return "硬件运动未启用（后端或网关已关闭运动）";
    if (!preview) return "先完成预览";
    if (!preview.valid) return "预览未通过，检查错误与姿态门禁";
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

  const completedEvent = commandEvents.find((event) => event.stage === "Completed");
  const verifiedActual = (completedEvent?.details?.actual ?? null) as Record<
    string,
    { raw_position?: number }
  > | null;
  const verificationDeviations = (completedEvent?.details?.deviations_pulses ??
    null) as Record<string, number> | null;

  const loadPreset = (preset: MotionPreset) => {
    setEditor(
      Object.fromEntries(
        JOINT_NAMES.map((name) => [name, targetText(name, preset.joints[name])]),
      ),
    );
    setPreview(null);
    setCommand(null);
    setAutoDuration(true);
  };

  const captureCurrent = () => {
    setEditor(
      Object.fromEntries(
        JOINT_NAMES.map((name) => [name, targetText(name, measured[name] ?? 0)]),
      ),
    );
    setPreview(null);
    setAutoDuration(true);
  };

  const poseTargets = () =>
    JOINT_NAMES.map((name) => ({
      joint: name,
      target_rad: numericTargets[name],
      duration_s: numericDuration,
    }));

  const handlePosePreview = async () => {
    if (!Number.isFinite(numericDuration)) {
      setError("时长必须是有限数值。");
      return;
    }
    const invalid = JOINT_NAMES.filter((name) => !Number.isFinite(numericTargets[name]));
    if (invalid.length > 0) {
      setError(`目标角度无效：${invalid.join(", ")}`);
      return;
    }
    setBusy(true);
    setError(null);
    setCommand(null);
    try {
      const result = await api.preview(sessionId, poseTargets());
      setPreview(result);
      setPreviewKind("pose");
      setGripTarget(null);
    } catch (caught) {
      setPreview(null);
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  };

  const handleGripPreview = async (label: string, rad: number) => {
    setBusy(true);
    setError(null);
    setCommand(null);
    try {
      const result = await api.preview(sessionId, [
        // 5.5 s keeps the full stroke (2.22 rad) under the contract's
        // 100 raw/s validation cap; the manual buttons use the worst case.
        { joint: gripper?.joint ?? "r_joint", target_rad: rad, duration_s: 5.5 },
      ]);
      setPreview(result);
      setPreviewKind("grip");
      setGripTarget({ label, rad });
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
    if (!preview?.preview_token || !lease) return;
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

  const poseCheck: PoseCheck | null = preview?.pose ?? null;
  const selectedPreset = presets?.presets.find((preset) =>
    JOINT_NAMES.every(
      (name) => Math.abs(preset.joints[name] - numericTargets[name]) < 1e-6,
    ),
  );

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1 className="page-title">姿态接管</h1>
          <p className="page-subtitle">
            多关节姿态接管。一次命令 = 五个关节或一个夹爪；流程仍是 预览 → 二次确认 → 执行。
            停止只是“保持指令已发送”，不是急停。
          </p>
        </div>
        <div className="page-actions">
          <StatusChip
            tone={robot.source_label === "SIMULATION" ? "simulation" : "ok"}
            label={robot.source_label === "SIMULATION" ? "模拟" : "实机"}
            detail={robot.source_label}
          />
          <StatusChip tone={lease ? "ok" : "warning"} label={lease ? "持有控制权" : "无控制权"} />
          {gripper && (
            <StatusChip
              tone={gripper.available ? "neutral" : "fault"}
              label={`夹爪 ${gripper.raw_position ?? "?"} raw`}
            />
          )}
        </div>
      </header>

      {hardwareBlocked && (
        <div className="banner banner-warning">
          <strong>硬件运动未启用</strong>
          <span>后端或网关未启用运动。执行会被拒绝，不会伪造运动结果。</span>
        </div>
      )}
      <div className="banner banner-warning">
        <strong>姿态接管 · 无急停 / 无自碰撞模型</strong>
        <span>
          速度上限 {formatNumber(vmax, 2)} rad/s，时长 ≥ {minDuration} s。FK 只检查工作区、桌面平面和低位偏航，
          不能证明自碰撞自由；保持手扶或清空工作区，物理断电仍是最终防护。
        </span>
      </div>
      {!safetyReady && (
        <div className="banner banner-fault">
          <strong>安全门禁已阻止</strong>
          <span>{diagnostics.safety.blockers.join(", ")}</span>
        </div>
      )}
      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}
      {presetError && (
        <ErrorBanner message={`姿态库：${presetError}`} onDismiss={() => setPresetError(null)} />
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
        <Card
          title="姿态库"
          subtitle={
            presets?.available
              ? `${presets.presets.length} 个姿态 · ${presets.scope}`
              : "姿态契约不可用"
          }
          actions={<StatusChip tone="neutral" label={connectionLabel(robot.connection_state)} detail={robot.connection_state} />}
        >
          {presets === null ? (
            <p className="muted">正在读取姿态库…</p>
          ) : presets.presets.length === 0 ? (
            <EmptyState title="没有预设姿态">
              后端不在姿态模式，或 takeover.yaml 没有 presets。
            </EmptyState>
          ) : (
            <div className="joint-selector pose-selector">
              {presets.presets.map((preset) => {
                const check = preset.pose_check;
                const ok = preset.limit_ok && (check?.ok ?? true);
                return (
                  <button
                    type="button"
                    key={preset.id}
                    className={`joint-option${selectedPreset?.id === preset.id ? " joint-option-active" : ""}`}
                    onClick={() => loadPreset(preset)}
                    title={preset.source}
                  >
                    <span className="joint-name">{preset.label}</span>
                    <span className="joint-meta mono">{preset.id}</span>
                    <span className="joint-meta">
                      {preset.verified_on_hardware ? "实机验证" : "限位边界变体"}
                    </span>
                    <StatusChip tone={ok ? "ok" : "fault"} label={ok ? "门禁通过" : "门禁拒绝"} />
                  </button>
                );
              })}
            </div>
          )}
          <p className="footnote">
            点击预设只载入编辑器，不发送命令。{presets?.gate_error ? ` FK 不可用：${presets.gate_error.join("; ")}` : ""}
          </p>
          <div className="joint-readout">
            <KeyValue label="当前 TCP">{tcpText(presets?.current.tcp_m ?? null)}</KeyValue>
            <KeyValue label="来源">
              <span className="mono">
                {presets?.current.source
                  ? truthLabel(presets.current.source)
                  : presets?.current.measured
                    ? "实测"
                    : "指令回显"}
              </span>
            </KeyValue>
          </div>
        </Card>

        <Card
          title="目标姿态"
          subtitle={`5 关节 · ${selectedPreset ? selectedPreset.label : "自定义"}`}
          actions={
            <StatusChip
              tone={selectedPreset ? "info" : "neutral"}
              label={selectedPreset ? "预设" : "自定义"}
            />
          }
        >
          <div className="table-wrap">
            <table className="data-table pose-table">
              <thead>
                <tr>
                  <th>关节</th>
                  <th>实测</th>
                  <th>目标 (rad)</th>
                  <th>Δ</th>
                  <th>rad/s</th>
                  <th>限位</th>
                  <th>时延</th>
                </tr>
              </thead>
              <tbody>
                {JOINT_NAMES.map((name) => {
                  const joint = robot.joints.find((item) => item.name === name);
                  const target = numericTargets[name];
                  const from = measured[name];
                  const delta = from === null || from === undefined ? null : target - from;
                  const velocity =
                    delta === null || !Number.isFinite(numericDuration) || numericDuration <= 0
                      ? null
                      : Math.abs(delta) / numericDuration;
                  const overLimit = joint
                    ? target < joint.limit_min_rad || target > joint.limit_max_rad
                    : false;
                  return (
                    <tr key={name}>
                      <td className="mono">{name}</td>
                      <td className="mono">{formatAngle(from, "rad", 3)}</td>
                      <td>
                        <input
                          className="input mono"
                          type="number"
                          step="0.001"
                          value={editor[name]}
                          style={overLimit ? { borderColor: "var(--fault)" } : undefined}
                          onChange={(event) => {
                            setEditor((current) => ({ ...current, [name]: event.target.value }));
                            setPreview(null);
                          }}
                        />
                      </td>
                      <td className="mono">{formatNumber(delta, 3)}</td>
                      <td className="mono">
                        {velocity !== null && velocity > vmax ? (
                          <span className="error-text">{formatNumber(velocity, 3)}</span>
                        ) : (
                          formatNumber(velocity, 3)
                        )}
                      </td>
                      <td className="mono">
                        {joint
                          ? `${formatNumber(joint.limit_min_rad, 3)} … ${formatNumber(joint.limit_max_rad, 3)}`
                          : "—"}
                      </td>
                      <td>
                        <DataAge ageMs={joint?.sample_age_ms ?? null} maxAgeMs={500} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="form-grid">
            <label className="field">
              <span>时长 (s)</span>
              <input
                className="input mono"
                type="number"
                step="0.1"
                min={minDuration}
                max={maxDuration}
                value={duration}
                onChange={(event) => {
                  setDuration(event.target.value);
                  setAutoDuration(false);
                  setPreview(null);
                }}
              />
              <span className="hint">建议 {suggested.toFixed(1)} s（按 {vmax} rad/s）</span>
            </label>
            <label className="field">
              <span>自动时长</span>
              <input
                type="checkbox"
                checked={autoDuration}
                onChange={(event) => {
                  setAutoDuration(event.target.checked);
                  setPreview(null);
                }}
              />
              <span className="hint">跟随最大关节差自动计算</span>
            </label>
          </div>

          <div className="button-row">
            <Button variant="ghost" onClick={captureCurrent} disabled={busy}>
              捕获当前姿态
            </Button>
            <Button variant="primary" onClick={() => void handlePosePreview()} disabled={busy}>
              {busy ? "预览中…" : "预览姿态"}
            </Button>
            <Button
              variant="danger"
              onClick={() => setConfirmOpen(true)}
              disabled={!canExecutePose || busy}
              title={
                previewKind !== "pose"
                  ? "当前预览是夹爪命令；先预览姿态"
                  : disabledReason || "确认后发送一次姿态命令"
              }
            >
              执行姿态（需确认）
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

      <Card
        title="夹爪"
        subtitle="r_joint · 闭合按时间完成（失速不是失败）"
        actions={
          <StatusChip
            tone={gripper?.available ? "ok" : "fault"}
            label={gripper?.available ? "实测" : "不可用"}
          />
        }
      >
        {gripper === null ? (
          <p className="muted">夹爪反馈不可用（后端不在姿态模式，或网关没有 r_joint）。</p>
        ) : (
          <>
            <div className="joint-readout">
              <KeyValue label="实测">
                <span className="mono">
                  {formatNumber(gripper.position_rad, 4)} rad · {gripper.raw_position ?? "?"} raw
                </span>
              </KeyValue>
              <KeyValue label="真值">
                <TruthBadge truth={gripper.truth} />
              </KeyValue>
              <KeyValue label="命令范围">
                <span className="mono">
                  {gripper.open_raw}（张开）… {gripper.close_raw}（闭合）raw
                </span>
              </KeyValue>
              <KeyValue label="实测止点">
                <span className="mono">
                  {gripper.measured_min_raw} … {gripper.measured_max_raw} raw
                </span>
              </KeyValue>
              <KeyValue label="时延">
                <DataAge ageMs={gripper.sample_age_ms} maxAgeMs={500} />
              </KeyValue>
            </div>
            <div className="button-row">
              <Button
                variant="secondary"
                onClick={() => void handleGripPreview("open", gripper.min_rad)}
                disabled={busy || !gripper.available}
              >
                预览张开
              </Button>
              <Button
                variant="secondary"
                onClick={() => void handleGripPreview("close", gripper.max_rad)}
                disabled={busy || !gripper.available}
              >
                预览闭合
              </Button>
              <Button
                variant="danger"
                onClick={() => setConfirmOpen(true)}
                disabled={!canExecuteGrip || busy}
                title={
                  previewKind !== "grip"
                    ? "当前预览是姿态命令；先预览夹爪"
                    : disabledReason || "确认后发送一次夹爪命令"
                }
              >
                执行夹爪（需确认）
              </Button>
            </div>
            <p className="footnote">
              夹爪没有力/接触反馈：完成只代表插值时间已到，测量到的寄存器是唯一证据。
              闭合在空夹爪上会停在约 {gripper.measured_max_raw} raw。
            </p>
          </>
        )}
      </Card>

      {preview && (
        <Card
          title={previewKind === "grip" ? `夹爪预览 · ${gripTarget?.label ?? ""}` : "姿态预览"}
          subtitle={`${preview.valid ? "有效" : "已拒绝"} · 命令 ${preview.command_id.slice(0, 10)}…`}
          actions={
            <StatusChip tone={preview.valid ? "ok" : "fault"} label={preview.valid ? "有效" : "已拒绝"} />
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
                      <th>完成方式</th>
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
                        <td className="mono">
                          {target.completion === "timed" ? "定时" : target.completion === "position" ? "位置" : target.completion ?? "位置"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {poseCheck && (
                <div className="joint-readout">
                  <KeyValue label="目标 TCP">{tcpText(poseCheck.tcp_m)}</KeyValue>
                  <KeyValue label="桌面余量">
                    <span className="mono">
                      {poseCheck.table_clearance_m === null
                        ? "—"
                        : `${formatNumber(poseCheck.table_clearance_m * 1000, 1)} mm`}
                    </span>
                  </KeyValue>
                  <KeyValue label="路径采样">
                    <span className="mono">{poseCheck.samples}</span>
                  </KeyValue>
                  <KeyValue label="起点">
                    <StatusChip
                      tone={poseCheck.start_compliant ? "ok" : "warning"}
                      label={poseCheck.start_compliant ? "门禁内" : "门禁外（豁免）"}
                    />
                  </KeyValue>
                </div>
              )}
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
          subtitle={`${command.command_id} · ${stageLabel(latestStage ?? command.status)}`}
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
          {verifiedActual && Object.keys(verifiedActual).length > 0 && (
            <div className="joint-readout">
              <KeyValue label="总线实测">
                <span className="mono">
                  {Object.entries(verifiedActual)
                    .map(([joint, item]) => `${joint} ${item.raw_position ?? "?"}`)
                    .join(" · ")}
                </span>
              </KeyValue>
              {verificationDeviations && (
                <KeyValue label="Δ 目标 (pulse)">
                  <span className="mono">
                    {Object.entries(verificationDeviations)
                      .map(
                        ([joint, delta]) =>
                          `${joint} ${delta > 0 ? "+" : ""}${delta}`,
                      )
                      .join(" · ")}
                  </span>
                </KeyValue>
              )}
            </div>
          )}
          <p className="footnote">
            姿态完成 = 插值后 <code>GetBusServoState</code> 的实际寄存器在 ±12 pulse 内；
            夹爪完成 = 插值时间到 + 实际寄存器（settled），不是力反馈。
            <code>/controller_manager/servo_states</code> 是命令缓存，不是实测。
          </p>
        </Card>
      )}

      <Modal
        open={confirmOpen}
        title={previewKind === "grip" ? "确认夹爪命令" : "确认姿态命令"}
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
              即将向{" "}
              <strong className="mono">
                {preview.targets.map((target) => target.joint).join(", ")}
              </strong>{" "}
              发送一次命令。请核对每个关节的目标、方向与时长。
            </p>
            <div className="preview-summary">
              {preview.targets.map((target) => (
                <KeyValue key={target.joint} label={target.joint}>
                  <span className="mono">
                    {formatNumber(target.from_rad, 3)} → {formatNumber(target.target_rad, 3)} rad
                    （{target.target_raw} raw · {target.duration_s} s）
                  </span>
                </KeyValue>
              ))}
            </div>
            {poseCheck && (
              <KeyValue label="目标 TCP / 桌面余量">
                <span className="mono">
                  {tcpText(poseCheck.tcp_m)} ·{" "}
                  {poseCheck.table_clearance_m === null
                    ? "—"
                    : `${formatNumber(poseCheck.table_clearance_m * 1000, 1)} mm`}
                </span>
              </KeyValue>
            )}
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
