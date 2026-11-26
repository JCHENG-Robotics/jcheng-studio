# Acceptance report — jcheng-studio

> Status: **pass** as of 2026-11-26 for v0.5.0 (console port + M1a REST +
> M1b WebSocket + M2a operator sessions).
> Operator: JerichoCheng. Reviewer: pending.
> Scope: the ported backend and UI, the read-only `/v1` REST and WebSocket
> clients, the M2a session store and authorization gate, the offline/mock test
> surface and the static boundary. No hardware session was run, no device was
> opened and no DDS domain was joined.
> The report is additive: the v0.1.0 scaffold acceptance remains in
> `evidence/20261126_scaffold/`.

## 1. Gates

| Gate | Command | Level | Result | Evidence |
|---|---|---|---|---|
| Repository gate | `bash scripts/check_repo_gate.sh` | L0 | pass | `evidence/20261126_sessions/gate.txt` |
| Unit tests (manifest, document set, no-hardware scan) | `python3 -m pytest -q tests` | L1 | pass (13 tests) | `tests/` |
| Backend suite | `backend/.venv/bin/python -m pytest tests` | L1 | pass (179 passed) | `evidence/20261126_sessions/backend-tests.txt` |
| Frontend production build | `npm run build` in `frontend/` | L0 | pass | `evidence/20261126_sessions/frontend-build.txt` |
| Mock HTTP API | covered by the backend suite | L2 | pass (mock only) | `backend/tests/` |
| Mock `/v1` REST gateway | `backend/tests/test_gateway_v1.py` | L2 | pass (10 tests) | `evidence/20261126_gateway_v1/` |
| Mock `/v1/ws/state` stream | `backend/tests/test_gateway_v1_stream.py` | L2 | pass (8 tests) | `evidence/20261126_gateway_ws/` |
| Real `jcheng-system` read-only gateway WS | loopback integration probe | L2 | pass (hello → state → safety) | `evidence/20261126_gateway_ws/integration.txt` |
| M2a session lifecycle, authorization gate, seal/export | `backend/tests/test_sessions.py` | L2 | pass (7 tests) | `evidence/20261126_sessions/` |
| Playwright browser E2E | `npm run test:e2e` | L3 | not run in this port | — |
| Legacy gateway integration | — | L2/L3 | not exercised | — |
| Hardware | — | L5 | not applicable; no session authorized | — |
| CI | `.github/workflows/ci.yml` jobs `studio-gate`, `console-tests`, `frontend-build` | L0/L1 | unverified until the first run | — |

## 2. Verified claims

| Claim | Level | Evidence |
|---|---|---|
| The manifest parses, matches `VERSION`, validates against the v1 schema and pins the v0.1.0 contract hash | L0 | `capability.yaml`; `tests/test_contract.py` |
| `hardware_access: none`, `safety.class: observer` and an empty ROS runtime surface are declared | L0 | `capability.yaml`; `tests/test_contract.py` |
| No device path, DDS/ROS interface, vendor topic, motion service or base-intent topic appears in scanned code, config or scripts | L0 | `tests/test_no_hardware.py` |
| No model weight, session database, operator env file or oversized file is tracked | L0 | `scripts/check_repo_gate.sh` |
| The required document set exists and relative links resolve | L0 | `scripts/check_repo_gate.sh` |
| The ported backend's mock, safety, pose and task paths pass | L1/L2 | 179 passed |
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
| The session path has no lease, motion or device call | L0/L2 | `backend/app/sessions/`; `tests/test_no_hardware.py` |
| The ported configs resolve without the baseline workspace | L0 | `configs/takeover.yaml`; `configs/calibration/` |
| The vendor-derived URDF is not shipped; the visual tests use a project fixture | L0 | `backend/tests/fixtures/armpi_ultra_test.urdf`; `docs/MIGRATION.md` |

## 3. Not verified

| Item | Reason | Risk |
|---|---|---|
| M2b console `/v1` joint/gripper intent | Not implemented | No `/v1` motion intent path exists |
| Trajectory inspection and evidence export | No M3 replay from session records | No session can be replayed beyond the raw record |
| Playwright browser E2E | Not run in this port | UI regressions are not yet caught in CI |
| Legacy gateway integration | No gateway in this repository | The ported hardware path is unproven here |
| Lease, deadman and fail-closed behavior on the real gateway | No `/v1` write path | The system layer's behavior is not exercised from the console |
| Hardware behavior | No session, no runtime | Nothing to validate on the robot |

## 4. Limitations

- The repository contains runtime console code, but no robot-side gateway.
  The frozen `ros2_gateway` remains in `jcheng-manipulation` and is not part of
  this repository.
- The `/v1` REST and WebSocket clients are read-only, and the M2a session path
  has no motion. They never acquire a lease, renew one, send a motion intent or
  call a stop endpoint. The stream is a short probe plus a bounded-reconnect
  reader; it does not persist a live session.
- The legacy HTTP adapter (`/health`, `X-Armpi-Token`) is retained only to run
  the ported console against an already-deployed frozen gateway. It is not a
  compatibility shim for the `/v1` contract and is not admitted.
- The production URDF is vendor-derived and is not shipped. The visual
  workbench needs `ARMPI_URDF_PATH`; tests use a project-authored fixture.
- The M2a session store is separate from the ported legacy SQLite event log;
  both live outside git. M2a does not yet forward joint/gripper intent.
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
