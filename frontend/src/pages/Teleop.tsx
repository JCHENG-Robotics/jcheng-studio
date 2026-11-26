import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type { SystemStopResult, TeleopStatusProbe } from "../api/types";
import { Button, Card, EmptyState, ErrorBanner, KeyValue, StatusChip } from "../components/ui";

function errorText(error: unknown): string {
  return error instanceof ApiError ? `${error.code}: ${error.message}` : String(error);
}

function probeTone(probe: TeleopStatusProbe | null): "ok" | "warning" | "fault" | "neutral" {
  if (!probe) return "neutral";
  if (!probe.configured) return "neutral";
  if (!probe.reachable || !probe.valid) return "fault";
  if (probe.stale || probe.degraded) return "warning";
  return "ok";
}

function probeLabel(probe: TeleopStatusProbe | null): string {
  if (!probe) return "未采样";
  if (!probe.configured) return "未配置";
  if (!probe.reachable) return "不可达";
  if (!probe.valid) return "状态无效";
  if (probe.stale || probe.degraded) return "已降级";
  return "正常";
}

export function TeleopPanel() {
  const [probe, setProbe] = useState<TeleopStatusProbe | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [operator, setOperator] = useState("studio-operator");
  const [stopResult, setStopResult] = useState<SystemStopResult | null>(null);
  const [stopError, setStopError] = useState<string | null>(null);
  const [stopBusy, setStopBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const next = await api.teleopStatus();
        if (alive) {
          setProbe(next);
          setError(null);
        }
      } catch (caught) {
        if (alive) {
          setError(errorText(caught));
          setProbe(null);
        }
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), 1000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  const handleStop = async () => {
    if (!window.confirm("请求系统软件停止？该停止会锁存，必须在系统侧复位后才能再次使能。")) {
      return;
    }
    setStopBusy(true);
    setStopError(null);
    try {
      const result = await api.systemStop({ operatorId: operator.trim() });
      setStopResult(result);
    } catch (caught) {
      setStopError(errorText(caught));
    } finally {
      setStopBusy(false);
    }
  };

  const capture = probe?.capture ?? {};
  const captureRows =
    typeof capture.rows_written === "object" && capture.rows_written !== null
      ? Object.entries(capture.rows_written as Record<string, number>)
      : [];

  return (
    <div className="grid grid-2">
      <Card
        title="遥操作状态"
        subtitle="只读状态来自 teleop 进程；缺失或超过 1 s 显示为降级，绝不显示为就绪"
      >
        <div className="row">
          <StatusChip tone={probeTone(probe)} label={probeLabel(probe)} detail={probe?.phase ?? undefined} />
          {probe?.latency_ms !== null && probe?.latency_ms !== undefined && (
            <span className="muted">轮询 {probe.latency_ms} ms</span>
          )}
        </div>
        {error && <ErrorBanner message={error} />}
        {probe?.errors.map((item) => (
          <ErrorBanner key={`${item.code}-${item.message}`} message={`${item.code}: ${item.message}`} />
        ))}
        {!probe ? (
          <EmptyState title="尚未取得 teleop 状态">
            teleop 状态服务未配置或尚未启动；此面板不会把未知状态显示为就绪。
          </EmptyState>
        ) : (
          <div className="kv-list">
            <KeyValue label="阶段">{probe.phase ?? "—"}</KeyValue>
            <KeyValue label="输入年龄">
              {probe.input_age_ms === null ? "—" : `${probe.input_age_ms.toFixed(1)} ms`}
            </KeyValue>
            <KeyValue label="输入状态">{probe.stale ? "stale（降级）" : "fresh"}</KeyValue>
            <KeyValue label="Deadman">{probe.deadman === null ? "—" : probe.deadman ? "按下" : "释放"}</KeyValue>
            <KeyValue label="模式">{probe.mode ?? "—"}</KeyValue>
            <KeyValue label="Profile">{probe.profile_id ?? "—"}</KeyValue>
            <KeyValue label="Profile hash">
              <span className="mono">{probe.profile_hash ?? "—"}</span>
            </KeyValue>
            <KeyValue label="Contract hash">
              <span className="mono">{probe.contract_hash ?? "—"}</span>
            </KeyValue>
            <KeyValue label="Lease ref">
              <span className="mono">{probe.lease_ref ?? "—"}</span>
            </KeyValue>
            <KeyValue label="采集">
              {capture.enabled ? String(capture.capture_id ?? "enabled") : "未启用"}
            </KeyValue>
          </div>
        )}
        {captureRows.length > 0 && (
          <div className="kv-list">
            {captureRows.map(([name, count]) => (
              <KeyValue key={name} label={`${name} 行`}>
                {count}
              </KeyValue>
            ))}
          </div>
        )}
      </Card>

      <Card
        title="系统停止"
        subtitle="这是系统软件停止，会锁存；不是急停。Studio 不持有租约、不发送运动指令。"
      >
        <label className="field">
          <span>操作员 ID</span>
          <input
            value={operator}
            onChange={(event) => setOperator(event.target.value)}
            maxLength={128}
          />
        </label>
        <div className="row">
          <Button
            variant="danger"
            onClick={() => void handleStop()}
            disabled={stopBusy || operator.trim().length === 0}
          >
            {stopBusy ? "请求中…" : "请求系统停止"}
          </Button>
          <span className="muted">经 /v1/motion/stop；无租约、无运动执行</span>
        </div>
        {stopError && <ErrorBanner message={stopError} onDismiss={() => setStopError(null)} />}
        {stopResult && (
          <div className="kv-list">
            <KeyValue label="已请求">{stopResult.requested ? "是" : "否"}</KeyValue>
            <KeyValue label="系统接受">{stopResult.accepted ? "是" : "否"}</KeyValue>
            <KeyValue label="往返延迟">
              {stopResult.latency_ms === null ? "—" : `${stopResult.latency_ms} ms`}
            </KeyValue>
            <KeyValue label="HTTP">{stopResult.status_code ?? "—"}</KeyValue>
            {stopResult.error && (
              <KeyValue label="错误">
                {stopResult.error.code}: {stopResult.error.message}
              </KeyValue>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}
