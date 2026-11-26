import { api } from "../api/client";
import { Card, EmptyState, KeyValue, StatusChip } from "../components/ui";
import { formatNumber } from "../format";
import { usePreferences, useRobotStore } from "../store/robotStore";

export function SettingsPage() {
  const robot = useRobotStore((store) => store.state);
  const diagnostics = useRobotStore((store) => store.diagnostics);
  const preferences = usePreferences();

  if (!robot || !diagnostics) {
    return <EmptyState title="正在读取配置…">需要后端 /api/state 与 /api/diagnostics。</EmptyState>;
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1 className="page-title">设置</h1>
          <p className="page-subtitle">
            配置只读。危险配置（模式、阈值、映射）需评审并写审计事件，不能在 UI 直接修改。
          </p>
        </div>
        <div className="page-actions">
          <StatusChip tone="warning" label="配置只读" />
        </div>
      </header>

      <div className="grid grid-2">
        <Card title="连接与能力" subtitle="后端启动参数决定模式；UI 不提供运行时切换">
          <div className="kv-list">
            <KeyValue label="模式">
              <StatusChip tone={robot.mode === "mock" ? "simulation" : "warning"} label={robot.mode === "mock" ? "模拟" : "硬件"} detail={robot.mode} />
            </KeyValue>
            <KeyValue label="浏览器来源">{window.location.origin}</KeyValue>
            <KeyValue label="API 路径">/api（开发期由 Vite 代理到后端）</KeyValue>
            <KeyValue label="绑定地址">{String(diagnostics.environment.bind_host ?? "—")}</KeyValue>
            <KeyValue label="ROS 网关">经 <code>/state</code>、<code>/battery</code>、<code>/tf</code> 读取（不在本页配置）</KeyValue>
            <KeyValue label="硬件运动">
              <StatusChip
                tone={robot.capabilities.hardware_motion ? "ok" : "warning"}
                label={robot.capabilities.hardware_motion ? "已启用" : "已关闭"}
              />
            </KeyValue>
            <KeyValue label="硬件读取">
              <StatusChip
                tone={robot.capabilities.hardware_read ? "ok" : "warning"}
                label={robot.capabilities.hardware_read ? "已启用" : "未启用"}
              />
            </KeyValue>
          </div>
          <p className="footnote">
            切换模式需要设置后端环境变量 <code>ARMPI_MODE=mock|hardware</code> 并重启。运行中的 UI 不能切换。
          </p>
        </Card>

        <Card title="显示偏好" subtitle="仅保存在本浏览器；不影响后端">
          <div className="form-grid">
            <label className="field">
              <span>角度单位</span>
              <select
                className="input"
                value={preferences.unit}
                onChange={(event) => preferences.setUnit(event.target.value as "rad" | "deg")}
              >
                <option value="rad">rad</option>
                <option value="deg">deg</option>
              </select>
            </label>
            <label className="field">
              <span>小数位</span>
              <select
                className="input"
                value={preferences.decimals}
                onChange={(event) => preferences.setDecimals(Number(event.target.value))}
              >
                <option value={2}>2</option>
                <option value={3}>3</option>
                <option value={4}>4</option>
              </select>
            </label>
            <label className="field">
              <span>时间显示</span>
              <select
                className="input"
                value={preferences.timeMode}
                onChange={(event) => preferences.setTimeMode(event.target.value as "local" | "utc")}
              >
                <option value="local">本地</option>
                <option value="utc">UTC</option>
              </select>
            </label>
            <label className="field">
              <span>密度</span>
              <select
                className="input"
                value={preferences.density}
                onChange={(event) => preferences.setDensity(event.target.value as "compact" | "comfortable")}
              >
                <option value="compact">紧凑</option>
                <option value="comfortable">宽松</option>
              </select>
            </label>
          </div>
        </Card>
      </div>

      <Card
        title="关节映射"
        subtitle={`${diagnostics.mapping.mapping_version} · ${diagnostics.mapping.verification_status}`}
        actions={
          <StatusChip
            tone={diagnostics.mapping.verified ? "ok" : "warning"}
            label={diagnostics.mapping.verified ? "已验证" : "暂定"}
          />
        }
      >
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>关节</th>
                <th>舵机</th>
                <th>初始原始值</th>
                <th>原始范围</th>
                <th>方向</th>
                <th>弧度范围</th>
                <th>来源</th>
                <th>验证</th>
              </tr>
            </thead>
            <tbody>
              {diagnostics.mapping.joints.map((joint) => (
                <tr key={joint.name}>
                  <td className="mono">{joint.name}</td>
                  <td className="mono">{joint.servo_id}</td>
                  <td className="mono">{joint.init_raw}</td>
                  <td className="mono">
                    {joint.min_raw} … {joint.max_raw}
                  </td>
                  <td className="mono">{joint.direction}</td>
                  <td className="mono">
                    {formatNumber(joint.min_rad, 3)} … {formatNumber(joint.max_rad, 3)}
                  </td>
                  <td>{joint.limit_source}</td>
                  <td>{joint.verified ? "是" : "暂定"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="footnote">
          编辑映射被禁用：需要实机测量、版本化与评审。当前 hash：
          <span className="mono"> {diagnostics.mapping.mapping_hash.slice(0, 24)}…</span>
        </p>
      </Card>

      <div className="grid grid-2">
        <Card title="安全阈值" subtitle="只读；修改需二次确认与审计">
          <div className="kv-list">
            {Object.entries(diagnostics.mapping.safety).map(([key, value]) => (
              <KeyValue key={key} label={key}>
                <span className="mono">{formatNumber(Number(value), 3)}</span>
              </KeyValue>
            ))}
          </div>
        </Card>

        <Card title="日志与导出" subtitle="只追加 SQLite；事件导出包含 schema 版本">
          <div className="kv-list">
            <KeyValue label="事件存储">{String(diagnostics.environment.mapping_version ? "SQLite WAL" : "—")}</KeyValue>
            <KeyValue label="保留期">30 天（设计值，清理任务未实现）</KeyValue>
            <KeyValue label="隐私">默认脱敏主机名与绝对路径</KeyValue>
            <KeyValue label="存储错误">
              {diagnostics.storage_error ? (
                <span className="error-text">{diagnostics.storage_error}</span>
              ) : (
                "无"
              )}
            </KeyValue>
          </div>
          <p className="footnote">
            <a href={api.exportUrl()} download>
              导出事件 CSV
            </a>
          </p>
        </Card>
      </div>

      <Card title="进度与限制" subtitle="未实现功能只列出，不放置可点击的假按钮">
        <ul className="roadmap-list">
          <li>
            <strong>G6 单关节受控运动</strong> — 单关节、限位已普查、网关按实测脉冲独立复核；
            默认关闭，必须用 <code>ARMPI_HARDWARE_MOTION</code> / <code>ARMPI_SUPERVISED_MOTION_ACK</code>
            组合显式启用。
          </li>
          <li>
            <strong>G7 姿态接管</strong> — 一次命令 = 五个关节或一个夹爪；姿态网关实例（8766）+
            TAKEOVER 确认短语；完成以 <code>GetBusServoState</code> 实测寄存器为准。
          </li>
          <li>
            <strong>G8 任务接管 + G8.1 底盘接近 + 快路径</strong> — 相机定位、深位姿抓取、7 个臂阶段、
            夹持验证与放回；木块在抓取带外时同一次运行内先底盘接近。实测 13.46 s、落点 2.27 mm
            （2026-11-21）。
          </li>
          <li>
            <strong>物理急停与独立编码器</strong> — 未实现；Stop 只是保持指令已发送，不是刹车。
          </li>
        </ul>
        <p className="footnote">
          当前版本 {String(diagnostics.environment.backend_version ?? "—")}；G1–G8.1 与 G8 快路径已实现。
          关节映射已验证；<code>/servo_states</code> 是命令缓存，不是实测反馈。无急停、无自碰撞模型。
        </p>
      </Card>
    </div>
  );
}
