import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api, ApiError } from "../api/client";
import type {
  CameraReference,
  VisualFrame,
  VisualSnapshot,
} from "../api/types";
import {
  Button,
  Card,
  EmptyState,
  KeyValue,
  SourceBadge,
  StatusChip,
  TruthBadge,
} from "../components/ui";
import { formatAge, formatAngle, formatNumber } from "../format";
import { jointTypeLabel, tfStatusLabel, truthLabel } from "../labels";
import { usePreferences } from "../store/robotStore";

type Point3 = [number, number, number];

const LABEL_FRAMES = new Set([
  "base_link",
  "link1",
  "link2",
  "link3",
  "link4",
  "link5",
  "camera_optical_calibrated",
]);

const AXIS_FRAMES = new Set([
  "base_link",
  "link1",
  "link2",
  "link3",
  "link4",
  "link5",
  "camera_connet_link",
  "camera_optical_calibrated",
]);

function project(point: Point3, yaw: number, pitch: number): [number, number] {
  const cy = Math.cos(yaw);
  const sy = Math.sin(yaw);
  const rx = point[0] * cy - point[1] * sy;
  const ry = point[0] * sy + point[1] * cy;
  const screenX = rx;
  const screenY = -(point[2] * Math.cos(pitch) - ry * Math.sin(pitch));
  return [screenX, screenY];
}

function rotated(
  frame: VisualFrame,
  local: Point3,
): Point3 {
  const [r0, r1, r2, r3, r4, r5, r6, r7, r8] = frame.rotation;
  return [
    frame.position_m[0] + r0 * local[0] + r1 * local[1] + r2 * local[2],
    frame.position_m[1] + r3 * local[0] + r4 * local[1] + r5 * local[2],
    frame.position_m[2] + r6 * local[0] + r7 * local[1] + r8 * local[2],
  ];
}

function KinematicView({
  frames,
  cameraFrame,
  yaw,
  pitch,
  showAxes,
  showLabels,
  showFrustum,
}: {
  frames: VisualFrame[];
  cameraFrame: VisualFrame | null;
  yaw: number;
  pitch: number;
  showAxes: boolean;
  showLabels: boolean;
  showFrustum: boolean;
}) {
  const layout = useMemo(() => {
    if (frames.length === 0) return null;
    const width = 800;
    const height = 540;
    const pad = 52;
    let minX = Number.POSITIVE_INFINITY;
    let maxX = Number.NEGATIVE_INFINITY;
    let minY = Number.POSITIVE_INFINITY;
    let maxY = Number.NEGATIVE_INFINITY;
    const projected = frames.map((frame) => {
      const [x, y] = project(frame.position_m, yaw, pitch);
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
      return { frame, x, y };
    });
    const spanX = Math.max(maxX - minX, 1e-6);
    const spanY = Math.max(maxY - minY, 1e-6);
    const scale = Math.min((width - 2 * pad) / spanX, (height - 2 * pad) / spanY);
    const offsetX = (width - spanX * scale) / 2;
    const offsetY = (height - spanY * scale) / 2;
    const points = new Map<string, [number, number]>();
    for (const item of projected) {
      const screenX = offsetX + (item.x - minX) * scale;
      const screenY = height - (offsetY + (item.y - minY) * scale);
      points.set(item.frame.name, [screenX, screenY]);
    }
    const toScreen = (point: Point3): [number, number] => {
      const [x, y] = project(point, yaw, pitch);
      return [
        offsetX + (x - minX) * scale,
        height - (offsetY + (y - minY) * scale),
      ];
    };
    return { width, height, points, toScreen, scale };
  }, [frames, yaw, pitch]);

  if (!layout) {
    return <EmptyState title="没有可渲染的模型">URDF/TF 不可用；安全链路不受影响。</EmptyState>;
  }
  const { width, height, points, toScreen, scale } = layout;

  const gridLines: ReactNode[] = [];
  for (let value = -0.3; value <= 0.5; value += 0.1) {
    const a = toScreen([value, -0.4, 0]);
    const b = toScreen([value, 0.4, 0]);
    gridLines.push(
      <line key={`gx-${value}`} x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} className="visual-grid-line" />,
    );
  }
  for (let value = -0.4; value <= 0.4; value += 0.1) {
    const a = toScreen([-0.3, value, 0]);
    const b = toScreen([0.5, value, 0]);
    gridLines.push(
      <line key={`gy-${value}`} x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} className="visual-grid-line" />,
    );
  }

  return (
    <svg
      className="kinematic-svg"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="URDF 运动学模型"
    >
      <g>{gridLines}</g>
      {frames.map((frame) => {
        if (!frame.parent) return null;
        const parent = points.get(frame.parent);
        const child = points.get(frame.name);
        if (!parent || !child) return null;
        const camera = frame.source === "measured_hand_eye";
        return (
          <line
            key={`bone-${frame.name}`}
            x1={parent[0]}
            y1={parent[1]}
            x2={child[0]}
            y2={child[1]}
            className={camera ? "visual-bone-camera" : "visual-bone"}
          />
        );
      })}
      {showAxes &&
        frames.map((frame) => {
          if (!AXIS_FRAMES.has(frame.name)) return null;
          const origin = points.get(frame.name);
          if (!origin) return null;
          const axes: Array<[Point3, string]> = [
            [[0.035, 0, 0], "visual-axis-x"],
            [[0, 0.035, 0], "visual-axis-y"],
            [[0, 0, 0.035], "visual-axis-z"],
          ];
          return (
            <g key={`axes-${frame.name}`}>
              {axes.map(([local, className]) => {
                const end = toScreen(rotated(frame, local));
                return (
                  <line
                    key={`${frame.name}-${className}`}
                    x1={origin[0]}
                    y1={origin[1]}
                    x2={end[0]}
                    y2={end[1]}
                    className={className}
                  />
                );
              })}
            </g>
          );
        })}
      {showFrustum &&
        cameraFrame &&
        (() => {
          const origin = points.get(cameraFrame.name);
          if (!origin) return null;
          const depth = 0.08;
          const halfW = 0.03;
          const halfH = 0.022;
          const corners: Point3[] = [
            [-halfW, -halfH, depth],
            [halfW, -halfH, depth],
            [halfW, halfH, depth],
            [-halfW, halfH, depth],
          ];
          const projected = corners.map((corner) => toScreen(rotated(cameraFrame, corner)));
          return (
            <g>
              {projected.map((corner, index) => (
                <line
                  key={`ray-${index}`}
                  x1={origin[0]}
                  y1={origin[1]}
                  x2={corner[0]}
                  y2={corner[1]}
                  className="visual-frustum"
                />
              ))}
              <polygon
                points={projected.map((corner) => corner.join(",")).join(" ")}
                className="visual-frustum-plane"
              />
            </g>
          );
        })()}
      {frames.map((frame) => {
        const point = points.get(frame.name);
        if (!point) return null;
        const camera = frame.is_camera;
        return (
          <g key={`joint-${frame.name}`}>
            <circle
              cx={point[0]}
              cy={point[1]}
              r={camera ? 5 : 3}
              className={camera ? "visual-joint-camera" : "visual-joint"}
            />
            {showLabels && LABEL_FRAMES.has(frame.name) && (
              <text x={point[0] + 6} y={point[1] - 5} className="visual-label">
                {frame.name}
              </text>
            )}
          </g>
        );
      })}
      <text x={12} y={20} className="visual-scale-label">
        比例 ≈ {formatNumber(scale * 0.1, 1)} px / 100 mm
      </text>
    </svg>
  );
}

function formatError(caught: unknown): string {
  if (caught instanceof ApiError) {
    return `${caught.code}: ${caught.message}`;
  }
  return caught instanceof Error ? caught.message : String(caught);
}

export function VisualWorkbench() {
  const preferences = usePreferences();
  const [visual, setVisual] = useState<VisualSnapshot | null>(null);
  const [references, setReferences] = useState<CameraReference[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [cameraTick, setCameraTick] = useState(0);
  const [cameraFailed, setCameraFailed] = useState(false);
  const [yaw, setYaw] = useState(-35);
  const [pitch, setPitch] = useState(25);
  const [showAxes, setShowAxes] = useState(true);
  const [showLabels, setShowLabels] = useState(true);
  const [showFrustum, setShowFrustum] = useState(false);

  const refreshReferences = useCallback(async () => {
    try {
      const list = await api.cameraReferences();
      setReferences(list.items);
    } catch (caught) {
      setError(formatError(caught));
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const snapshot = await api.visual();
        if (!cancelled) {
          setVisual(snapshot);
          setError(null);
        }
      } catch (caught) {
        if (!cancelled) setError(formatError(caught));
      } finally {
        if (!cancelled) setLoading(false);
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
    void refreshReferences();
    const timer = window.setInterval(() => void refreshReferences(), 5000);
    return () => window.clearInterval(timer);
  }, [refreshReferences]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setCameraTick((tick) => tick + 1);
      setCameraFailed(false);
    }, 300);
    return () => window.clearInterval(timer);
  }, []);

  const frames = visual?.tf.frames ?? [];
  const camera = visual?.camera ?? null;
  const cameraFrame = visual?.tf.camera_frame ?? null;
  const tfStatus = visual?.tf.status ?? "unavailable";
  const tfTone =
    tfStatus === "live"
      ? "ok"
      : tfStatus === "live_static_only" || tfStatus === "model_only"
        ? "warning"
        : tfStatus === "degraded"
          ? "fault"
          : "critical";

  const saveReference = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const saved = await api.saveCameraReference(note);
      setNotice(
        `已保存引用 ${saved.reference_id}（${saved.truth} / ${saved.calibration_version}）。仅保存元数据，不保存图像字节。`,
      );
      setNote("");
      await refreshReferences();
    } catch (caught) {
      setError(formatError(caught));
    } finally {
      setBusy(false);
    }
  };

  const yawRadians = (yaw * Math.PI) / 180;
  const pitchRadians = (pitch * Math.PI) / 180;

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1 className="page-title">可视化工作台</h1>
          <p className="page-subtitle">
            G4 只读可视化：URDF 前向运动学、相机与标定/TF 警示。渲染层不进入运动或安全控制链。
          </p>
        </div>
        <div className="page-actions">
          {visual && <SourceBadge source={visual.source_label} />}
          <StatusChip tone={tfTone} label={`TF ${tfStatusLabel(tfStatus)}`} detail={tfStatus} />
          <Button variant="secondary" onClick={() => void refreshReferences()} disabled={busy}>
            刷新引用
          </Button>
        </div>
      </header>

      {error && (
        <div className="banner banner-fault">
          <strong>错误</strong>
          <span className="mono">{error}</span>
        </div>
      )}
      {notice && (
        <div className="banner banner-simulation">
          <strong>相机引用</strong>
          <span>{notice}</span>
        </div>
      )}
      {loading && !visual ? (
        <EmptyState title="正在读取模型…">后端会解析 URDF 并计算当前关节的模型帧。</EmptyState>
      ) : visual ? (
        <>
          <div className="banner banner-warning">
            <strong>只读可视化</strong>
            <span>
              模型帧是 URDF + 当前关节状态（真值={truthLabel(visual.tf.joint_truth)}）的前向运动学，不是实时
              tf2。渲染失败不会影响安全门禁、控制权或运动链路。
            </span>
          </div>
          {visual.warnings.length > 0 && (
            <div className="banner banner-warning">
              <strong>模型警告</strong>
              <ul className="warning-list">
                {visual.warnings.slice(0, 8).map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="visual-layout">
            <div className="visual-main">
              <Card
                title="运动学模型"
                subtitle={`${visual.urdf.robot_name ?? "URDF"} · ${visual.tf.frames.length} 个框架 · ${visual.tf.source} · live=${visual.tf.live}`}
                actions={<StatusChip tone="neutral" label={`真值 ${truthLabel(visual.tf.joint_truth)}`} detail={`truth=${visual.tf.joint_truth}`} />}
              >
                <div className="visual-controls">
                  <label className="field">
                    <span>偏航 {yaw}°</span>
                    <input
                      className="range"
                      type="range"
                      min={-180}
                      max={180}
                      value={yaw}
                      onChange={(event) => setYaw(Number(event.target.value))}
                    />
                  </label>
                  <label className="field">
                    <span>俯仰 {pitch}°</span>
                    <input
                      className="range"
                      type="range"
                      min={5}
                      max={85}
                      value={pitch}
                      onChange={(event) => setPitch(Number(event.target.value))}
                    />
                  </label>
                  <div className="visual-toggles">
                    <label className="series-toggle">
                      <input
                        type="checkbox"
                        checked={showAxes}
                        onChange={(event) => setShowAxes(event.target.checked)}
                      />
                      关节轴
                    </label>
                    <label className="series-toggle">
                      <input
                        type="checkbox"
                        checked={showLabels}
                        onChange={(event) => setShowLabels(event.target.checked)}
                      />
                      框架标签
                    </label>
                    <label className="series-toggle">
                      <input
                        type="checkbox"
                        checked={showFrustum}
                        onChange={(event) => setShowFrustum(event.target.checked)}
                      />
                      相机视锥
                    </label>
                  </div>
                </div>
                <KinematicView
                  frames={frames}
                  cameraFrame={cameraFrame}
                  yaw={yawRadians}
                  pitch={pitchRadians}
                  showAxes={showAxes}
                  showLabels={showLabels}
                  showFrustum={showFrustum}
                />
                <div className="chart-legend visual-legend">
                  <span className="legend-item"><span className="legend-swatch visual-bone-swatch" /> URDF 连杆</span>
                  <span className="legend-item"><span className="legend-swatch visual-camera-swatch" /> 实测手眼</span>
                  <span className="legend-item"><span className="legend-swatch visual-axis-x-swatch" /> x</span>
                  <span className="legend-item"><span className="legend-swatch visual-axis-y-swatch" /> y</span>
                  <span className="legend-item"><span className="legend-swatch visual-axis-z-swatch" /> z</span>
                  <span className="legend-item">轴长 35 mm · 视锥 80 mm</span>
                </div>
                <p className="hint">
                  基座为 base_link；视角是等轴投影，只用于结构检查。模型/指令/实测的真值必须以下方表格与顶栏为准。
                </p>
              </Card>

              <div className="visual-lower-grid">
                <Card
                  title="关节与限位"
                  subtitle="URDF 与已验证映射并列；超出限位会标红"
                >
                  <div className="table-wrap">
                    <table className="data-table visual-table">
                      <thead>
                        <tr>
                          <th>关节</th>
                          <th>位置</th>
                          <th>限位 ({preferences.unit})</th>
                          <th>限位 / 状态</th>
                        </tr>
                      </thead>
                      <tbody>
                        {visual.joints.map((joint) => (
                          <tr key={joint.name} className={joint.within_limits === false ? "row-fault" : ""}>
                            <td>
                              <strong>{joint.name}</strong>
                              <div className="muted">{jointTypeLabel(joint.type)}</div>
                            </td>
                            <td className="mono">
                              {joint.position_rad === null
                                ? "不可用"
                                : formatAngle(joint.position_rad, preferences.unit, preferences.decimals)}
                              <div className="muted">{joint.position_source}</div>
                            </td>
                            <td className="mono">
                              {joint.lower_rad === null || joint.upper_rad === null
                                ? "—"
                                : `${formatNumber(joint.lower_rad * (preferences.unit === "deg" ? 180 / Math.PI : 1), 2)} … ${formatNumber(
                                    joint.upper_rad * (preferences.unit === "deg" ? 180 / Math.PI : 1),
                                    2,
                                  )}`}
                            </td>
                            <td>
                              <div className="muted">
                                {joint.limit_source === "software_config" ? "映射" : joint.limit_source}
                              </div>
                              <StatusChip
                                tone={joint.verified ? "ok" : "warning"}
                                label={joint.verified ? "已验证" : "暂定"}
                              />
                              <StatusChip
                                tone={
                                  joint.within_limits === null
                                    ? "neutral"
                                    : joint.within_limits
                                      ? "ok"
                                      : "fault"
                                }
                                label={
                                  joint.within_limits === null
                                    ? "未知"
                                    : joint.within_limits
                                      ? "限位内"
                                      : "超出限位"
                                }
                              />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Card>

                <Card title="坐标框架" subtitle="名称、父级与模型位置（mm）">
                  <div className="table-wrap frame-table">
                    <table className="data-table visual-table">
                      <thead>
                        <tr>
                          <th>框架</th>
                          <th>父级</th>
                          <th>位置 (mm)</th>
                          <th>来源</th>
                        </tr>
                      </thead>
                      <tbody>
                        {frames.map((frame) => (
                          <tr key={frame.name}>
                            <td>
                              <strong>{frame.name}</strong>
                              {frame.nominal && <span className="muted"> 标称</span>}
                            </td>
                            <td className="mono">{frame.parent ?? "—"}</td>
                            <td className="mono">
                              {frame.position_m
                                .map((value) => (value * 1000).toFixed(1))
                                .join(", ")}
                            </td>
                            <td className="mono">{frame.source}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Card>
              </div>
            </div>

            <aside className="visual-side">
              <Card
                title="相机 · RGB"
                subtitle={camera ? `${camera.topic} · ${camera.frame}` : "相机状态不可用"}
                actions={camera && <TruthBadge truth={camera.truth} />}
              >
                {camera?.available ? (
                  <div className="camera-frame visual-camera-frame">
                    {cameraFailed ? (
                      <div className="camera-fallback">快照不可用</div>
                    ) : (
                      <img
                        key={cameraTick}
                        src={api.cameraSnapshotUrl(cameraTick)}
                        alt="ArmPi 相机"
                        onError={() => setCameraFailed(true)}
                      />
                    )}
                    <div className="visual-camera-overlay">
                      <span className="visual-frame-label">{camera.frame}</span>
                      <span className="visual-frame-warning">
                        机身框架 · PnP 使用 {camera.optical_frame ?? "光学框架"}
                      </span>
                      <span className="visual-frame-meta">
                        {camera.width}×{camera.height} · {formatNumber(camera.fps, 1)} fps ·{" "}
                        {camera.encoding ?? "—"} · 时延 {formatAge(camera.sample_age_ms)}
                      </span>
                    </div>
                  </div>
                ) : (
                  <EmptyState title="相机不可用">
                    没有可显示的相机帧；不填充模拟正常值。
                  </EmptyState>
                )}

                {camera && (
                  <div className="kv-list visual-camera-meta">
                    <KeyValue label="分辨率">
                      {camera.width && camera.height ? `${camera.width}×${camera.height}` : "—"}
                    </KeyValue>
                    <KeyValue label="编码">{camera.encoding ?? "—"}</KeyValue>
                    <KeyValue label="帧时延">{formatAge(camera.sample_age_ms)}</KeyValue>
                    <KeyValue label="真值">{truthLabel(camera.truth)}</KeyValue>
                    <KeyValue label="框架来源">{camera.frame ?? "—"}（机身）</KeyValue>
                    <KeyValue label="TF 状态">{tfStatusLabel(camera.tf_status ?? visual.tf.status)}</KeyValue>
                    <KeyValue label="TF 框架">
                      {camera.tf_frames?.length ? camera.tf_frames.join(", ") : "无"}
                    </KeyValue>
                    <KeyValue label="检测">不可用（无实时检测器）</KeyValue>
                    <KeyValue label="光学框架">{camera.optical_frame ?? "不可用"}</KeyValue>
                  </div>
                )}
                {camera && camera.warnings.length > 0 && (
                  <ul className="warning-list">
                    {camera.warnings.map((warning) => (
                      <li key={warning}>{warning}</li>
                    ))}
                  </ul>
                )}

                <div className="camera-note-row">
                  <input
                    className="input"
                    placeholder="引用备注（如：运行前采样）"
                    value={note}
                    maxLength={500}
                    onChange={(event) => setNote(event.target.value)}
                  />
                  <Button
                    variant="primary"
                    onClick={() => void saveReference()}
                    disabled={busy || !camera?.available}
                  >
                    保存引用
                  </Button>
                </div>
                <p className="hint">
                  保存的是时间戳、分辨率、标定版本和 TF 状态等元数据；不保存图像字节。
                </p>
              </Card>

              <Card title="相机标定" subtitle="测量外参，不是实时 TF">
                {camera ? (
                  <>
                    <div className="kv-list">
                      <KeyValue label="光学框架">{camera.optical_frame ?? "不可用"}</KeyValue>
                      <KeyValue label="机身框架">{camera.frame ?? "—"}</KeyValue>
                      <KeyValue label="父级">{camera.calibration.parent_link ?? "—"}</KeyValue>
                      <KeyValue label="版本">
                        {camera.calibration.calibration_version || "—"}
                      </KeyValue>
                      <KeyValue label="平移">
                        {camera.calibration.translation_m
                          .map((value) => (value * 1000).toFixed(1))
                          .join(", ")}{" "}
                        mm
                      </KeyValue>
                      <KeyValue label="RPY">
                        {camera.calibration.rpy_deg
                          .map((value) => value.toFixed(3))
                          .join(", ")}{" "}
                        °
                      </KeyValue>
                      <KeyValue label="真值">
                        <TruthBadge truth={camera.calibration.truth} />
                      </KeyValue>
                    </div>
                    <p className="hint">{camera.calibration.calibration_source || "来源不可用"}</p>
                    <ul className="note-list">
                      {camera.calibration.notes.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  </>
                ) : (
                  <p className="muted">校准数据不可用</p>
                )}
              </Card>

              <Card title="模型来源" subtitle="可追溯的 URDF 与审计状态">
                <div className="kv-list">
                  <KeyValue label="路径">
                    <span className="mono visual-path">{visual.urdf.path}</span>
                  </KeyValue>
                  <KeyValue label="SHA-256">
                    <span className="mono" title={visual.urdf.sha256 ?? undefined}>
                      {visual.urdf.sha256
                        ? `${visual.urdf.sha256.slice(0, 16)}…${visual.urdf.sha256.slice(-8)}`
                        : "—"}
                    </span>
                  </KeyValue>
                  <KeyValue label="验证">
                    <StatusChip
                      tone={visual.urdf.verified ? "ok" : "warning"}
                      label={visual.urdf.verified ? "已验证" : "暂定"}
                    />
                  </KeyValue>
                  <KeyValue label="连杆 / 关节">
                    {visual.urdf.links} / {visual.urdf.joints}
                  </KeyValue>
                  <KeyValue label="TF 状态">
                    <StatusChip tone={tfTone} label={tfStatusLabel(visual.tf.status)} detail={visual.tf.status} />
                  </KeyValue>
                  <KeyValue label="TF 来源">
                    {visual.tf.source}（live={visual.tf.live ? "是" : "否"}）
                  </KeyValue>
                  <KeyValue label="TF 真值">{visual.tf.truth}</KeyValue>
                  <KeyValue label="TF 网关">
                    {visual.tf.gateway_status ?? "—"} · {visual.tf.dynamic_edges ?? 0} 动态 /{" "}
                    {visual.tf.static_edges ?? 0} 静态
                  </KeyValue>
                  <KeyValue label="TF 最大时延">
                    {formatAge(visual.tf.max_tf_age_ms ?? null)}
                  </KeyValue>
                  <KeyValue label="关节真值">{visual.tf.joint_truth}</KeyValue>
                  <KeyValue label="样本时延">{formatAge(visual.tf.max_sample_age_ms)}</KeyValue>
                </div>
                <p className="hint">{visual.urdf.source_note || "无来源说明。"}</p>
              </Card>

              <Card title="已保存引用" subtitle={`${references.length} 条元数据引用`}>
                {references.length === 0 ? (
                  <EmptyState title="还没有引用">
                    在相机卡片填写备注并保存；不会保存图像文件。
                  </EmptyState>
                ) : (
                  <div className="table-wrap frame-table">
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>创建时间</th>
                          <th>真值</th>
                          <th>标定</th>
                          <th>TF</th>
                          <th>备注</th>
                        </tr>
                      </thead>
                      <tbody>
                        {references.map((reference) => (
                          <tr key={reference.reference_id}>
                            <td className="mono">{reference.created_at}</td>
                            <td><TruthBadge truth={reference.truth} /></td>
                            <td className="mono ellipsis">{reference.calibration_version}</td>
                            <td className="mono">{reference.tf_status}</td>
                            <td className="ellipsis" title={reference.note}>
                              {reference.note || "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>
            </aside>
          </div>
        </>
      ) : null}
    </div>
  );
}
