# Changelog

All notable changes to this project are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.0] — 2026-11-26

### Added

- Read-only `/v1` gateway client (`backend/app/adapters/gateway_v1.py`):
  `GET /v1/health`, `/v1/contract`, `/v1/state` and `/v1/safety` over HTTP,
  Bearer authentication, contract-hash pinning and fail-closed diagnostics.
  It has no POST, lease, motion, arm, disarm or stop method.
- `GET /api/gateway/v1/status` and a "Gateway v1 · 只读诊断" card on the
  Diagnostics page.
- Nine mock-gateway tests covering unauthenticated health, Bearer enforcement,
  contract mismatch, missing token, unreachable gateway, the absence of
  mutation methods, the status route and hash/manifest agreement.
- `docs/GATEWAY_V1.md` and `evidence/20261126_gateway_v1/`.

### Changed

- Version 0.3.0. `capability.yaml` now declares `safety.class: observer`
  (read-only HTTP) with an empty scope list and `hardware_access: none`.
- Backend suite: 164 passed. The ported legacy adapter and runners remain
  transitional and disabled by default.

### Not included

- The `/v1` WebSocket subscription (M1b).
- Any `/v1` lease or motion intent; the console remains read-only.

## [0.2.0] — 2026-11-26

### Added

- Ported the frozen ArmPi Debug Studio console from
  `jcheng-manipulation` (`a08b8b6`, 2026-11-26 working tree):
  FastAPI backend, React/TypeScript UI, joint/takeover/visual configs, the
  2026-11-18 hand-eye calibration, operator runners and the G1–G8.1 records.
- Ported `packages/jcheng_core` (`jcheng_stats`, `jcheng_vision`), the shared
  pure-Python statistics and point-cloud primitives required by the
  experiment and task paths.
- Backend test surface: 154 passed; the mock, safety, pose and task
  paths run without ROS, a gateway or a device.
- Frontend production build (`tsc -b && vite build`).
- Project-authored kinematic fixture for the visual-workbench tests; the
  vendor-derived URDF is not shipped and is read only from `ARMPI_URDF_PATH`.
- Loopback-only console launcher (`tools/studio/start_studio.sh`) that stores
  its database and log under `~/.local/state/armpi-studio/`, reads a token only
  from an environment variable or a `0600` file, and never uses SSH or ROS.
- Migration record (`docs/MIGRATION.md`) and legacy document index
  (`docs/legacy/README.md`) with links to the baseline repository.
- CI jobs `console-tests` and `frontend-build`, alongside `studio-gate`.

### Changed

- Version 0.2.0. The admission manifest keeps `hardware_access: none`, an
  empty ROS runtime surface and the v0.1.0 contract hash.
- Vendor device and topic labels in mock/adapters were replaced with neutral
  `mock://` and `gateway-*` names; the no-hardware static scan covers the whole
  tree.
- The capstone README, Chinese README, roadmap and acceptance report now
  describe the ported console and its offline verification, not the
  admission-only scaffold.

### Not included

- `armpi-debug-studio/ros2_gateway/` — a ROS 2/DDS L2 gateway; it stays in the
  frozen baseline and is not copied into this L4 repository.
- `data/studio.db` — runtime event data; session databases stay outside git.
- Hardware evidence bundles and the vendor-derived URDF/xacro.
- Gateway-coupled converters (`build_g5_dataset.py`,
  `export_gateway_motion_config.py`); they import the legacy gateway and stay
  with it.

## [0.1.0] — 2026-11-26

### Added

- Initial repository scaffold: bilingual documentation set, admission manifest,
  repository gate, no-hardware/no-DDS static scan and `studio-gate` CI.
- Operator-console boundary for the ArmPi Debug Studio's ecosystem home:
  gateway HTTP/WS only, no DDS domain, no device access, no motion execution.
- Session, authorization and evidence rules for future operator sessions
  (`docs/SESSIONS.md`), proposed until M2.
- Organization documentation set: `README.md`, `README.zh-CN.md`, `ROADMAP.md`,
  `ACCEPTANCE.md` and this changelog.
