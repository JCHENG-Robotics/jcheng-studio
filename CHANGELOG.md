# Changelog

All notable changes to this project are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.6.0] — 2026-11-26

### Added

- M2b intent client (`backend/app/adapters/gateway_v1_intent.py`): one
  `JOINT_STEP` or `GRIPPER` command through `/v1/motion/validate` and
  `/v1/motion/execute`. It has no lease acquisition/release and no stop method.
- `POST /api/session/intent/joint` and `/api/session/intent/gripper`, gated by
  an authorized M2a session and an **operator-supplied system lease**.
- Diagnostics intent panel: lease ID, joint/gripper target, validate + execute,
  and validation/result display.
- Intent events in the session record: `intent_requested`, `intent_rejected`,
  `intent_executed`, `intent_unknown`; actions are populated from them.
- Eight intent tests: unauthorized refusal, joint/gripper payloads,
  validation rejection, missing lease, UNKNOWN degradation, missing gateway and
  no lease/stop methods.
- `evidence/20261126_intent/`.

### Changed

- Version 0.6.0. Backend suite: 187 passed.
- `capability.yaml` declares `scopes: ["arm", "gripper"]` as **intent scopes**;
  `class: observer` and `requires_lease: true` remain. The system layer owns
  the lease and the only motion entry.
- The Studio never calls `/v1/leases`; the operator obtains the lease from the
  system layer and pastes it into the console.

### Not included

- A lease-acquisition UI or a Studio-held lease.
- Hardware validation; the intent path is tested against a mock gateway only.

## [0.5.0] — 2026-11-26

### Added

- M2a operator-session store (`backend/app/sessions/store.py`): SQLite outside
  the repository, append-only events, one active session, a SHA-256 seal and a
  self-describing JSON export.
- Authorization gate over the read-only `/v1` status: configured, reachable,
  authenticated, contract-matching, fresh and not stop-latched; a failed check
  degrades the session and refuses authorization.
- Session API: `POST /api/session/open`, `/authorize`, `/degrade`, `/close`;
  `GET /current`, `/list`, `/{id}`, `/{id}/export`.
- Diagnostics session card: open, authorize, degrade, close/seal and export.
- Seven session tests: lifecycle, conflict, authorization refusal, seal/export,
  tamper detection, token/URL redaction and restart-close.
- `evidence/20261126_sessions/`.

### Changed

- Version 0.5.0. Backend suite: 179 passed.
- `docs/SESSIONS.md` is frozen at `schema_version: 1` with the M2a storage and
  API choices recorded.
- A backend restart closes a session left active (first session access).

### Not included

- M2b joint/gripper intent through the `/v1` lease/validate/execute path.
- Any lease or motion authority; the Studio still never holds one.

## [0.4.0] — 2026-11-26

### Added

- Read-only `/v1/ws/state` WebSocket client
  (`backend/app/adapters/gateway_v1_stream.py`) using the `websockets` sync API:
  one subscription receives `hello` then `state`/`safety` envelopes, validates
  `jcheng-ws.v1.json`, requires `hello` first and reconnects with bounded
  backoff. No POST, lease, motion or stop method.
- `GET /api/gateway/v1/stream` and a "流探测" action on the Diagnostics
  "Gateway v1" card.
- Eight mock-WebSocket tests and a loopback interop run against the real
  `jcheng-system` read-only gateway (`c036fe4`), which now serves
  `/v1/ws/state`.
- `evidence/20261126_gateway_ws/`.

### Changed

- Version 0.4.0. Backend suite: 172 passed.
- `websockets==17.4` is an explicit backend dependency.

### Not included

- Persistent session recording of the stream; the probe is deliberately short.
- Lease, motion intent or any write path; the console remains read-only.

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
