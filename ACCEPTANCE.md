# Acceptance report — jcheng-studio

> Status: **pass** as of 2026-11-26 for v0.8.0 (console port + M1a REST +
> M1b WebSocket + M2a sessions + M2b intent + M3 replay + M5 teleop panel),
> with the 2026-11-27 authority-hardening pass (legacy motion default-disabled;
> System-only intent and stop; local lease reduced to a UI concurrency lock).
> Operator: JerichoCheng. Reviewer: pending.
> Scope: the ported backend and UI, the read-only `/v1` REST and WebSocket
> clients, the M2a session store, the M2b intent path, the M3 offline replay and
> the M5 teleop panel (read-only status + opt-in system stop proxy), the
> offline/mock test surface and the static boundary. No hardware session
> was run, no device was opened and no DDS domain was joined.
> The report is additive: the v0.1.0 scaffold acceptance remains in
> `evidence/20261126_scaffold/`.

## 1. Gates

| Gate | Command | Level | Result | Evidence |
|---|---|---|---|---|
| Repository gate | `bash scripts/check_repo_gate.sh` | L0 | pass | `evidence/20261127_capabilities/gate.txt` |
| Unit tests (manifest, document set, no-hardware scan) | `python3 -m pytest -q tests` | L1 | pass (13 tests) | `tests/` |
| Backend suite | `backend/.venv/bin/python -m pytest tests` | L1 | pass (220 passed) | `evidence/20261127_capabilities/backend-tests.txt` |
| Frontend production build | `npm run build` in `frontend/` | L0 | pass | `evidence/20261127_capabilities/frontend-build.txt` |
| Mock HTTP API | covered by the backend suite | L2 | pass (mock only) | `backend/tests/` |
| Mock `/v1` REST gateway | `backend/tests/test_gateway_v1.py` | L2 | pass (10 tests) | `evidence/20261126_gateway_v1/` |
| Mock `/v1/ws/state` stream | `backend/tests/test_gateway_v1_stream.py` | L2 | pass (8 tests) | `evidence/20261126_gateway_ws/` |
| Real `jcheng-system` read-only gateway WS | loopback integration probe | L2 | pass (hello → state → safety) | `evidence/20261126_gateway_ws/integration.txt` |
| M2a session lifecycle, authorization gate, seal/export | `backend/tests/test_sessions.py` | L2 | pass (7 tests) | `evidence/20261126_sessions/` |
| M2b joint/gripper intent validate + execute | `backend/tests/test_gateway_v1_intent.py` | L2 | pass (8 tests) | `evidence/20261126_intent/` |
| M3 offline replay and hashed analysis export | `backend/tests/test_session_replay.py` | L2 | pass (4 tests) | `evidence/20261126_replay/` |
| M5 teleop panel status + system stop proxy | `backend/tests/test_teleop_panel.py` | L2 | pass (10 tests; mock gateway run) | `evidence/20261126_teleop_panel/` |
| Capability registry (19 fixed rows) | `backend/tests/test_capabilities.py` | L2 | pass (6 tests; hardware read-only + demo probes) | `evidence/20261127_capabilities/` |
| Offline demo mode (`--demo`) | `backend/tests/test_capabilities.py` | L1 | pass; demo requires mock and never executes motion | `evidence/20261127_capabilities/demo-capabilities.json` |
| Legacy authority hardening | `backend/tests/test_legacy_motion_disabled.py` | L1/L2 | pass (13 tests; mock/loopback only) | `backend/tests/test_legacy_motion_disabled.py` |
| Playwright browser E2E | `npm run test:e2e` | L3 | not run in this port | — |
| Legacy gateway integration | — | L2/L3 | not exercised | — |
| Hardware | — | L5 | not applicable; no session authorized | — |
| CI | `.github/workflows/ci.yml` jobs `studio-gate`, `console-tests`, `frontend-build` | L0/L1 | unverified until the first run | — |

## 2. Verified claims

| Claim | Level | Evidence |
|---|---|---|
| The manifest parses, matches `VERSION`, validates against the v1 schema and pins the v0.1.0 contract hash | L0 | `capability.yaml`; `tests/test_contract.py` |
| `hardware_access: none`, `safety.class: observer` with `arm`/`gripper` intent scopes and an empty ROS runtime surface are declared | L0 | `capability.yaml`; `tests/test_contract.py` |
| No device path, DDS/ROS interface, vendor topic, motion service or base-intent topic appears in scanned code, config or scripts | L0 | `tests/test_no_hardware.py` |
| No model weight, session database, operator env file or oversized file is tracked | L0 | `scripts/check_repo_gate.sh` |
| The required document set exists and relative links resolve | L0 | `scripts/check_repo_gate.sh` |
| The ported backend's mock, safety, pose and task paths pass | L1/L2 | 191 passed |
| The ported frontend type-checks and builds | L0 | `tsc -b && vite build` |
| The read-only `/v1` client implements health, contract, state and safety and has no mutation method | L1/L2 | `backend/app/adapters/gateway_v1.py`; `backend/tests/test_gateway_v1.py` |
| A mock gateway proves unauthenticated health, Bearer enforcement, contract-mismatch rejection, missing-token and unreachable handling | L2 | `backend/tests/test_gateway_v1.py` |
| Every recorded request in the read-only suite is a `GET` | L2 | `backend/tests/test_gateway_v1.py` |
| The read-only `/v1/ws/state` client validates `jcheng-ws.v1.json` envelopes, requires hello first, has bounded reconnect and no mutation method | L1/L2 | `backend/app/adapters/gateway_v1_stream.py`; `backend/tests/test_gateway_v1_stream.py` |
| The stream probe requires Bearer, rejects a contract mismatch and an invalid first envelope, and reports an unreachable gateway without fabricating state | L2 | `backend/tests/test_gateway_v1_stream.py` |
| The studio client reads hello → state → safety from the real `jcheng-system` read-only gateway | L2 | `evidence/20261126_gateway_ws/integration.txt` |
| A session can be opened, authorized, degraded, closed and exported with a matching record hash | L2 | `backend/tests/test_sessions.py` |
| Authorization is refused (and the session degraded) when the gateway status is not ready or the contract does not match | L2 | `backend/tests/test_sessions.py` |
| A tampered session record is rejected on export; the token and URL credentials are not stored | L2 | `backend/tests/test_sessions.py` |
| A backend restart closes a session left active | L2 | `backend/tests/test_sessions.py` |
| An authorized session is required before any intent; the intent carries an operator-supplied system lease | L2 | `backend/tests/test_gateway_v1_intent.py` |
| The intent client validates the command and submits it to `/v1/motion/validate` and `/v1/motion/execute`; it never calls `/v1/leases` and has no stop method | L2 | `backend/tests/test_gateway_v1_intent.py` |
| A validation rejection records `intent_rejected`; an UNKNOWN execution records `intent_unknown` and degrades the session | L2 | `backend/tests/test_gateway_v1_intent.py` |
| The session/intent path has no device or DDS call; the Studio never becomes a lease holder | L0/L2 | `backend/app/sessions/`, `backend/app/adapters/gateway_v1_intent.py`; `tests/test_no_hardware.py` |
| M3 replay keeps `commanded` targets and gateway `measured` positions in separate fields; a delta is computed only for a COMPLETED joint step with a recorded joint order | L2 | `backend/tests/test_session_replay.py` |
| M3 replay exports are self-describing (studio version/commit, contract hash, calibration) and carry a verifiable analysis hash | L2 | `backend/tests/test_session_replay.py`; `evidence/20261126_replay/` |
| M3 replay is offline: no gateway, robot or device is contacted | L0/L2 | `backend/app/analysis/`; `tests/test_session_replay.py` |
| M5 status proxy reports a missing/unreachable/invalid/>1 s input as degraded, never ready | L2 | `backend/tests/test_teleop_panel.py`; `docs/TELEOP_PANEL.md` |
| M5 stop goes through the system `POST /v1/motion/stop` (Bearer server-side), latches `STOP_LATCHED`, and uses no Studio lease or local motion adapter | L2 | `evidence/20261126_teleop_panel/` |
| M5 status and stop URLs are loopback-only; the browser never holds the gateway token | L0/L2 | `backend/app/adapters/loopback.py`; `backend/tests/test_teleop_panel.py` |
| The ported configs resolve without the baseline workspace | L0 | `configs/takeover.yaml`; `configs/calibration/` |
| The vendor-derived URDF is not shipped; the visual tests use a project fixture | L0 | `backend/tests/fixtures/armpi_ultra_test.urdf`; `docs/MIGRATION.md` |
| `POST /api/motion/execute`, `/api/chassis/pulse` and `/api/motion/stop` answer `410 Gone` by default and never call the adapter | L2 | `backend/tests/test_legacy_motion_disabled.py` |
| The legacy dispatch/chassis/stop paths are hard-blocked in `MotionOperations` and `HardwareAdapter`; no default module names `/motion/dispatch`; legacy motion is enabled only by the `ARMPI_MODE=legacy` + `ARMPI_ENABLE_LEGACY_MOTION=1` double switch | L1/L2 | `backend/app/legacy_motion.py`; `backend/tests/test_legacy_motion_disabled.py` |
| The legacy double-switch mode is operator-only, not system-authorized (the frozen gateway cannot validate a System lease); an empty/whitespace lease is rejected with `400` and never dispatched, and the path is scheduled for removal under boundary waiver `B4 studio legacy_motion.py` | L1/L2 | `backend/tests/test_legacy_motion_disabled.py`; `docs/INTEGRATION.md#legacy-motion-deprecation` |
| Intent is refused outside hardware mode (`409`) and without `ARMPI_SYSTEM_INTENT=1` (`403`); the only stop path is the system `/v1/motion/stop` proxy | L2 | `backend/tests/test_legacy_motion_disabled.py`; `backend/tests/test_teleop_panel.py` |
| The local `/api/control/*` lock is a UI concurrency lock (`motion_authorization: false`); motion ops contain no `self.leases.check` and the safety report marks the check advisory | L1/L2 | `backend/tests/test_legacy_motion_disabled.py`; `backend/app/safety/lease.py` |
| A mock process cannot call a real system: `Settings.from_env` clears the `/v1` URL/token, disables the system stop and records a startup warning | L1 | `backend/tests/test_legacy_motion_disabled.py` |

## 3. Not verified

| Item | Reason | Risk |
|---|---|---|
| M2b intent against a real gateway or robot | Mock validate/execute only | Real safety, lease and bus behavior is not exercised |
| Gripper measured position in replay | `MotionResult` carries arm positions only | No gripper delta can be computed from this record |
| Playwright browser E2E | Not run in this port | UI regressions are not yet caught in CI |
| Teleop panel browser E2E and a real teleop process | Backend routes tested; panel is build-verified; the live run used the demo harness | UI/staleness rendering is not browser-tested |
| Legacy gateway integration | No gateway in this repository | The ported hardware path is unproven here |
| Legacy motion under the double switch | Mock adapter only in this pass; the frozen gateway is not in this repository | The deprecated transport is unproven here and is not a supported default |
| Lease acquisition/ownership by the Studio | By design it acquires no motion lease; `/api/control/*` is a UI concurrency lock and the operator supplies the System lease | No end-to-end lease UI in this console yet |
| Hardware behavior | No session, no runtime | Nothing to validate on the robot |

## 4. Limitations

- The repository contains runtime console code, but no robot-side gateway.
  The frozen `ros2_gateway` remains in `jcheng-manipulation` and is not part of
  this repository.
- The `/v1` REST and WebSocket clients are read-only. M2b forwards one
  joint/gripper intent through `/v1/motion/validate` and `/v1/motion/execute`,
  but the operator supplies a system-issued lease and the Studio never
  acquires, renews, releases or holds one. The stream is a short probe plus a
  bounded-reconnect reader; it does not persist a live session.
- The legacy HTTP adapter (`/health`, `X-Armpi-Token`) is retained only to run
  the ported console against an already-deployed frozen gateway. It is not a
  compatibility shim for the `/v1` contract and is not admitted. Its motion
  dispatch/chassis/stop path is default-disabled, answers `410 Gone` at the
  HTTP surface, is hard-blocked in the service/adapter, and is reachable only
  through the deprecated `ARMPI_MODE=legacy` + `ARMPI_ENABLE_LEGACY_MOTION=1`
  switch. That mode is **operator-only, not system-authorized**: the frozen
  gateway has no lease validator, so a non-empty `lease_id` is accepted as an
  opaque operator reference (not a safety or authorization check), and an
  empty/whitespace lease is rejected with `400` before dispatch. It is not a
  safe or normal-operation path and is scheduled for removal under boundary
  waiver `B4 studio legacy_motion.py`. The classes were not deleted or moved in
  this pass.
- The local `/api/control/*` lease is a UI concurrency lock, not a motion
  authorization; motion requests only validate and forward the operator's
  System-issued `lease_id`.
- The production URDF is vendor-derived and is not shipped. The visual
  workbench needs `ARMPI_URDF_PATH`; tests use a project-authored fixture.
- The M2a session store is separate from the ported legacy SQLite event log;
  both live outside git. M3 replay reads the session record only: `measured`
  values are the gateway MotionResult array, not a fresh bus read, and the
  gripper has no measured position in that result.
- No motion authority and no e-stop. The platform has no e-stop, brake, encoder
  or self-collision model; a software stop is not an e-stop.
- `/joint_states` is a command cache, not a measurement; any future session
  that needs pose truth must carry the system layer's bus read.
- The Studio cannot acquire a gateway token over SSH and must not be restarted
  just because the robot is unreachable.
- Synthetic or mock results are never reported as hardware validation.
- The reviewer sign-off is pending; this report is not an organization-wide
  acceptance.

## 5. Sign-off

| Role | Name | Date |
|---|---|---|
| Operator | JerichoCheng | 2026-11-26 |
| Reviewer | pending | — |

## 6. Machine-readable report

None. When produced, reports must validate against
`jcheng-interfaces/schemas/acceptance-report.v1.schema.json`.
