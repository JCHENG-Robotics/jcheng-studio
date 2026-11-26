import { useMemo, useState } from "react";
import type { EventRecord } from "../api/types";
import { api } from "../api/client";
import { Button, Card, EmptyState, KeyValue, StatusChip } from "../components/ui";
import { formatClock, formatTime, isFaultStage, isWarningStage } from "../format";
import { stageLabel } from "../labels";
import { usePreferences, useRobotStore } from "../store/robotStore";

function severityOf(event: EventRecord): "fault" | "warning" | "info" {
  if (event.error_code || isFaultStage(event.stage)) return "fault";
  if (isWarningStage(event.stage)) return "warning";
  return "info";
}

export function EventTimeline() {
  const events = useRobotStore((store) => store.events);
  const preferences = usePreferences();
  const [stage, setStage] = useState("");
  const [source, setSource] = useState("");
  const [severity, setSeverity] = useState("");
  const [commandId, setCommandId] = useState("");
  const [selected, setSelected] = useState<EventRecord | null>(null);
  const [frozen, setFrozen] = useState(false);
  const [frozenEvents, setFrozenEvents] = useState<EventRecord[]>([]);
  const [copied, setCopied] = useState(false);

  const stages = useMemo(
    () => Array.from(new Set(events.map((event) => event.stage))).sort(),
    [events],
  );
  const sources = useMemo(
    () => Array.from(new Set(events.map((event) => event.source))).sort(),
    [events],
  );

  const visible = useMemo(() => {
    const base = frozen ? frozenEvents : events;
    return base.filter((event) => {
      if (stage && event.stage !== stage) return false;
      if (source && event.source !== source) return false;
      if (severity && severityOf(event) !== severity) return false;
      if (commandId && !(event.command_id ?? "").toLowerCase().includes(commandId.toLowerCase())) {
        return false;
      }
      return true;
    });
  }, [events, frozen, frozenEvents, stage, source, severity, commandId]);

  const copySelected = async () => {
    if (!selected) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(selected, null, 2));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1 className="page-title">事件时间线</h1>
          <p className="page-subtitle">
            命令、状态与错误事件。事件只追加；故障时可冻结视图而不丢缓冲。
          </p>
        </div>
        <div className="page-actions">
          <Button
            variant={frozen ? "primary" : "secondary"}
            onClick={() => {
              if (frozen) {
                setFrozen(false);
                setFrozenEvents([]);
              } else {
                setFrozenEvents(events);
                setFrozen(true);
              }
            }}
          >
            {frozen ? "恢复实时视图" : "冻结视图"}
          </Button>
          <a className="button button-secondary" href={api.exportUrl()} download>
            导出 CSV
          </a>
        </div>
      </header>

      {frozen && (
        <div className="banner banner-warning">
          <strong>视图已冻结</strong>
          <span>显示 {frozenEvents.length} 条快照事件；新事件仍会写入后端，但不会自动滚动。</span>
        </div>
      )}

      <Card
        title="筛选"
        subtitle={`${visible.length} / ${events.length} 条事件`}
        actions={
          <Button
            variant="ghost"
            onClick={() => {
              setStage("");
              setSource("");
              setSeverity("");
              setCommandId("");
            }}
          >
            清除筛选
          </Button>
        }
      >
        <div className="filter-grid">
          <label className="field">
            <span>阶段</span>
            <select className="input" value={stage} onChange={(event) => setStage(event.target.value)}>
              <option value="">全部</option>
              {stages.map((item) => (
                <option key={item} value={item}>
                  {stageLabel(item)} ({item})
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>来源</span>
            <select className="input" value={source} onChange={(event) => setSource(event.target.value)}>
              <option value="">全部</option>
              {sources.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>严重度</span>
            <select className="input" value={severity} onChange={(event) => setSeverity(event.target.value)}>
              <option value="">全部</option>
              <option value="info">信息</option>
              <option value="warning">警告</option>
              <option value="fault">故障</option>
            </select>
          </label>
          <label className="field">
            <span>命令 ID</span>
            <input
              className="input mono"
              value={commandId}
              onChange={(event) => setCommandId(event.target.value)}
              placeholder="cmd_…"
            />
          </label>
        </div>
      </Card>

      <div className="timeline-layout">
        <Card title="事件" subtitle="点击一行查看完整 JSON">
          {visible.length === 0 ? (
            <EmptyState title="没有匹配的事件">
              执行一次预览、故障注入或停止请求后，事件会出现在这里。
            </EmptyState>
          ) : (
            <div className="table-wrap timeline-table">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>阶段</th>
                    <th>来源</th>
                    <th>命令</th>
                    <th>结果</th>
                    <th>错误</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((event) => (
                    <tr
                      key={event.event_id}
                      className={`row-clickable${selected?.event_id === event.event_id ? " row-selected" : ""}`}
                      onClick={() => setSelected(event)}
                    >
                      <td className="mono">{formatClock(event.wall_time, preferences.timeMode)}</td>
                      <td>
                        <StatusChip
                          tone={severityOf(event) === "fault" ? "fault" : severityOf(event) === "warning" ? "warning" : "info"}
                          label={stageLabel(event.stage)}
                          detail={event.stage}
                        />
                      </td>
                      <td>{event.source}</td>
                      <td className="mono ellipsis">{event.command_id ?? "—"}</td>
                      <td className="ellipsis" title={event.result}>
                        {event.result}
                      </td>
                      <td className="mono">{event.error_code ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card
          title="事件详情"
          subtitle={selected ? selected.event_id : "选择一条事件"}
          actions={
            selected && (
              <Button variant="secondary" onClick={() => void copySelected()}>
                {copied ? "已复制" : "复制 JSON"}
              </Button>
            )
          }
        >
          {selected ? (
            <div className="event-detail">
              <KeyValue label="时间">{formatTime(selected.wall_time, preferences.timeMode)}</KeyValue>
              <KeyValue label="单调时钟">{selected.monotonic_ms.toFixed(3)} ms</KeyValue>
              <KeyValue label="阶段">
                {stageLabel(selected.stage)}（<span className="mono">{selected.stage}</span>）
              </KeyValue>
              <KeyValue label="来源">{selected.source}</KeyValue>
              <KeyValue label="命令">{selected.command_id ?? "—"}</KeyValue>
              <KeyValue label="错误">{selected.error_code ?? "—"}</KeyValue>
              <KeyValue label="Schema">{selected.schema_version}</KeyValue>
              <pre className="code-block">{JSON.stringify(selected, null, 2)}</pre>
            </div>
          ) : (
            <EmptyState title="选择一条事件">右侧会显示完整 JSON、错误码与可复制的审计字段。</EmptyState>
          )}
        </Card>
      </div>
    </div>
  );
}
