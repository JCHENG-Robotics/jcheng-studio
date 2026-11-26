# jcheng-studio

> 状态：**实验性**，2026-11-26。版本 0.1.0。
> 仅准入骨架：本仓库固定了操作台边界与网关集成规则，但不包含后端、不包含界面，
> 也不包含运行时代码。运行中的机器人不会使用其中任何内容。
> 本文为英文主文档的中文对照；如有歧义，以 [README.md](README.md) 为准。

`jcheng-studio` 是 JCHENG 生态的 L4 操作台，也是 ArmPi Debug Studio 在生态中的
归属仓库。它是面向人的界面：连接与诊断、关节与夹爪调试、轨迹检查和 ArmPi Ultra
上的实验记录。它只通过 HTTP/WS 访问 `jcheng-system` 网关：不加入任何 DDS 域、
不打开设备、绝不自行执行运动。它提出的每个动作都由系统层校验并执行或拒绝。

## 范围与边界

| 项 | 本仓库 |
|---|---|
| 层 | L4 操作应用（操作台），见仓库分类 |
| 运行位置 | 操作员笔记本：浏览器界面与本地后端；绝不在机器人上运行 |
| 输入 | 网关 HTTP/WS 响应（`/v1/state`、`/v1/safety`、`/v1/contract`、事件）与操作员输入 |
| 输出 | 操作员会话记录与导出的证据，存放在本仓库之外；M1 起增加转发给系统网关的运动**意图** |
| 运动授权 | 无。控制租约、deadman、关节/底盘限幅、停止锁存与 `ExecuteMotion` 都属于系统层 |
| 硬件访问 | `none` |
| 拥有的设备 | 无 |
| 禁止 | 加入 DDS 域 0/41/42、设备访问、厂商话题、`rclpy`、厂商 SDK 调用、直接 `ExecuteMotion` 或 `/cmd_vel`、SSH 登录机器人、把令牌放进 git、伪造或插值机器人状态 |

系统层是运动的唯一来源，感知层是建议目标的唯一来源。操作台只显示网关报告的内容，
把命令值与测量值分开标注，绝不把厂商栈中的 `/joint_states`（命令缓存）当作测量。
操作台不是急停：平台没有急停、制动、编码器或自碰撞模型。

## 目录

```text
jcheng-studio/
├── capability.yaml                 # 准入清单（offline，hardware_access: none）
├── docs/
│   ├── ARCHITECTURE.md             # 控制台、后端、会话与网关平面
│   ├── INTEGRATION.md              # 网关契约、准入与失败语义
│   └── SESSIONS.md                 # 操作员会话、授权与记录
├── tests/
│   ├── test_contract.py            # 清单与文档集检查
│   └── test_no_hardware.py         # 无硬件 / 无 DDS / 无运动静态扫描
├── scripts/check_repo_gate.sh      # 仓库门禁（本地与 CI）
├── evidence/20261126_scaffold/     # M0 证据记录
├── VERSION
├── README.md / README.zh-CN.md
├── ROADMAP.md / CHANGELOG.md / ACCEPTANCE.md
└── LICENSE-REVIEW.md
```

## 依赖

- Python 3.10 或更新版本（CI 使用 3.12）、`pytest` 8.3.4、`PyYAML` 6.0.2。
- v0.1.0 不需要 ROS，任何阶段都不需要：操作台是 HTTP/WS 客户端，不加入 DDS 图。
- 任何运行时工作都需要操作员网络内可达的 `jcheng-system` 网关；门禁本身不需要
  机器人，也不监听网络。
- 会话记录与导出存放在本仓库之外（`~/.local/state/armpi-studio/`）。网关凭据放在
  `~/.config/armpi-studio/studio.env`（权限 0600），绝不进入 git。

## 快速开始

```bash
python3 -m pytest -q tests
bash scripts/check_repo_gate.sh
```

门禁检查文档集、准入清单、相对链接、密钥、会话数据库、操作员环境文件、模型权重、
录制数据与两个测试模块。它不接触机器人，不打开任何设备，也不加入任何 DDS 域。

## 验证状态

| 声明 | 状态 | 证据 |
|---|---|---|
| 仓库门禁与单元测试通过 | 已验证（L0/L1，2026-11-26） | `evidence/20261126_scaffold/` |
| 清单声明 `hardware_access: none` 且运行时表面为空 | 已验证（L0，2026-11-26） | `tests/test_contract.py` |
| 仓库中不存在硬件、DDS、厂商话题或运动调用 | 已验证（L0，2026-11-26） | `tests/test_no_hardware.py` |
| 网关客户端、控制台界面或操作员会话 | 未验证 | 尚未实现（M1–M3） |

完整报告：[ACCEPTANCE.md](ACCEPTANCE.md)。

## 相关仓库

- [`jcheng-interfaces`](https://github.com/JCHENG-Robotics/jcheng-interfaces) — 网关 OpenAPI、WS 信封、消息 Schema 与契约哈希。
- [`jcheng-system`](https://github.com/JCHENG-Robotics/jcheng-system) — 特权运行时；网关所有者，也是运动的唯一来源。
- [`jcheng-perception`](https://github.com/JCHENG-Robotics/jcheng-perception) — 建议感知目标，经网关呈现。
- [`jcheng-ops`](https://github.com/JCHENG-Robotics/jcheng-ops) — Studio 看门狗与无人值守健康检查，由该仓库拥有。
- [`jcheng-manipulation`](https://github.com/JerichoCheng/jcheng-manipulation) — 冻结的 `v1.0.0` 基线，当前 Debug Studio 代码所在处。
- [`.github`](https://github.com/JCHENG-Robotics/.github) — 生态路线图、变更日志、验收与仓库分类。

## 许可证

第三方与版权审查尚未完成；尚未添加 `LICENSE` 文件。会话记录与导出的证据有各自的
处理规则，永不被本仓库的最终许可证覆盖。见 [LICENSE-REVIEW.md](LICENSE-REVIEW.md)。
