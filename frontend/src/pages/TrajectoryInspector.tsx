import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import type {
  StreamQuality,
  TrajectoryDataset,
  TrajectoryRecord,
  TrajectoryStream,
  TrajectorySummary,
} from "../api/types";
import {
  Button,
  Card,
  EmptyState,
  KeyValue,
  MetricValue,
  StatusChip,
  TruthBadge,
} from "../components/ui";
import { formatNumber } from "../format";
import { originLabel, stageLabel, streamLabel, truthLabel } from "../labels";
import { usePreferences, useRobotStore } from "../store/robotStore";

const COLORS = {
  planned: "#5aa9ff",
  commanded: "#f5a623",
  feedback: "#44d492",
  error: "#ff6b6b",
};

type SampleStream = "planned" | "commanded" | "feedback";

interface ChartSeries {
  label: string;
  color: string;
  values: Array<[number, number]>;
  dashed?: boolean;
}

function datasetTimes(dataset: TrajectoryDataset): { min: number; max: number } {
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  for (const stream of [dataset.planned, dataset.commanded, dataset.feedback]) {
    const offset = stream.time_offset_s ?? 0;
    for (const point of stream.points) {
      const t = point.t_s + offset;
      if (t < min) min = t;
      if (t > max) max = t;
    }
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return { min: 0, max: 0 };
  return { min, max };
}

function streamTimes(stream: TrajectoryStream, jointIndex: number): Array<[number, number]> {
  const offset = stream.time_offset_s ?? 0;
  return stream.points.map((point) => [point.t_s + offset, point.positions[jointIndex]]);
}

function holdValue(
  stream: TrajectoryStream,
  jointIndex: number,
  t: number,
): { value: number | null; at: number | null } {
  const offset = stream.time_offset_s ?? 0;
  let value: number | null = null;
  let at: number | null = null;
  for (const point of stream.points) {
    const time = point.t_s + offset;
    if (time > t) break;
    value = point.positions[jointIndex];
    at = time;
  }
  if (value === null && stream.points.length > 0) {
    const first = stream.points[0];
    value = first.positions[jointIndex];
    at = first.t_s + offset;
  }
  return { value, at };
}

function interpolate(
  times: number[],
  values: number[],
  t: number,
): number {
  if (t <= times[0]) return values[0];
  if (t >= times[times.length - 1]) return values[values.length - 1];
  let low = 0;
  let high = times.length - 1;
  while (high - low > 1) {
    const middle = Math.floor((low + high) / 2);
    if (times[middle] <= t) low = middle;
    else high = middle;
  }
  const span = times[high] - times[low];
  if (span === 0) return values[high];
  const ratio = (t - times[low]) / span;
  return values[low] + ratio * (values[high] - values[low]);
}

function downsample(values: Array<[number, number]>, max = 900): Array<[number, number]> {
  if (values.length <= max) return values;
  const stride = Math.ceil(values.length / max);
  const result = values.filter((_, index) => index % stride === 0);
  const last = values[values.length - 1];
  if (result[result.length - 1] !== last) result.push(last);
  return result;
}

function formatError(caught: unknown): string {
  if (caught instanceof ApiError) {
    const issues = caught.details.issues;
    if (Array.isArray(issues)) {
      const text = issues
        .map((issue) => {
          if (issue && typeof issue === "object") {
            const item = issue as { path?: unknown; message?: unknown };
            return `${String(item.path ?? "?")}: ${String(item.message ?? "invalid")}`;
          }
          return String(issue);
        })
        .join("; ");
      return `${caught.code}: ${caught.message} — ${text}`;
    }
    return `${caught.code}: ${caught.message}`;
  }
  return caught instanceof Error ? caught.message : String(caught);
}

function TrajectoryChart({
  series,
  cursor,
  height = 230,
  unitLabel,
  baseline,
}: {
  series: ChartSeries[];
  cursor: number;
  height?: number;
  unitLabel: string;
  baseline?: number;
}) {
  const drawable = series.filter((item) => item.values.length > 0);
  const all = drawable.flatMap((item) => item.values);
  if (all.length === 0) {
    return <EmptyState title="没有可绘制的数据">该数据集缺少所选关节的样本。</EmptyState>;
  }
  const width = 840;
  const pad = { left: 58, right: 14, top: 14, bottom: 30 };
  const innerWidth = width - pad.left - pad.right;
  const innerHeight = height - pad.top - pad.bottom;
  let xMin = Number.POSITIVE_INFINITY;
  let xMax = Number.NEGATIVE_INFINITY;
  let yMin = Number.POSITIVE_INFINITY;
  let yMax = Number.NEGATIVE_INFINITY;
  for (const [t, value] of all) {
    if (t < xMin) xMin = t;
    if (t > xMax) xMax = t;
    if (value < yMin) yMin = value;
    if (value > yMax) yMax = value;
  }
  if (xMax === xMin) xMax = xMin + 1;
  if (yMax === yMin) {
    yMin -= 0.1;
    yMax += 0.1;
  }
  const yPad = (yMax - yMin) * 0.12;
  yMin -= yPad;
  yMax += yPad;
  const sx = (t: number) => pad.left + ((t - xMin) / (xMax - xMin)) * innerWidth;
  const sy = (value: number) => pad.top + (1 - (value - yMin) / (yMax - yMin)) * innerHeight;
  const xTicks = Array.from({ length: 5 }, (_, index) => xMin + ((xMax - xMin) * index) / 4);
  const yTicks = Array.from({ length: 5 }, (_, index) => yMin + ((yMax - yMin) * index) / 4);
  const cursorX = cursor >= xMin && cursor <= xMax ? sx(cursor) : null;

  return (
    <svg
      className="trajectory-chart"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="轨迹曲线图"
    >
      {yTicks.map((value) => (
        <g key={`y-${value}`}>
          <line
            x1={pad.left}
            x2={width - pad.right}
            y1={sy(value)}
            y2={sy(value)}
            className="chart-grid"
          />
          <text x={pad.left - 8} y={sy(value) + 4} textAnchor="end" className="chart-label">
            {value.toFixed(3)}
          </text>
        </g>
      ))}
      {xTicks.map((value) => (
        <g key={`x-${value}`}>
          <line
            x1={sx(value)}
            x2={sx(value)}
            y1={pad.top}
            y2={height - pad.bottom}
            className="chart-grid"
          />
          <text x={sx(value)} y={height - 10} textAnchor="middle" className="chart-label">
            {value.toFixed(2)} s
          </text>
        </g>
      ))}
      {drawable.map((item) => {
        const points = downsample(item.values)
          .map(([t, value], index) => `${index === 0 ? "M" : "L"}${sx(t).toFixed(2)},${sy(value).toFixed(2)}`)
          .join(" ");
        return (
          <path
            key={item.label}
            d={points}
            fill="none"
            stroke={item.color}
            strokeWidth={1.8}
            strokeDasharray={item.dashed ? "5 4" : undefined}
          />
        );
      })}
      {baseline !== undefined && baseline >= yMin && baseline <= yMax && (
        <line
          x1={pad.left}
          x2={width - pad.right}
          y1={sy(baseline)}
          y2={sy(baseline)}
          className="chart-baseline"
        />
      )}
      {cursorX !== null && (
        <line
          x1={cursorX}
          x2={cursorX}
          y1={pad.top}
          y2={height - pad.bottom}
          className="chart-cursor"
        />
      )}
      <text x={pad.left} y={10} className="chart-axis-label">
        {unitLabel}
      </text>
    </svg>
  );
}

function qualityRow(label: string, quality: StreamQuality) {
  const rate = quality.effective_rate_hz;
  return (
    <tr key={label}>
      <td>
        <strong>{streamLabel(label)}</strong>
        <div>
          <TruthBadge truth={quality.truth} />
        </div>
      </td>
      <td className="mono">{quality.samples}</td>
      <td className="mono">{rate === null ? "—" : `${formatNumber(rate, rate >= 10 ? 1 : 2)} Hz`}</td>
      <td className="mono" title={`${quality.gaps_over_1_5x_median} 个间隔 > 1.5× 中位数`}>
        {quality.max_gap_s === null ? "—" : `${formatNumber(quality.max_gap_s, 2)} s`}
      </td>
      <td className="mono">
        {quality.dropout_ratio === null ? "—" : `${formatNumber(quality.dropout_ratio * 100, 1)} %`}
      </td>
    </tr>
  );
}

export function TrajectoryInspector() {
  const preferences = usePreferences();
  const setReplayActive = useRobotStore((store) => store.setReplayActive);
  const [items, setItems] = useState<TrajectorySummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [record, setRecord] = useState<TrajectoryRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [joint, setJoint] = useState("");
  const [playing, setPlaying] = useState(false);
  const [cursor, setCursor] = useState(0);
  const [show, setShow] = useState({
    planned: true,
    commanded: true,
    feedback: true,
    error: true,
  });
  const fileInput = useRef<HTMLInputElement | null>(null);

  const refreshList = useCallback(async (prefer?: string) => {
    const list = await api.trajectories();
    setItems(list.items);
    setSelectedId((current) => prefer ?? current ?? list.items[0]?.trajectory_id ?? null);
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        const list = await api.trajectories();
        setItems(list.items);
        setSelectedId(list.items[0]?.trajectory_id ?? null);
      } catch (caught) {
        setError(formatError(caught));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setRecord(null);
      return;
    }
    let cancelled = false;
    setBusy(true);
    setError(null);
    setPlaying(false);
    void api
      .trajectory(selectedId)
      .then((loaded) => {
        if (cancelled) return;
        setRecord(loaded);
        setJoint(loaded.analysis.joint_names[0] ?? "");
        setCursor(datasetTimes(loaded.dataset).min);
      })
      .catch((caught) => {
        if (!cancelled) setError(formatError(caught));
      })
      .finally(() => {
        if (!cancelled) setBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  useEffect(() => {
    setReplayActive(playing);
  }, [playing, setReplayActive]);

  useEffect(() => () => setReplayActive(false), [setReplayActive]);

  const range = useMemo(
    () => (record ? datasetTimes(record.dataset) : { min: 0, max: 0 }),
    [record],
  );
  const span = Math.max(range.max - range.min, 1e-6);

  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => {
      setCursor((current) => Math.min(range.max, current + 0.2));
    }, 100);
    return () => window.clearInterval(timer);
  }, [playing, range.max]);

  useEffect(() => {
    if (playing && cursor >= range.max) setPlaying(false);
  }, [playing, cursor, range.max]);

  const jointIndex = record ? record.analysis.joint_names.indexOf(joint) : -1;
  const scale = preferences.unit === "deg" ? 180 / Math.PI : 1;

  const positionSeries = useMemo<ChartSeries[]>(() => {
    if (!record || jointIndex < 0) return [];
    const dataset = record.dataset;
    const streams: Array<[SampleStream, string, boolean]> = [
      ["planned", "Planned", false],
      ["commanded", "Commanded", true],
      ["feedback", "Feedback", false],
    ];
    return streams
      .filter(([key]) => show[key])
      .map(([key, label, dashed]) => ({
        label,
        color: COLORS[key],
        dashed,
        values: streamTimes(dataset[key], jointIndex).map(
          ([t, value]) => [t, value * scale] as [number, number],
        ),
      }));
  }, [record, jointIndex, show, scale]);

  const errorSeries = useMemo<ChartSeries[]>(() => {
    if (!record || jointIndex < 0 || !show.error || !record.analysis.tracking.available) {
      return [];
    }
    const dataset = record.dataset;
    const planned = streamTimes(dataset.planned, jointIndex);
    const times = planned.map(([t]) => t);
    const values = planned.map(([, value]) => value);
    const feedback = streamTimes(dataset.feedback, jointIndex);
    const errors: Array<[number, number]> = feedback
      .filter(([t]) => t >= times[0] && t <= times[times.length - 1])
      .map(([t, value]) => [t, (value - interpolate(times, values, t)) * scale]);
    return [{ label: "Feedback - planned", color: COLORS.error, values: errors }];
  }, [record, jointIndex, show.error, scale]);

  const handleImport = async (file: File) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      let parsed: unknown;
      try {
        parsed = JSON.parse(await file.text());
      } catch {
        throw new Error("文件不是合法 JSON");
      }
      const imported = await api.importTrajectory(parsed);
      setNotice(
        imported.duplicate
          ? `数据集已存在（${imported.dataset_sha256.slice(0, 12)}…），已选中原记录；没有重复写入。`
          : `已导入「${imported.name}」；数据仅保存在本机 SQLite，不会发送运动命令。`,
      );
      await refreshList(imported.trajectory_id);
    } catch (caught) {
      setError(formatError(caught));
    } finally {
      setBusy(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const handleDemo = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const imported = await api.demoTrajectory();
      setNotice(
        imported.duplicate
          ? "示例数据集已存在，已选中原记录。"
          : "已加载模拟示例：计划 / 指令 / 反馈三流分离，反馈明确标记为模拟。",
      );
      await refreshList(imported.trajectory_id);
    } catch (caught) {
      setError(formatError(caught));
    } finally {
      setBusy(false);
    }
  };

  const handleRecompute = async () => {
    if (!record) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.recomputeTrajectory(record.trajectory_id);
      setNotice(
        result.matches_stored
          ? `复算一致（${result.analysis_version}）。`
          : `复算结果与存储分析不一致：stored=${result.stored_analysis_version} fresh=${result.analysis_version}。`,
      );
    } catch (caught) {
      setError(formatError(caught));
    } finally {
      setBusy(false);
    }
  };

  const tracking = record?.analysis.tracking;
  const fidelity = record?.analysis.command_fidelity;
  const selectedJointMetrics = tracking?.per_joint[joint];
  const safeIndex = jointIndex >= 0 ? jointIndex : 0;
  const values = record
    ? {
        planned: holdValue(record.dataset.planned, safeIndex, cursor),
        commanded: holdValue(record.dataset.commanded, safeIndex, cursor),
        feedback: holdValue(record.dataset.feedback, safeIndex, cursor),
      }
    : null;

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1 className="page-title">轨迹检查</h1>
          <p className="page-subtitle">
            G2 离线轨迹检查：计划 / 指令 / 反馈三流分离，指标可复算。回放只是数据播放，绝不向硬件发命令。
          </p>
        </div>
        <div className="page-actions">
          <label className="button button-secondary">
            导入数据集 JSON
            <input
              ref={fileInput}
              type="file"
              accept="application/json,.json"
              hidden
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void handleImport(file);
              }}
            />
          </label>
          <Button variant="primary" onClick={() => void handleDemo()} disabled={busy}>
            加载示例数据
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
          <strong>离线数据</strong>
          <span>{notice}</span>
        </div>
      )}
      {playing && (
        <div className="banner banner-replay">
          <strong>回放</strong>
          <span>
            离线回放进行中：只播放已记录数据，不会向硬件发送任何命令。顶部来源显示“回放”。
          </span>
        </div>
      )}

      <div className="trajectory-layout">
        <div className="trajectory-main">
          <Card
            title="数据集"
            subtitle={`${items.length} 个数据集的摘要；数据来自本机 SQLite`}
            actions={
              <Button variant="ghost" onClick={() => void refreshList()} disabled={busy}>
                刷新列表
              </Button>
            }
          >
            {loading ? (
              <p className="muted">加载中…</p>
            ) : items.length === 0 ? (
              <EmptyState title="还没有离线数据集">
                导入一个 JSON 数据集，或点击“加载示例数据”查看 G2 分析流程。示例是模拟数据。
              </EmptyState>
            ) : (
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>名称</th>
                      <th>来源</th>
                      <th>反馈真值</th>
                      <th>样本数（计划/指令/反馈）</th>
                      <th>时长</th>
                      <th>跟踪分析</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => (
                      <tr
                        key={item.trajectory_id}
                        className={`row-clickable${selectedId === item.trajectory_id ? " row-selected" : ""}`}
                        onClick={() => setSelectedId(item.trajectory_id)}
                      >
                        <td>
                          <strong>{item.name}</strong>
                          <div className="mono ellipsis">{item.trajectory_id}</div>
                        </td>
                        <td>{originLabel(item.source)}</td>
                        <td>
                          <TruthBadge truth={item.feedback_truth} />
                        </td>
                        <td className="mono">
                          {item.planned_samples}/{item.commanded_samples}/{item.feedback_samples}
                        </td>
                        <td className="mono">{formatNumber(item.duration_s, 2)} s</td>
                        <td>
                          <StatusChip
                            tone={item.tracking_available ? "ok" : "warning"}
                            label={item.tracking_available ? "可用" : "不可用"}
                            detail={item.tracking_reason ?? undefined}
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card
            title="关节曲线"
            subtitle={
              record
                ? `${record.name} · ${joint || "—"} · ${preferences.unit}`
                : "选择一个数据集"
            }
            actions={
              <div className="series-toggles">
                {(["planned", "commanded", "feedback"] as SampleStream[]).map((key) => (
                  <label key={key} className="series-toggle">
                    <input
                      type="checkbox"
                      checked={show[key]}
                      onChange={(event) =>
                        setShow((current) => ({ ...current, [key]: event.target.checked }))
                      }
                    />
                    <span className="series-dot" style={{ background: COLORS[key] }} />
                    {streamLabel(key)}
                  </label>
                ))}
              </div>
            }
          >
            {record ? (
              <>
                <div className="trajectory-controls">
                  <label className="field">
                    <span>关节</span>
                    <select
                      className="input"
                      value={joint}
                      onChange={(event) => setJoint(event.target.value)}
                    >
                      {record.analysis.joint_names.map((name) => (
                        <option key={name} value={name}>
                          {name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <div className="chart-legend">
                    {positionSeries.map((item) => (
                      <span key={item.label} className="legend-item">
                        <span
                          className="legend-swatch"
                          style={{
                            background: item.dashed ? "transparent" : item.color,
                            borderColor: item.color,
                          }}
                        />
                        {streamLabel(item.label.toLowerCase())}
                      </span>
                    ))}
                  </div>
                </div>
                <TrajectoryChart
                  series={positionSeries}
                  cursor={cursor}
                  unitLabel={`位置（${preferences.unit}）`}
                />
                {tracking?.available ? (
                  <div className="trajectory-error-block">
                    <div className="chart-legend">
                      <span className="legend-item">
                        <span className="legend-swatch" style={{ background: COLORS.error }} />
                        跟踪误差
                        <TruthBadge truth={tracking.sample_truth} />
                      </span>
                    </div>
                    <TrajectoryChart
                      series={errorSeries}
                      cursor={cursor}
                      height={170}
                      unitLabel={`误差（${preferences.unit}）`}
                      baseline={0}
                    />
                  </div>
                ) : (
                  <div className="banner banner-warning">
                    <strong>跟踪误差不可用</strong>
                    <span>{tracking?.reason ?? "缺少反馈流"}</span>
                  </div>
                )}
              </>
            ) : (
              <EmptyState title="未选择数据集">
                选择一个已导入的数据集后显示逐关节曲线和误差。
              </EmptyState>
            )}
          </Card>

          <Card
            title="回放"
            subtitle="客户端播放；不调用运动接口，不写事件，不访问 ROS"
            actions={
              <div className="button-row">
                <Button variant="ghost" onClick={() => setCursor(range.min)} disabled={!record || playing}>
                  重置
                </Button>
                <Button
                  variant={playing ? "secondary" : "primary"}
                  onClick={() => {
                    if (playing) {
                      setPlaying(false);
                      return;
                    }
                    if (cursor >= range.max) setCursor(range.min);
                    setPlaying(true);
                  }}
                  disabled={!record || range.max <= range.min}
                >
                  {playing ? "暂停回放" : "播放回放"}
                </Button>
              </div>
            }
          >
            {record ? (
              <>
                <div className="replay-readout">
                  <MetricValue
                    label="游标"
                    value={formatNumber(cursor, 3)}
                    unit="s"
                    hint={`范围 ${formatNumber(range.min, 2)}–${formatNumber(range.max, 2)} s`}
                  />
                  {(["planned", "commanded", "feedback"] as SampleStream[]).map((key) => {
                    const held = values?.[key].value;
                    return (
                      <div key={key} className="replay-value">
                        <span className="replay-value-label">
                          <span className="series-dot" style={{ background: COLORS[key] }} />
                          {streamLabel(key)}
                        </span>
                        <span className="mono">
                          {formatNumber(held === null || held === undefined ? null : held * scale, preferences.decimals)}{" "}
                          {preferences.unit}
                        </span>
                      </div>
                    );
                  })}
                </div>
                <input
                  className="range"
                  type="range"
                  min={0}
                  max={1}
                  step={0.0005}
                  value={(cursor - range.min) / span}
                  aria-label="回放游标"
                  onChange={(event) => {
                    setPlaying(false);
                    setCursor(range.min + Number(event.target.value) * span);
                  }}
                />
                <p className="hint">
                  读数使用样本保持（最后一个不晚于游标的样本）。回放速度 2×；暂停或离开页面即停止。
                </p>
              </>
            ) : (
              <EmptyState title="没有可回放的数据">导入或加载示例数据集后可使用时间轴回放。</EmptyState>
            )}
          </Card>
        </div>

        <aside className="trajectory-side">
          <Card title="跟踪指标" subtitle="计划与反馈的逐关节误差">
            {record && tracking?.available && selectedJointMetrics ? (
              <>
                <div className="metric-row">
                  <MetricValue
                    label="最大 |误差|"
                    value={formatNumber(selectedJointMetrics.max_abs_error_rad * scale, preferences.decimals)}
                    unit={preferences.unit}
                    tone="warning"
                  />
                  <MetricValue
                    label="RMSE"
                    value={formatNumber(selectedJointMetrics.rmse_rad * scale, preferences.decimals)}
                    unit={preferences.unit}
                  />
                  <MetricValue
                    label="终点误差"
                    value={formatNumber(selectedJointMetrics.final_error_rad * scale, preferences.decimals)}
                    unit={preferences.unit}
                  />
                </div>
                <div className="table-wrap">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>关节</th>
                        <th>最大</th>
                        <th>RMSE</th>
                        <th>终点</th>
                      </tr>
                    </thead>
                    <tbody>
                      {record.analysis.joint_names.map((name) => {
                        const metrics = tracking.per_joint[name];
                        if (!metrics) return null;
                        return (
                          <tr key={name} className={name === joint ? "row-selected" : ""}>
                            <td>{name}</td>
                            <td className="mono">{formatNumber(metrics.max_abs_error_rad * scale, 4)}</td>
                            <td className="mono">{formatNumber(metrics.rmse_rad * scale, 4)}</td>
                            <td className="mono">{formatNumber(metrics.final_error_rad * scale, 4)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <p className="hint">
                  {tracking.samples_compared} 个对齐样本 · 单位 {preferences.unit} · 真值=
                  {truthLabel(tracking.sample_truth)}
                </p>
              </>
            ) : record ? (
              <div className="banner banner-warning">
                <strong>不可用</strong>
                <span>{tracking?.reason ?? "缺少反馈流"}</span>
              </div>
            ) : (
              <EmptyState title="未选择数据集">指标来自后端分析，CSV 可独立复算。</EmptyState>
            )}
          </Card>

          <Card title="指令保真度" subtitle="计划与指令的差异">
            {fidelity?.available && fidelity.aggregate ? (
              <div className="metric-row">
                <MetricValue
                  label="最大 |误差|"
                  value={formatNumber(fidelity.aggregate.max_abs_error_rad * scale, preferences.decimals)}
                  unit={preferences.unit}
                />
                <MetricValue
                  label="RMSE"
                  value={formatNumber(fidelity.aggregate.rmse_rad * scale, preferences.decimals)}
                  unit={preferences.unit}
                />
                <MetricValue
                  label="最差终点"
                  value={formatNumber(fidelity.aggregate.max_abs_final_error_rad * scale, preferences.decimals)}
                  unit={preferences.unit}
                />
              </div>
            ) : (
              <p className="muted">{fidelity?.reason ?? "缺少指令流"}</p>
            )}
          </Card>

          <Card title="采样质量" subtitle="采样率、间隔与丢样（各流独立）">
            {record ? (
              <div className="table-wrap">
                <table className="data-table quality-table">
                  <thead>
                    <tr>
                      <th>数据流</th>
                      <th>样本数</th>
                      <th>采样率</th>
                      <th>最大间隔</th>
                      <th>丢样率</th>
                    </tr>
                  </thead>
                  <tbody>
                    {qualityRow("planned", record.analysis.sampling_quality.planned)}
                    {qualityRow("commanded", record.analysis.sampling_quality.commanded)}
                    {qualityRow("feedback", record.analysis.sampling_quality.feedback)}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="muted">未选择数据集</p>
            )}
          </Card>

          <Card title="数据集" subtitle="来源、时间基准与可追溯哈希">
            {record ? (
              <div className="kv-list">
                <KeyValue label="ID">{record.trajectory_id}</KeyValue>
                <KeyValue label="创建时间">{record.created_at}</KeyValue>
                <KeyValue label="来源">{originLabel(record.source)}</KeyValue>
                <KeyValue label="分析版本">{record.analysis_version}</KeyValue>
                <KeyValue label="SHA-256">
                  <span className="mono" title={record.dataset_sha256}>
                    {record.dataset_sha256.slice(0, 16)}…{record.dataset_sha256.slice(-8)}
                  </span>
                </KeyValue>
                <KeyValue label="时间基准">{record.dataset.timebase}</KeyValue>
                <KeyValue label="执行器">
                  {record.dataset.executor
                    ? `${stageLabel(record.dataset.executor.status)} (${truthLabel(record.dataset.executor.truth)})`
                    : "—"}
                </KeyValue>
              </div>
            ) : (
              <p className="muted">未选择数据集</p>
            )}
          </Card>

          <Card title="导出" subtitle="CSV 含对齐样本与误差，可独立复算">
            {record ? (
              <div className="button-row export-row">
                <a
                  className="button button-secondary"
                  href={api.trajectoryExportUrl(record.trajectory_id, "csv", "feedback")}
                  download
                >
                  反馈 CSV
                </a>
                <a
                  className="button button-secondary"
                  href={api.trajectoryExportUrl(record.trajectory_id, "csv", "commanded")}
                  download
                >
                  指令 CSV
                </a>
                <a
                  className="button button-secondary"
                  href={api.trajectoryExportUrl(record.trajectory_id, "json")}
                  download
                >
                  数据集 JSON
                </a>
                <Button variant="ghost" onClick={() => void handleRecompute()} disabled={busy}>
                  复算指标
                </Button>
              </div>
            ) : (
              <p className="muted">未选择数据集</p>
            )}
          </Card>

          <Card title="说明" subtitle="分析假设与限制">
            {record ? (
              <>
                <ul className="note-list">
                  {record.analysis.notes.map((note) => (
                    <li key={note}>{note}</li>
                  ))}
                  <li>
                    CSV 导出使用与后端相同的线性插值，并按所选流的时间戳对齐；不是硬件同步证据。
                  </li>
                </ul>
              </>
            ) : (
              <p className="muted">未选择数据集</p>
            )}
          </Card>
        </aside>
      </div>
    </div>
  );
}
