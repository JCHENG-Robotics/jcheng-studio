import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type {
  CapabilitiesPayload,
  CapabilityFeature,
  CapabilityStatus,
} from "../api/types";
import { Card, StatusChip } from "../components/ui";

/**
 * Static fallback so every surface stays visible even when the backend is
 * unreachable. The backend registry is the source of truth; this list only
 * keeps the page useful while the API is down.
 */
const FALLBACK_FEATURES: Array<Omit<CapabilityFeature, "status" | "detail">> = [
  { id: "overview", title: "总览", route: "/", category: "页面", description: "连接链路、安全门禁与快照" },
  { id: "joints", title: "关节控制", route: "/joints", category: "页面", description: "单关节/姿态预览与执行" },
  { id: "teleop", title: "遥操作", route: "/teleop", category: "页面", description: "只读遥操作状态与系统停止" },
  { id: "task", title: "任务接管", route: "/task", category: "页面", description: "任务规划、预览与接管执行" },
  { id: "diagnostics", title: "诊断", route: "/diagnostics", category: "页面", description: "ROS 图、缺陷、探针与回放" },
  { id: "trajectory", title: "轨迹检查", route: "/trajectory", category: "页面", description: "离线轨迹指标与图表" },
  { id: "experiments", title: "实验台", route: "/experiments", category: "页面", description: "离线实验记录与统计" },
  { id: "visual", title: "可视化工作台", route: "/visual", category: "页面", description: "URDF/FK 与相机参考" },
  { id: "events", title: "事件时间线", route: "/events", category: "页面", description: "只读事件时间线" },
  { id: "settings", title: "设置", route: "/settings", category: "页面", description: "只读能力与版本" },
  { id: "motion", title: "运动执行", route: "/joints", category: "集成", description: "运动意图下发能力" },
  { id: "session", title: "操作会话", route: "/diagnostics", category: "集成", description: "M2a 会话记录与授权" },
  { id: "replay", title: "会话回放", route: "/diagnostics", category: "集成", description: "M3 commanded/measured 分析" },
  { id: "gateway_v1", title: "/v1 网关诊断", route: "/diagnostics", category: "集成", description: "只读 health/contract/state/safety" },
  { id: "system_stop", title: "系统停止", route: "/teleop", category: "集成", description: "M5 系统软件停止代理" },
  { id: "joints_data", title: "关节反馈", route: "/joints", category: "数据", description: "joint_states + servo_states" },
  { id: "battery_data", title: "电池", route: "/", category: "数据", description: "电池电压/状态" },
  { id: "camera_rgb", title: "相机 · RGB", route: "/", category: "数据", description: "/depth_cam/rgb/image_raw" },
  { id: "camera_aux", title: "相机 · 辅助", route: "/", category: "数据", description: "/camera/color/image_raw" },
];

const STATUS_META: Record<
  CapabilityStatus,
  { label: string; tone: "ok" | "warning" | "fault" | "neutral" }
> = {
  available: { label: "可用", tone: "ok" },
  read_only: { label: "只读", tone: "warning" },
  degraded: { label: "降级", tone: "warning" },
  not_configured: { label: "未配置", tone: "neutral" },
  blocked: { label: "被阻止", tone: "fault" },
  offline: { label: "离线", tone: "fault" },
};

const STATUS_ORDER: CapabilityStatus[] = [
  "available",
  "read_only",
  "degraded",
  "not_configured",
  "blocked",
  "offline",
];

function offlineFeatures(): CapabilityFeature[] {
  return FALLBACK_FEATURES.map((feature) => ({
    ...feature,
    status: "offline" as const,
    detail: "后端不可达；状态未知",
  }));
}

export function CapabilitiesPage() {
  const [payload, setPayload] = useState<CapabilitiesPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .capabilities()
      .then((next) => {
        if (!cancelled) {
          setPayload(next);
          setError(null);
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setPayload(null);
          setError(
            caught instanceof ApiError
              ? `${caught.code}: ${caught.message}`
              : String(caught),
          );
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const features = payload?.features ?? offlineFeatures();
  const groups = useMemo(
    () => [...new Set(features.map((feature) => feature.category))],
    [features],
  );

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1 className="page-title">功能总览</h1>
          <p className="page-subtitle">
            全部页面与集成的固定清单；状态随实机/配置变化，但功能不会消失。
          </p>
        </div>
        <div className="page-actions">
          {payload && (
            <StatusChip
              tone={payload.demo ? "simulation" : payload.mode === "mock" ? "simulation" : "warning"}
              label={payload.demo ? "演示模式" : payload.mode === "mock" ? "模拟" : "实机"}
              detail={`mode=${payload.mode} source=${payload.source_label}`}
            />
          )}
          <StatusChip tone="info" label="固定清单" detail={`${features.length} 项`} />
        </div>
      </header>

      {error && (
        <div className="banner banner-warning">
          <strong>后端不可达</strong>
          <span>{error}。下面显示静态功能清单，状态全部标记为离线。</span>
        </div>
      )}

      {payload?.demo && (
        <div className="banner banner-warning">
          <strong>演示模式（DEMO）</strong>
          <span>数据来自 Mock，页面可完整展示，但不会下发任何运动。</span>
        </div>
      )}

      {payload && (
        <Card title="状态汇总" subtitle={`生成时间 ${payload.generated_at}`}>
          <div className="row">
            {STATUS_ORDER.map((status) => {
              const count = payload.summary[status] ?? 0;
              return (
                <StatusChip
                  key={status}
                  tone={STATUS_META[status].tone}
                  label={`${STATUS_META[status].label} ${count}`}
                  detail={status}
                />
              );
            })}
          </div>
        </Card>
      )}

      {groups.map((group) => (
        <Card key={group} title={group} subtitle={`${features.filter((f) => f.category === group).length} 项`}>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>功能</th>
                  <th>状态</th>
                  <th>说明</th>
                  <th>入口</th>
                </tr>
              </thead>
              <tbody>
                {features
                  .filter((feature) => feature.category === group)
                  .map((feature) => {
                    const meta = STATUS_META[feature.status];
                    return (
                      <tr key={feature.id}>
                        <td>
                          <strong>{feature.title}</strong>
                          <div className="muted mono">{feature.id}</div>
                        </td>
                        <td>
                          <StatusChip tone={meta.tone} label={meta.label} detail={feature.detail} />
                        </td>
                        <td>
                          {feature.description}
                          <div className="muted">{feature.detail}</div>
                        </td>
                        <td>
                          <Link to={feature.route}>打开</Link>
                        </td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        </Card>
      ))}

      {loading && !payload && (
        <p className="footnote">正在读取 /api/capabilities…（离线时显示静态清单）</p>
      )}
    </div>
  );
}
