# jcheng-studio

**简体中文** | [English](README.md)

> 状态：**实验性**，2026-11-26。版本 0.8.0。
> 已从 `jcheng-manipulation` 冻结基线迁入 ArmPi Debug Studio 控制台，并实现只读
> `/v1` REST 客户端（health、contract、state、safety）与只读 `/v1/ws/state` 订阅；
> 已用 mock 网关和真实的 `jcheng-system` 只读网关验证。M2a 操作会话存储、M2b
> 关节/夹爪意图转发与 M3 离线会话回放已实现；lease 由操作员从系统层取得，
> Studio 不申请、不持有。M5 新增遥操作面板：轮询只读 teleop 状态 API，并提供
> 一个需显式开启的系统停止代理（`POST /v1/motion/stop`），不持有 lease、不执行运动。
> 机器人侧网关仍不在本仓库，本仓库尚未执行任何实机会话。
> 英文为正式版本，见 [README.md](README.md)；本文为中文对照。

`jcheng-studio` 是 JCHENG 生态的 L4 操作台，也是 ArmPi Debug Studio 在生态中的
归属仓库。它是面向人的界面：连接与诊断、关节与夹爪调试、轨迹检查和 ArmPi Ultra
上的实验记录。它只通过 HTTP/WS 访问 `jcheng-system` 网关：不加入任何 DDS 域、
不打开设备、不执行运动。它提出的每个动作都由系统层校验并执行（或拒绝）。

## 范围与边界

| 项目 | 本仓库 |
|---|---|
| 层级 | L4 操作应用（操作台），按仓库分类法 |
| 运行位置 | 操作员笔记本：浏览器界面与本地后端；从不在机器人上运行 |
| 输入 | 网关 HTTP/WS 响应（`/v1/state`、`/v1/safety`、`/v1/contract`、事件）与操作员输入 |
| 输出 | 当前提供只读 `/v1` 诊断；操作会话记录与导出证据存放在仓库外；运动**意图**从 M2 起转发给系统网关 |
| 运动权限 | 无。控制租约、deadman、关节/底盘限位、停止锁存与 `ExecuteMotion` 归系统层 |
| 硬件访问 | `none` |
| 拥有设备 | 无 |
| 禁止 | 加入 DDS 域 0/41/42、访问设备、厂商话题、`rclpy`、厂商 SDK 调用、直接 `ExecuteMotion` 或 `/cmd_vel`、SSH 到机器人、把令牌写入 git、伪造或插值机器人状态 |

系统层是唯一的运动起点，感知层是唯一的建议目标来源。控制台只显示网关报告的
内容，把 commanded 与 measured 分开标注，绝不把厂商栈里的 `/joint_states`
（命令缓存）当作测量值。控制台不是急停：平台没有急停、抱闸、编码器或自碰撞
模型。

## 已迁移内容

已迁移的控制台是冻结基线的 G1–G8.1 功能面，并按本仓库边界做了适配。完整来源
与排除记录见 [docs/MIGRATION.md](docs/MIGRATION.md)。

| 资产 | 位置 | 状态 |
|---|---|---|
| FastAPI 后端、mock 适配器与 legacy HTTP 适配器 | `backend/` | 已迁移；离线测试通过 |
| 只读 `/v1` REST 客户端与诊断 | `backend/app/adapters/gateway_v1.py`；诊断页 | M1a 完成；已用 mock 网关验证 |
| 只读 `/v1/ws/state` 订阅 | `backend/app/adapters/gateway_v1_stream.py`；诊断页 | M1b 完成；mock 与真实 `jcheng-system` 网关验证 |
| M2a 操作会话存储与授权门禁 | `backend/app/sessions/`；诊断页 | 完成；SQLite 在仓库外，封存/导出已测试 |
| M2b 关节/夹爪意图转发 | `backend/app/adapters/gateway_v1_intent.py`；诊断页 | 完成；操作员提供 lease，mock 校验/执行已验证 |
| M3 离线会话回放与 commanded/measured 分析 | `backend/app/analysis/session_replay.py`；诊断页 | 完成；带 provenance 的回放与哈希导出 |
| React/TypeScript 控制台（八个页面） | `frontend/` | 已迁移；生产构建通过 |
| 关节映射、位姿/任务契约与视觉模型 | `configs/` | 已迁移 |
| 任务契约使用的手眼标定 | `configs/calibration/` | 已迁移（2026-11-18 TSAI 结果） |
| 操作员运行脚本 | `tools/` 与 `tools/studio/` | 已迁移；仅操作员手动运行 |
| G1–G8.1 与 E7 设计记录 | `docs/legacy/` | 迁移记录；链接指向基线仓库 |
| 产品需求文档 | `docs/PRD.md` | 已迁移 PRD v1.0 |

未迁移：机器人侧 `ros2_gateway/`（ROS 2/DDS，L2）、运行时 `data/studio.db`、硬件
证据包，以及厂商派生的 URDF。legacy HTTP 适配器面向冻结网关（`/health`、
`X-Armpi-Token`），不是生态的 `/v1` 契约。`/v1` REST 与 WebSocket 客户端都是
只读的；M2a 会话存储对机器人运动仍是只读，M2b 使用**操作员从系统层取得的 lease**
转发关节/夹爪意图；Studio 不申请、不持有 lease。

## 仓库结构

```text
jcheng-studio/
├── backend/
│   ├── app/                     # FastAPI 应用、适配器、安全核心、领域与持久化
│   └── tests/                   # 191 passed；REST/WS、会话、意图与回放路径
├── packages/jcheng_core/        # 共享的纯 Python 统计与点云原语
├── configs/                     # 关节映射、接管契约、视觉模型、标定
├── frontend/
│   ├── src/                     # React 控制台页面与类型化 API 客户端
│   └── e2e/                     # Playwright 规格；CI 只跑 mock 套件
├── tools/
│   ├── studio/                  # 启动脚本与 G7/G8 操作员运行脚本
│   └── ...                      # TF 快照辅助与 S 曲线参考实现
├── docs/
│   ├── ARCHITECTURE.md          # 控制台、后端、会话与网关平面
│   ├── INTEGRATION.md           # 网关契约、准入与故障语义
│   ├── SESSIONS.md              # 操作会话、授权与记录
│   ├── MIGRATION.md             # 来源、排除项与边界适配
│   ├── GATEWAY_V1.md            # 只读 /v1 客户端（M1a）
│   ├── PRD.md                   # 产品需求
│   └── legacy/                  # G1–G8.1 与 E7 记录
├── capability.yaml              # 准入清单（observer，hardware_access: none）
├── tests/                       # 清单、文档集与无硬件检查
├── scripts/check_repo_gate.sh   # 仓库门禁（本地与 CI）
├── evidence/20261126_scaffold/  # M0 证据记录
├── evidence/20261126_console_port/  # v0.2.0 迁移证据
├── evidence/20261126_gateway_v1/    # M1a 只读 REST 客户端证据
├── evidence/20261126_gateway_ws/    # M1b 只读 WS 客户端证据
├── evidence/20261126_sessions/      # M2a 会话存储证据
├── evidence/20261126_intent/        # M2b 意图证据
├── evidence/20261126_replay/        # M3 回放/分析证据
├── VERSION
├── README.md / README.zh-CN.md
├── ROADMAP.md / CHANGELOG.md / ACCEPTANCE.md
└── LICENSE-REVIEW.md
```

## 环境要求

- Python 3.11–3.12 与 `pip`；后端固定 FastAPI、uvicorn、pydantic、PyYAML、
  numpy 与 websockets。`pytest` 与 `httpx` 是测试依赖。本地 `packages/jcheng_core`
  提供共享的统计与点云原语，需单独以 editable 方式安装（不是 PyPI 依赖）。
- Node.js 20 与 npm；`npm ci` 安装锁定版本的前端工具链。
- 任何阶段都不需要 ROS：控制台是 HTTP/WS 客户端，不加入 DDS 图。
- 运行时需要操作员网络可达的 `jcheng-system` 网关；门禁本身不需要机器人，也不
  监听网络。只读 `/v1` 诊断使用 `ARMPI_GATEWAY_V1_URL` 与
  `ARMPI_GATEWAY_V1_TOKEN`（见 [GATEWAY_V1.md](docs/GATEWAY_V1.md)）；legacy
  适配器使用独立的 `ARMPI_GATEWAY_URL` / `ARMPI_GATEWAY_TOKEN`。
- 操作会话记录与导出存放在仓库外（`~/.local/state/armpi-studio/`）。M2a 存储默认
  为 `sessions.db`；可用 `ARMPI_SESSION_DB` / `ARMPI_SESSION_EXPORTS` 覆盖。
  网关凭据只放在 `~/.config/armpi-studio/studio.env`（0600），绝不进入 git。
- 生产 URDF 是厂商派生资产，本仓库不附带。视觉工作台需要时用
  `ARMPI_URDF_PATH` 指向只读副本；测试使用项目自建的运动学夹具。

## 快速开始

```bash
# 静态门禁、准入清单与文档集。
python3 -m pytest -q tests
bash scripts/check_repo_gate.sh

# 后端（mock 模式；无机器人、无网关）。
python3 -m venv backend/.venv
cd backend
.venv/bin/pip install -e '.[dev]'
.venv/bin/pip install -e ../packages/jcheng_core --no-deps
.venv/bin/python -m pytest tests
cd ..

# 前端构建。
cd frontend && npm ci && npm run build; cd ..

# 启动控制台（默认 mock）。
tools/studio/start_studio.sh
```

门禁检查文档集、准入清单、相对链接、密钥、会话数据库、操作员 env 文件、模型
权重、记录数据与测试模块。它不接触机器人、不打开设备、不加入 DDS 域。

## 验证状态

| 声明 | 状态 | 证据 |
|---|---|---|
| 仓库门禁、准入清单与文档集通过 | 已验证（L0，2026-11-26） | [`evidence/20261126_replay/`](evidence/20261126_replay/README.md) |
| 后端套件：191 passed | 已验证（L1，2026-11-26） | [`evidence/20261126_replay/backend-tests.txt`](evidence/20261126_replay/backend-tests.txt) |
| 只读 `/v1` REST 客户端（health、contract、state、safety） | 已验证（L2，mock 网关，2026-11-26） | [`backend/tests/test_gateway_v1.py`](backend/tests/test_gateway_v1.py)；[证据](evidence/20261126_gateway_v1/README.md) |
| 只读 `/v1/ws/state` 订阅 | 已验证（L2，mock 网关，2026-11-26） | [`backend/tests/test_gateway_v1_stream.py`](backend/tests/test_gateway_v1_stream.py)；[证据](evidence/20261126_gateway_ws/README.md) |
| 真实 `jcheng-system` 只读网关的 WS 联调 | 已验证（L2，loopback，2026-11-26） | [`evidence/20261126_gateway_ws/integration.txt`](evidence/20261126_gateway_ws/integration.txt) |
| M2a 会话生命周期、授权拒绝、封存/导出哈希与脱敏 | 已验证（L2，mock 网关，2026-11-26） | [`backend/tests/test_sessions.py`](backend/tests/test_sessions.py)；[证据](evidence/20261126_sessions/README.md) |
| M2b 关节/夹爪意图校验+执行、操作员提供 lease、不调用 `/v1/leases` | 已验证（L2，mock 网关，2026-11-26） | [`backend/tests/test_gateway_v1_intent.py`](backend/tests/test_gateway_v1_intent.py)；[证据](evidence/20261126_intent/README.md) |
| M3 离线回放：commanded 与 measured 分离；仅在记录 joint_order 时计算关节 Δ；导出带哈希 | 已验证（L2，离线，2026-11-26） | [`backend/tests/test_session_replay.py`](backend/tests/test_session_replay.py)；[证据](evidence/20261126_replay/README.md) |
| 前端生产构建通过 | 已验证（L0，2026-11-26） | [`evidence/20261126_replay/frontend-build.txt`](evidence/20261126_replay/frontend-build.txt) |
| 扫描范围内无设备路径、DDS/ROS 接口、厂商话题或运动调用 | 已验证（L0，2026-11-26） | `tests/test_no_hardware.py`；[门禁记录](evidence/20261126_replay/gate.txt) |
| Mock HTTP API 路径（预览、执行、外设、任务规划） | 已验证（L2，仅 mock，2026-11-26） | `backend/tests/` |
| Playwright 浏览器 E2E | 未验证 | 本次迁移未运行 |
| Legacy 网关集成（只读或运动） | 未验证 | 本仓库未实际调用 legacy 适配器 |
| 硬件验收（M4） | 未开始 | M4 路线图 |
| 实机运动 | 未验证 | 未授权任何会话；本仓库无硬件证据 |

完整报告见 [ACCEPTANCE.md](ACCEPTANCE.md)。

## 相关仓库

- [`jcheng-interfaces`](https://github.com/JCHENG-Robotics/jcheng-interfaces) — 网关 OpenAPI、WS 信封、消息 schema 与契约哈希。
- [`jcheng-system`](https://github.com/JCHENG-Robotics/jcheng-system) — 特权运行时；网关所有者与唯一运动起点。
- [`jcheng-perception`](https://github.com/JCHENG-Robotics/jcheng-perception) — 建议性感知目标，经网关呈现。
- [`jcheng-ops`](https://github.com/JCHENG-Robotics/jcheng-ops) — Studio 看门狗与无人值守健康检查，归本仓库之外。
- [`jcheng-manipulation`](https://github.com/JerichoCheng/jcheng-manipulation) — 冻结的 `v1.0.0` 基线，提供迁移的控制台，仍承载机器人侧网关与硬件证据。
- [`.github`](https://github.com/JCHENG-Robotics/.github) — 生态路线图、变更日志、验收与仓库分类法。

## 许可证

第三方与版权审查仍在进行；尚未添加 `LICENSE` 文件。迁移的控制台来自 MIT 许可
的冻结基线；厂商派生模型资产未复制。会话记录与导出证据有自己的处理规则，不受
本仓库最终许可证覆盖。见 [LICENSE-REVIEW.md](LICENSE-REVIEW.md)。
