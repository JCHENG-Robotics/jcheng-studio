import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api/client";
import type {
  ExperimentDetail,
  ExperimentSummary,
  PlanEntry,
  TrajectorySummary,
  TrialRecord,
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
import { useRobotStore } from "../store/robotStore";
import {
  experimentStatusLabel,
  failureCodeLabel,
  measurementSourceLabel,
  methodLabel,
} from "../labels";

type Method = "baseline" | "intervention";

interface CreateFormState {
  name: string;
  purpose: string;
  data_origin: "hardware" | "simulated";
  operator: string;
  planned_pairs: string;
  seed: string;
  fixtures: string;
  hardware_config: string;
  calibration_version: string;
  mapping_version: string;
  stop_rules: string;
  notes: string;
}

interface TrialFormState {
  pair_id: string;
  method: Method;
  trial_order: number;
  fixture: string;
  grasp_success: boolean;
  error: string;
  rotation: string;
  duration: string;
  failure_code: string;
  failure_detail: string;
  measurement_source: string;
  trajectory_id: string;
  operator_notes: string;
}

const MEASUREMENT_SOURCES = [
  "unknown",
  "vision",
  "caliper",
  "fixture",
  "operator",
  "simulated",
];

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

function fmt(value: number | null | undefined, decimals = 3): string {
  return value === null || value === undefined ? "—" : value.toFixed(decimals);
}

function fmtPct(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${(value * 100).toFixed(1)}%`;
}

function fmtP(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value < 0.001) return value.toExponential(2);
  return value.toFixed(4);
}

function emptyCreateForm(): CreateFormState {
  return {
    name: "",
    purpose: "",
    data_origin: "hardware",
    operator: "",
    planned_pairs: "0",
    seed: "7",
    fixtures: "",
    hardware_config: "",
    calibration_version: "",
    mapping_version: "",
    stop_rules: "",
    notes: "",
  };
}

function trialFormFromEntry(
  entry: PlanEntry | null,
  dataOrigin: "hardware" | "simulated" = "hardware",
): TrialFormState {
  return {
    pair_id: entry?.pair_id ?? "",
    method: entry?.method ?? "baseline",
    trial_order: entry?.trial_order ?? 1,
    fixture: entry?.fixture ?? "",
    grasp_success: true,
    error: "",
    rotation: "",
    duration: "",
    failure_code: "GRASP_FAILED",
    failure_detail: "",
    measurement_source: dataOrigin === "simulated" ? "simulated" : "unknown",
    trajectory_id: "",
    operator_notes: "",
  };
}

function nextPlanEntry(detail: ExperimentDetail | null): PlanEntry | null {
  if (!detail) return null;
  const recorded = new Set(detail.trials.map((trial) => `${trial.pair_id}|${trial.method}`));
  return (
    detail.experiment.plan.entries.find(
      (entry) => !recorded.has(`${entry.pair_id}|${entry.method}`),
    ) ?? null
  );
}

export function ExperimentLab() {
  const diagnostics = useRobotStore((store) => store.diagnostics);
  const [items, setItems] = useState<ExperimentSummary[]>([]);
  const [failureCodes, setFailureCodes] = useState<Record<string, string>>({});
  const [trajectories, setTrajectories] = useState<TrajectorySummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ExperimentDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState<CreateFormState>(emptyCreateForm);
  const [trialForm, setTrialForm] = useState<TrialFormState>(() => trialFormFromEntry(null));

  const refreshList = useCallback(async (prefer?: string) => {
    const list = await api.experiments();
    setItems(list.items);
    setFailureCodes(list.failure_codes);
    setSelectedId((current) => prefer ?? current ?? list.items[0]?.experiment_id ?? null);
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        const [list, trajectoryList] = await Promise.all([api.experiments(), api.trajectories()]);
        setItems(list.items);
        setFailureCodes(list.failure_codes);
        setTrajectories(trajectoryList.items);
        setSelectedId(list.items[0]?.experiment_id ?? null);
      } catch (caught) {
        setError(formatError(caught));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setBusy(true);
    setError(null);
    void api
      .experiment(selectedId)
      .then((loaded) => {
        if (cancelled) return;
        setDetail(loaded);
        setTrialForm(trialFormFromEntry(nextPlanEntry(loaded), loaded.experiment.data_origin));
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

  const experiment = detail?.experiment ?? null;
  const analysis = detail?.analysis ?? null;
  const planComplete = Boolean(
    experiment &&
      experiment.plan.entries.length > 0 &&
      nextPlanEntry(detail) === null,
  );
  const recordedKeys = useMemo(
    () => new Set((detail?.trials ?? []).map((trial) => `${trial.pair_id}|${trial.method}`)),
    [detail],
  );
  const availablePairs = useMemo(() => {
    if (!experiment) return [];
    return Array.from(
      new Set(
        experiment.plan.entries
          .filter((entry) => !recordedKeys.has(`${entry.pair_id}|${entry.method}`))
          .map((entry) => entry.pair_id),
      ),
    );
  }, [experiment, recordedKeys]);

  const handleDemo = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const loaded = await api.demoExperiment();
      setDetail(loaded);
      setTrialForm(trialFormFromEntry(nextPlanEntry(loaded), loaded.experiment.data_origin));
      setNotice(
        loaded.duplicate
          ? "示例实验已存在，已选中原记录。"
          : "已加载模拟配对实验（16 对 / 32 个试验）；全部标记为模拟。",
      );
      await refreshList(loaded.experiment.experiment_id);
    } catch (caught) {
      setError(formatError(caught));
    } finally {
      setBusy(false);
    }
  };

  const handleCreate = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const payload = {
        name: createForm.name,
        purpose: createForm.purpose,
        data_origin: createForm.data_origin,
        planned_pairs: Number(createForm.planned_pairs || "0"),
        seed: Number(createForm.seed || "7"),
        fixtures: createForm.fixtures
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
        hardware_config: createForm.hardware_config,
        calibration_version: createForm.calibration_version,
        mapping_version: createForm.mapping_version,
        stop_rules: createForm.stop_rules,
        notes: createForm.notes,
        operator: createForm.operator ?? "",
      };
      const created = await api.createExperiment(payload);
      setDetail(created);
      setTrialForm(trialFormFromEntry(nextPlanEntry(created), created.experiment.data_origin));
      setShowCreate(false);
      setCreateForm(emptyCreateForm());
      setNotice(`实验已创建：${created.experiment.name}`);
      await refreshList(created.experiment.experiment_id);
    } catch (caught) {
      setError(formatError(caught));
    } finally {
      setBusy(false);
    }
  };

  const applyEntry = (entry: PlanEntry | null) => {
    if (!entry) return;
    setTrialForm((current) => ({
      ...current,
      pair_id: entry.pair_id,
      method: entry.method,
      trial_order: entry.trial_order,
      fixture: entry.fixture,
    }));
  };

  const handlePairChange = (pairId: string) => {
    const entry =
      experiment?.plan.entries.find(
        (item) => item.pair_id === pairId && !recordedKeys.has(`${item.pair_id}|${item.method}`),
      ) ?? experiment?.plan.entries.find((item) => item.pair_id === pairId);
    setTrialForm((current) => ({
      ...current,
      pair_id: pairId,
      method: entry?.method ?? current.method,
      trial_order: entry?.trial_order ?? current.trial_order,
      fixture: entry?.fixture ?? current.fixture,
    }));
  };

  const handleMethodChange = (method: Method) => {
    const entry = experiment?.plan.entries.find(
      (item) => item.pair_id === trialForm.pair_id && item.method === method,
    );
    setTrialForm((current) => ({
      ...current,
      method,
      trial_order: entry?.trial_order ?? current.trial_order,
      fixture: entry?.fixture ?? current.fixture,
    }));
  };

  const handleRecordTrial = async () => {
    if (!experiment) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const payload = {
        pair_id: trialForm.pair_id,
        trial_order: trialForm.trial_order,
        method: trialForm.method,
        fixture: trialForm.fixture,
        grasp_success: trialForm.grasp_success,
        continuous_error_mm:
          trialForm.error.trim() === "" ? null : Number(trialForm.error),
        rotation_error_deg:
          trialForm.rotation.trim() === "" ? null : Number(trialForm.rotation),
        failure_code: trialForm.grasp_success ? null : trialForm.failure_code,
        failure_detail: trialForm.grasp_success ? "" : trialForm.failure_detail,
        duration_s:
          trialForm.duration.trim() === "" ? null : Number(trialForm.duration),
        measurement_source: trialForm.measurement_source,
        trajectory_id: trialForm.trajectory_id || null,
        operator_notes: trialForm.operator_notes,
      };
      await api.recordTrial(experiment.experiment_id, payload);
      const loaded = await api.experiment(experiment.experiment_id);
      setDetail(loaded);
      setTrialForm(trialFormFromEntry(nextPlanEntry(loaded), loaded.experiment.data_origin));
      setNotice(`已记录试验：${trialForm.pair_id} / ${trialForm.method}`);
      await refreshList(experiment.experiment_id);
    } catch (caught) {
      setError(formatError(caught));
    } finally {
      setBusy(false);
    }
  };

  const fillNext = () => {
    const entry = nextPlanEntry(detail);
    if (entry) applyEntry(entry);
  };

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1 className="page-title">实验台</h1>
          <p className="page-subtitle">
            G3 实验定义与配对试验记录：基线 / 干预、失败代码、连续误差与可复算的配对分析。
          </p>
        </div>
        <div className="page-actions">
          <Button variant="secondary" onClick={() => setShowCreate((current) => !current)}>
            {showCreate ? "取消新建" : "新建实验"}
          </Button>
          <Button variant="primary" onClick={() => void handleDemo()} disabled={busy}>
            加载示例实验
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
          <strong>实验记录</strong>
          <span>{notice}</span>
        </div>
      )}

      {showCreate && (
        <Card title="新建实验" subtitle="实验定义创建后不可修改；试验只能追加">
          <div className="experiment-form">
            <label className="field">
              <span>名称 *</span>
              <input
                className="input"
                value={createForm.name}
                onChange={(event) =>
                  setCreateForm((current) => ({ ...current, name: event.target.value }))
                }
              />
            </label>
            <label className="field">
              <span>数据来源</span>
              <select
                className="input"
                value={createForm.data_origin}
                onChange={(event) =>
                  setCreateForm((current) => ({
                    ...current,
                    data_origin: event.target.value as "hardware" | "simulated",
                  }))
                }
              >
                <option value="hardware">硬件</option>
                <option value="simulated">模拟</option>
              </select>
            </label>
            <label className="field">
              <span>操作员</span>
              <input
                className="input"
                value={createForm.operator}
                onChange={(event) =>
                  setCreateForm((current) => ({ ...current, operator: event.target.value }))
                }
              />
            </label>
            <label className="field experiment-form-wide">
              <span>目的 *</span>
              <textarea
                className="input"
                rows={2}
                value={createForm.purpose}
                onChange={(event) =>
                  setCreateForm((current) => ({ ...current, purpose: event.target.value }))
                }
              />
            </label>
            <label className="field">
              <span>计划配对数量</span>
              <input
                className="input"
                type="number"
                min={0}
                max={200}
                value={createForm.planned_pairs}
                onChange={(event) =>
                  setCreateForm((current) => ({ ...current, planned_pairs: event.target.value }))
                }
              />
            </label>
            <label className="field">
              <span>随机化种子</span>
              <input
                className="input"
                type="number"
                value={createForm.seed}
                onChange={(event) =>
                  setCreateForm((current) => ({ ...current, seed: event.target.value }))
                }
              />
            </label>
            <label className="field">
              <span>夹具（逗号分隔）</span>
              <input
                className="input"
                placeholder="fixture_A 或 fixture_A, fixture_B"
                value={createForm.fixtures}
                onChange={(event) =>
                  setCreateForm((current) => ({ ...current, fixtures: event.target.value }))
                }
              />
            </label>
            <label className="field">
              <span>硬件配置</span>
              <input
                className="input"
                value={createForm.hardware_config}
                onChange={(event) =>
                  setCreateForm((current) => ({
                    ...current,
                    hardware_config: event.target.value,
                  }))
                }
              />
            </label>
            <label className="field">
              <span>标定版本</span>
              <input
                className="input"
                value={createForm.calibration_version}
                onChange={(event) =>
                  setCreateForm((current) => ({
                    ...current,
                    calibration_version: event.target.value,
                  }))
                }
              />
            </label>
            <label className="field">
              <span>映射版本</span>
              <input
                className="input"
                value={createForm.mapping_version}
                onChange={(event) =>
                  setCreateForm((current) => ({
                    ...current,
                    mapping_version: event.target.value,
                  }))
                }
              />
            </label>
            <label className="field experiment-form-wide">
              <span>停止规则</span>
              <textarea
                className="input"
                rows={2}
                value={createForm.stop_rules}
                onChange={(event) =>
                  setCreateForm((current) => ({ ...current, stop_rules: event.target.value }))
                }
              />
            </label>
            <label className="field experiment-form-wide">
              <span>备注</span>
              <textarea
                className="input"
                rows={2}
                value={createForm.notes}
                onChange={(event) =>
                  setCreateForm((current) => ({ ...current, notes: event.target.value }))
                }
              />
            </label>
          </div>
          <div className="button-row form-actions">
            <Button variant="ghost" onClick={() => setShowCreate(false)}>
              取消
            </Button>
            <Button
              variant="primary"
              onClick={() => void handleCreate()}
              disabled={busy || !createForm.name.trim() || !createForm.purpose.trim()}
            >
              创建实验
            </Button>
          </div>
        </Card>
      )}

      <Card
        title="实验列表"
        subtitle={`${items.length} 个实验定义；试验追加写入本机 SQLite`}
        actions={
          <Button variant="ghost" onClick={() => void refreshList()} disabled={busy}>
            刷新列表
          </Button>
        }
      >
        {loading ? (
          <p className="muted">加载中…</p>
        ) : items.length === 0 ? (
          <EmptyState title="还没有实验定义">
            新建一个实验，或加载模拟示例查看 G3 记录与分析流程。
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>名称</th>
                  <th>来源</th>
                  <th>状态</th>
                  <th>试验数</th>
                  <th>完整配对</th>
                  <th>基线 / 干预成功率</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr
                    key={item.experiment_id}
                    className={`row-clickable${selectedId === item.experiment_id ? " row-selected" : ""}`}
                    onClick={() => setSelectedId(item.experiment_id)}
                  >
                    <td>
                      <strong>{item.name}</strong>
                      <div className="mono ellipsis">{item.experiment_id}</div>
                    </td>
                    <td>
                      <TruthBadge truth={item.truth} />
                    </td>
                    <td>
                      <StatusChip
                        tone={
                          item.status === "complete"
                            ? "ok"
                            : item.status === "in_progress"
                              ? "warning"
                              : "neutral"
                        }
                        label={experimentStatusLabel(item.status)}
                        detail={item.status}
                      />
                    </td>
                    <td className="mono">{item.n_trials}</td>
                    <td className="mono">
                      {item.n_complete_pairs}
                      {item.planned_pairs > 0 ? ` / ${item.planned_pairs}` : ""}
                    </td>
                    <td className="mono">
                      {fmtPct(item.baseline_success_rate)} / {fmtPct(item.intervention_success_rate)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {experiment && analysis && (
        <>
          <Card
            title="配对分析"
            subtitle={`${experiment.name} · 分析 ${analysis.analysis_version} · 种子 ${analysis.seed}`}
            actions={<TruthBadge truth={analysis.data_origin === "simulated" ? "simulated" : "measured"} />}
          >
            {!analysis.available ? (
              <div className="banner banner-warning">
                <strong>分析不可用</strong>
                <span>{analysis.reason}</span>
              </div>
            ) : (
              <>
                <div className="metric-row metric-row-4">
                  <MetricValue
                    label="平均改善"
                    value={fmt(analysis.paired?.improvement_mm, 3)}
                    unit="mm"
                    tone={(analysis.paired?.improvement_mm ?? 0) > 0 ? "ok" : "warning"}
                    hint="基线 − 干预；正值更好"
                  />
                  <MetricValue
                    label={`${Math.round((analysis.confidence ?? 0.95) * 100)}% 置信区间（差值）`}
                    value={
                      analysis.paired
                        ? `[${fmt(analysis.paired.ci95_difference_mm.low, 2)}, ${fmt(
                            analysis.paired.ci95_difference_mm.high,
                            2,
                          )}]`
                        : "—"
                    }
                    unit="mm"
                    hint="干预 − 基线；负值偏向干预"
                  />
                  <MetricValue
                    label="完整配对"
                    value={analysis.validation.n_complete_pairs}
                    hint={`已记录 ${analysis.validation.n_input} 个试验`}
                  />
                  <MetricValue
                    label="符号检验 p"
                    value={fmtP(analysis.paired?.sign_test.p_value)}
                    hint={
                      analysis.paired
                        ? `${analysis.paired.sign_test.wins} 胜 / ${analysis.paired.sign_test.losses} 负 / ${analysis.paired.sign_test.ties} 平`
                        : "—"
                    }
                  />
                </div>

                {analysis.warnings.length > 0 && (
                  <div className="banner banner-warning">
                    <strong>解释限制</strong>
                    <ul className="warning-list">
                      {analysis.warnings.map((warning) => (
                        <li key={warning}>{warning}</li>
                      ))}
                    </ul>
                  </div>
                )}

                <div className="analysis-grid">
                  <div>
                    <h3 className="analysis-heading">连续结果（mm）</h3>
                    <div className="kv-list">
                      <KeyValue label="有误差的配对">
                        {analysis.paired?.n_pairs_with_error ?? "—"}
                      </KeyValue>
                      <KeyValue label="平均差值">
                        {fmt(analysis.paired?.difference_mm.mean, 4)} mm
                      </KeyValue>
                      <KeyValue label="效应量 dz">
                        {fmt(analysis.paired?.effect_size_dz, 3)}
                      </KeyValue>
                      <KeyValue label="顺序效应差">
                        {fmt(analysis.order_effect.difference_mm, 3)} mm
                      </KeyValue>
                      <KeyValue label="Bootstrap">
                        {analysis.bootstrap_samples} 次抽样，种子 {analysis.seed}
                      </KeyValue>
                    </div>
                  </div>
                  <div>
                    <h3 className="analysis-heading">二元结果</h3>
                    <div className="kv-list">
                      <KeyValue label="基线成功率">
                        {fmtPct(analysis.success?.baseline_rate)}
                      </KeyValue>
                      <KeyValue label="干预成功率">
                        {fmtPct(analysis.success?.intervention_rate)}
                      </KeyValue>
                      <KeyValue label="不一致对（基线/干预）">
                        {analysis.success
                          ? `${analysis.success.discordant.baseline_only} / ${analysis.success.discordant.intervention_only}`
                          : "—"}
                      </KeyValue>
                      <KeyValue label="McNemar p">
                        {fmtP(analysis.success?.mcnemar_exact_p)}
                      </KeyValue>
                    </div>
                  </div>
                </div>

                <h3 className="analysis-heading">方法汇总</h3>
                <div className="table-wrap">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>方法</th>
                        <th>n</th>
                        <th>误差均值</th>
                        <th>误差 SD</th>
                        <th>旋转均值</th>
                        <th>成功率</th>
                        <th>失败代码</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(["baseline", "intervention"] as Method[]).map((method) => {
                        const summary = analysis.methods[method];
                        if (!summary) return null;
                        const codes = Object.entries(summary.failure_codes)
                          .map(([code, count]) => `${failureCodeLabel(code)}=${count}`)
                          .join(", ");
                        return (
                          <tr key={method}>
                            <td>
                              <strong>{methodLabel(method)}</strong>
                              <div className="muted">{method}</div>
                            </td>
                            <td className="mono">{summary.n}</td>
                            <td className="mono">{fmt(summary.error.mean, 3)} mm</td>
                            <td className="mono">{fmt(summary.error.std, 3)} mm</td>
                            <td className="mono">{fmt(summary.rotation_error_deg.mean, 3)}°</td>
                            <td className="mono">{fmtPct(summary.success_rate)}</td>
                            <td className="mono ellipsis" title={codes || undefined}>
                              {codes || "—"}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </Card>

          <div className="experiment-layout">
            <div className="experiment-main">
              <Card
                title="记录试验"
                subtitle="追加式记录；成功后配对/方法不可重复"
                actions={
                  <Button
                    variant="secondary"
                    onClick={fillNext}
                    disabled={busy || !nextPlanEntry(detail)}
                  >
                    下一计划配对
                  </Button>
                }
              >
                {planComplete ? (
                  <div className="banner banner-warning">
                    <strong>计划已完成</strong>
                    <span>随机化计划中的全部配对/方法都已记录；不能再追加。</span>
                  </div>
                ) : (
                  <>
                    <div className="trial-form">
                      <label className="field">
                        <span>配对 ID</span>
                        {experiment.plan.entries.length > 0 ? (
                          <select
                            className="input"
                            value={trialForm.pair_id}
                            onChange={(event) => handlePairChange(event.target.value)}
                          >
                            {availablePairs.map((pairId) => (
                              <option key={pairId} value={pairId}>
                                {pairId}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <input
                            className="input"
                            value={trialForm.pair_id}
                            onChange={(event) =>
                              setTrialForm((current) => ({
                                ...current,
                                pair_id: event.target.value,
                              }))
                            }
                          />
                        )}
                      </label>
                      <label className="field">
                        <span>方法</span>
                        <select
                          className="input"
                          value={trialForm.method}
                          onChange={(event) => handleMethodChange(event.target.value as Method)}
                        >
                          <option value="baseline">基线（baseline）</option>
                          <option value="intervention">干预（intervention）</option>
                        </select>
                      </label>
                      <label className="field">
                        <span>试验顺序</span>
                        <select
                          className="input"
                          value={trialForm.trial_order}
                          onChange={(event) =>
                            setTrialForm((current) => ({
                              ...current,
                              trial_order: Number(event.target.value),
                            }))
                          }
                        >
                          <option value={1}>1</option>
                          <option value={2}>2</option>
                        </select>
                      </label>
                      <label className="field">
                        <span>夹具</span>
                        <input
                          className="input"
                          value={trialForm.fixture}
                          onChange={(event) =>
                            setTrialForm((current) => ({
                              ...current,
                              fixture: event.target.value,
                            }))
                          }
                        />
                      </label>
                      <label className="field">
                        <span>结果</span>
                        <select
                          className="input"
                          value={trialForm.grasp_success ? "success" : "failure"}
                          onChange={(event) =>
                            setTrialForm((current) => ({
                              ...current,
                              grasp_success: event.target.value === "success",
                            }))
                          }
                        >
                          <option value="success">成功</option>
                          <option value="failure">失败</option>
                        </select>
                      </label>
                      <label className="field">
                        <span>误差 (mm) *</span>
                        <input
                          className="input"
                          type="number"
                          min={0}
                          step="0.01"
                          value={trialForm.error}
                          onChange={(event) =>
                            setTrialForm((current) => ({ ...current, error: event.target.value }))
                          }
                        />
                      </label>
                      <label className="field">
                        <span>旋转误差 (°)</span>
                        <input
                          className="input"
                          type="number"
                          min={0}
                          step="0.01"
                          value={trialForm.rotation}
                          onChange={(event) =>
                            setTrialForm((current) => ({
                              ...current,
                              rotation: event.target.value,
                            }))
                          }
                        />
                      </label>
                      <label className="field">
                        <span>时长 (s)</span>
                        <input
                          className="input"
                          type="number"
                          min={0}
                          step="0.1"
                          value={trialForm.duration}
                          onChange={(event) =>
                            setTrialForm((current) => ({
                              ...current,
                              duration: event.target.value,
                            }))
                          }
                        />
                      </label>
                      <label className="field">
                        <span>失败代码</span>
                        <select
                          className="input"
                          value={trialForm.failure_code}
                          disabled={trialForm.grasp_success}
                          onChange={(event) =>
                            setTrialForm((current) => ({
                              ...current,
                              failure_code: event.target.value,
                            }))
                          }
                        >
                          {Object.keys(failureCodes).map((code) => (
                            <option key={code} value={code}>
                              {failureCodeLabel(code)} — {code}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="field">
                        <span>失败详情</span>
                        <input
                          className="input"
                          value={trialForm.failure_detail}
                          disabled={trialForm.grasp_success}
                          onChange={(event) =>
                            setTrialForm((current) => ({
                              ...current,
                              failure_detail: event.target.value,
                            }))
                          }
                        />
                      </label>
                      <label className="field">
                        <span>测量来源</span>
                        <select
                          className="input"
                          value={trialForm.measurement_source}
                          onChange={(event) =>
                            setTrialForm((current) => ({
                              ...current,
                              measurement_source: event.target.value,
                            }))
                          }
                        >
                          {MEASUREMENT_SOURCES.map((source) => (
                            <option key={source} value={source}>
                              {measurementSourceLabel(source)}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="field">
                        <span>轨迹数据集</span>
                        <select
                          className="input"
                          value={trialForm.trajectory_id}
                          onChange={(event) =>
                            setTrialForm((current) => ({
                              ...current,
                              trajectory_id: event.target.value,
                            }))
                          }
                        >
                          <option value="">无</option>
                          {trajectories.map((trajectory) => (
                            <option key={trajectory.trajectory_id} value={trajectory.trajectory_id}>
                              {trajectory.name}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="field trial-form-wide">
                        <span>操作员备注</span>
                        <input
                          className="input"
                          value={trialForm.operator_notes}
                          onChange={(event) =>
                            setTrialForm((current) => ({
                              ...current,
                              operator_notes: event.target.value,
                            }))
                          }
                        />
                      </label>
                    </div>
                    <div className="button-row form-actions">
                      <Button variant="primary" onClick={() => void handleRecordTrial()} disabled={busy}>
                        记录试验
                      </Button>
                      <span className="hint">
                        成功试验必须填写连续误差；失败试验必须选择失败代码。数据来源由实验定义决定。
                      </span>
                    </div>
                  </>
                )}
              </Card>

              <Card
                title="已记录试验"
                subtitle={`${detail?.trials.length ?? 0} 条追加记录；按配对和试验顺序排序`}
              >
                {detail && detail.trials.length > 0 ? (
                  <div className="table-wrap">
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>配对</th>
                          <th>#</th>
                          <th>方法</th>
                          <th>夹具</th>
                          <th>结果</th>
                          <th>误差 (mm)</th>
                          <th>旋转 (°)</th>
                          <th>时长 (s)</th>
                          <th>真值</th>
                          <th>轨迹</th>
                        </tr>
                      </thead>
                      <tbody>
                        {detail.trials.map((trial: TrialRecord) => (
                          <tr key={trial.trial_id}>
                            <td className="mono">{trial.pair_id}</td>
                            <td className="mono">{trial.trial_order}</td>
                            <td>{methodLabel(trial.method)}</td>
                            <td>{trial.fixture}</td>
                            <td>
                              <StatusChip
                                tone={trial.grasp_success ? "ok" : "fault"}
                                label={trial.grasp_success ? "成功" : "失败"}
                                detail={trial.failure_detail || undefined}
                              />
                              {trial.failure_code && (
                                <div className="mono trial-failure">{trial.failure_code}</div>
                              )}
                            </td>
                            <td className="mono">
                              {trial.continuous_error_mm === null
                                ? "—"
                                : trial.continuous_error_mm.toFixed(3)}
                            </td>
                            <td className="mono">
                              {trial.rotation_error_deg === null
                                ? "—"
                                : trial.rotation_error_deg.toFixed(2)}
                            </td>
                            <td className="mono">
                              {trial.duration_s === null ? "—" : trial.duration_s.toFixed(2)}
                            </td>
                            <td>
                              <TruthBadge truth={trial.truth} />
                            </td>
                            <td className="mono ellipsis">
                              {trial.trajectory_id ?? "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <EmptyState title="还没有试验">
                    使用上面的表单记录第一条 baseline 或 intervention trial。
                  </EmptyState>
                )}
              </Card>
            </div>

            <aside className="experiment-side">
              <Card title="定义" subtitle="创建后不可修改">
                <div className="kv-list">
                  <KeyValue label="ID">{experiment.experiment_id}</KeyValue>
                  <KeyValue label="创建时间">{experiment.created_at}</KeyValue>
                  <KeyValue label="来源">
                    <TruthBadge truth={experiment.data_origin === "simulated" ? "simulated" : "measured"} />
                  </KeyValue>
                  <KeyValue label="操作员">{experiment.operator || "—"}</KeyValue>
                  <KeyValue label="机器人">{experiment.robot_id || "—"}</KeyValue>
                  <KeyValue label="标定">{experiment.calibration_version || "—"}</KeyValue>
                  <KeyValue label="映射">{experiment.mapping_version || "—"}</KeyValue>
                  <KeyValue label="计划">
                    {experiment.protocol.planned_pairs > 0
                      ? `${experiment.plan.n_pairs} 对，种子 ${experiment.plan.seed}`
                      : "手动（无计划）"}
                  </KeyValue>
                  <KeyValue label="成功率">
                    {analysis?.success
                      ? `${fmtPct(analysis.success.baseline_rate)} / ${fmtPct(
                          analysis.success.intervention_rate,
                        )}`
                      : "—"}
                  </KeyValue>
                </div>
                <p className="hint">{experiment.stop_rules || "未记录停止规则。"}</p>
              </Card>

              <Card title="导出" subtitle="CSV 可用脚本复算；JSON 含定义、试验与分析">
                <div className="button-row export-row">
                  <a
                    className="button button-secondary"
                    href={api.experimentExportUrl(experiment.experiment_id, "csv")}
                    download
                  >
                    Trials CSV
                  </a>
                  <a
                    className="button button-secondary"
                    href={api.experimentExportUrl(experiment.experiment_id, "json")}
                    download
                  >
                    元数据 JSON
                  </a>
                </div>
              </Card>

              <Card title="失败代码" subtitle="失败试验必须归类">
                {analysis && analysis.available ? (
                  <div className="table-wrap">
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>代码</th>
                          <th>基线</th>
                          <th>干预</th>
                        </tr>
                      </thead>
                      <tbody>
                        {Object.keys(analysis.methods.baseline?.failure_codes ?? {}).length === 0 &&
                        Object.keys(analysis.methods.intervention?.failure_codes ?? {}).length ===
                          0 ? (
                          <tr>
                            <td colSpan={3} className="muted">
                              没有失败记录
                            </td>
                          </tr>
                        ) : (
                          Array.from(
                            new Set([
                              ...Object.keys(analysis.methods.baseline?.failure_codes ?? {}),
                              ...Object.keys(analysis.methods.intervention?.failure_codes ?? {}),
                            ]),
                          )
                            .sort()
                            .map((code) => (
                              <tr key={code}>
                                <td className="mono">{failureCodeLabel(code)}</td>
                                <td className="mono">
                                  {analysis.methods.baseline?.failure_codes[code] ?? 0}
                                </td>
                                <td className="mono">
                                  {analysis.methods.intervention?.failure_codes[code] ?? 0}
                                </td>
                              </tr>
                            ))
                        )}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="muted">暂无分析</p>
                )}
              </Card>

              <Card title="协议说明" subtitle="方法学限制与可追溯信息">
                <ul className="note-list">
                  {analysis?.limitations.map((item) => <li key={item}>{item}</li>)}
                  <li>
                    分析版本 {analysis?.analysis_version ?? "—"}；同一 trials 与 seed 可复算。
                  </li>
                  <li>
                    {experiment.data_origin === "simulated"
                      ? "模拟实验只用于展示流程，不是硬件验证。"
                      : diagnostics?.mapping.verification_status === "provisional"
                        ? "当前映射仍为暂定；不要据此宣称硬件显著改善。"
                        : "映射已验证；硬件对比仍需结合夹具与测量误差解读。"}
                  </li>
                </ul>
              </Card>
            </aside>
          </div>
        </>
      )}
    </div>
  );
}
