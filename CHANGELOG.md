# Changelog

All notable changes to this project are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Always-visible capability registry: `GET /api/capabilities` and the
  `/capabilities` page list 19 pages/integrations/data sources with a live
  status (`available`, `read_only`, `degraded`, `not_configured`, `blocked`,
  `offline`). The list shape never changes when a dependency is missing, so an
  operator can always see what the console can do and why a feature is not
  ready.
- Offline demo mode: `tools/studio/start_studio.sh --demo` / `ARMPI_DEMO=1`
  runs the mock adapter with an explicit DEMO banner and an `/api/health`
  `demo` flag. It requires mock mode and never executes motion.
- The capability page falls back to a static catalog when the backend is
  unreachable, so every surface stays visible even with no API.
- `ARMPI_ENABLE_LEGACY_MOTION` (default `false`) and `ARMPI_SYSTEM_INTENT`
  (default `false`), plus `ARMPI_MODE=legacy` validation. Legacy motion needs
  both `ARMPI_MODE=legacy` and `ARMPI_ENABLE_LEGACY_MOTION=1`; system intent
  needs hardware mode plus the opt-in. `Settings.startup_warnings` records
  non-fatal startup findings.
- `backend/app/legacy_motion.py` isolates the deprecated gateway paths and the
  410 response factory; `backend/app/system_intent.py` holds the shared
  hardware/opt-in intent gate.
- `backend/tests/test_legacy_motion_disabled.py`: 12 tests covering the 410
  defaults, the no-adapter guarantee, intent refusal, the system stop proxy,
  the double switch, the mock/v1 guard, and a static scan that no default
  module names `/motion/dispatch`.

### Changed

- **Legacy motion is default-disabled.** `POST /api/motion/execute`,
  `POST /api/chassis/pulse` and `POST /api/motion/stop` answer `410 Gone`
  (`LEGACY_MOTION_DISABLED`) with a migration hint unless the deprecated
  double switch is set. The block is enforced again in `MotionOperations` and
  `HardwareAdapter`, so direct service/adapter calls are refused too. This is
  default-disable and isolation, not deletion.
- **The Studio has no independent motion lease authority.** The local
  `LeaseManager` is a UI concurrency lock only: `POST /api/control/acquire`
  reports `authority: "ui_concurrency_lock"` and
  `motion_authorization: false`, and motion never checks it. The safety report
  shows the same check as advisory (`blocking: false`). Motion requests only
  validate and forward the operator's System-issued `lease_id`; the system
  layer owns the check.
- **The deprecated legacy mode is operator-only, not system-authorized.** The
  frozen gateway has no lease validator, so a non-empty `lease_id` is accepted
  as an opaque operator reference and only recorded; it is not a safety or
  authorization guarantee and must not be described as safe or used for normal
  robot operation. An empty or whitespace-only `lease_id` on the legacy
  execute/chassis endpoints is now rejected locally with HTTP `400`
  (`BAD_REQUEST`) before any adapter call (previously the schema returned 422),
  and no `Dispatched` event is written. The path is scheduled for removal and
  tracked by the formal boundary waiver `B4 studio legacy_motion.py` (expiry +
  removal owner).
- `/api/session/intent/joint|gripper` are hardware-only and opt-in. Outside
  `ARMPI_MODE=hardware` they answer `409 SYSTEM_INTENT_REQUIRES_HARDWARE`; with
  `ARMPI_SYSTEM_INTENT` unset they answer `403 SYSTEM_INTENT_DISABLED`. The
  single stop remains `POST /api/teleop/stop` → system `/v1/motion/stop`.
- A mock process can no longer reach a real system gateway: `ARMPI_MODE=mock`
  with `ARMPI_GATEWAY_V1_URL`/`ARMPI_GATEWAY_V1_TOKEN` clears both, disables
  the system stop and logs a warning at startup.
- `/api/capabilities` tells the truth about the new default: in mock/hardware
  mode the joints/task/motion rows are `read_only` because legacy motion is
  disabled, instead of claiming a complete mock execution surface.
- `/api/health` reports `demo`; backend suite is now 222 tests. Mode-dependent
  sections stay rendered: mock fault injection and the `/v1` diagnostics card
  remain visible in hardware mode with a disabled state and a reason.

### Tests

- The existing suites were updated to the new behavior, not weakened.
  `test_api.py` explicitly opts into the deprecated runner and asserts that the
  operator lease is recorded; the original `GATE_REJECTED` refusal is retained
  by `test_hardware_mode_gate_still_rejects_with_legacy_enabled` and by
  `test_hardware_motion.py`. `test_capabilities.py` changes `available` to
  `read_only` and adds a detail assertion. The task/pose/hardware tests only
  opt into the deprecated runner; their original assertions are unchanged.
  No safety, refusal or dispatch assertion was removed.
- New coverage: `test_legacy_empty_lease_is_rejected_and_never_dispatched`
  proves an empty/whitespace legacy lease is rejected with 400/403, the adapter
  is never called, and no `Dispatched` event exists. The default-disabled,
  intent-gate, system-stop, double-switch, mock/v1-guard and static
  `/motion/dispatch` assertions remain in
  `backend/tests/test_legacy_motion_disabled.py`.

### Deprecated

- The frozen pre-ecosystem motion transport (`/motion/dispatch`,
  `/chassis/pulse`, legacy `/motion/stop`) and the three legacy HTTP
  endpoints. They remain for the migration window only, are default-disabled,
  and must not be used by new features or by `capability.yaml`.

### Not included

- No legacy class, module or endpoint was deleted or moved in this pass; only
  the default path, the empty-lease rejection, the authority and the
  documentation changed.
- The formal `.github/boundary-waivers.yaml` entry (`B4 studio legacy_motion.py`,
  expiry + removal owner) is added by the QA follow-up. This pass keeps the code
  default-disabled and does not move it.
- No system, mobile or interface change; no hardware session; no robot
  connection.

## [0.8.0] — 2026-11-26

### Added

- Teleop panel (ecosystem M5) at `/teleop`: polls the read-only
  `jcheng.teleop.status.v1` payload through `GET /api/teleop/status` and shows
  phase, input age, deadman, mode, profile/contract hashes, hashed lease ref
  and capture counters. A missing, unreachable, invalid or >1 s old sample is
  rendered **degraded**, never ready.
- The Studio's single system write: `POST /api/teleop/stop` proxies
  `POST /v1/motion/stop` on the system gateway (Bearer token held by the
  backend). It latches the system software stop, requires no Studio lease and
  never calls the local motion adapter. It is opt-in via `ARMPI_SYSTEM_STOP=1`
  and loopback-only; both URLs are validated as loopback.
- `backend/app/adapters/teleop_status.py`, `system_stop.py`, `loopback.py`,
  `application/teleop_ops.py`, `backend/tests/test_teleop_panel.py` (10 tests),
  `docs/TELEOP_PANEL.md`.
- `frontend/vitest.config.ts` keeps Playwright `e2e/*.spec.ts` out of
  `npm test`; previously `vitest run` tried to collect them and failed.

### Changed

- Version 0.8.0. Backend tests: 201 passed; frontend `tsc -b && vite build`
  and `npm test` pass (no unit suites).
- `docs/GATEWAY_V1.md` distinguishes the read-only `/v1` intent client (still
  no lease/stop) from the dedicated system-stop proxy added for the panel.

### Not included

- Browser Playwright E2E for the panel and a live gateway run in CI.
- Any Studio lease acquisition, motion execution or hardware session.

## [0.7.0] — 2026-11-26

### Added

- M3 offline session replay (`backend/app/analysis/session_replay.py`): a
  provenance-labelled analysis of one M2a session record and its M2b intent
  events.
- Commanded and gateway-reported measured values are separate fields; a joint
  delta is computed only for a COMPLETED step and only when the session
  recorded `joint_order` in calibration. Gripper measured position is marked
  unavailable instead of guessed.
- `GET /api/session/{id}/analysis` and `/analysis/export`; the export carries
  studio version/commit, contract hash and calibration, and an analysis hash.
- Diagnostics “会话回放 · M3” card: choose a session, analyze, inspect the
  `commanded`/`measured` table and export the hashed analysis.
- New sessions record `joint_order`/`gripper_joint` in calibration so a later
  replay is self-contained.
- `backend/tests/test_session_replay.py` (4 tests); `evidence/20261126_replay/`.

### Changed

- Version 0.7.0. Backend suite: 191 passed.
- M3 replay is offline: no gateway, robot or device is contacted.

### Not included

- A bus re-read by the Studio; `measured` remains the gateway MotionResult.
- A gripper measured delta; the result does not carry it.
- Hardware acceptance (M4).

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
