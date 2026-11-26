# Acceptance report — jcheng-studio

> Status: **pass** as of 2026-11-26 for the v0.2.0 console port.
> Operator: JerichoCheng. Reviewer: pending.
> Scope: the ported backend and UI, the offline/mock test surface and the
> static boundary. No hardware session was run, no device was opened and no
> DDS domain was joined.
> The report is additive: the v0.1.0 scaffold acceptance remains in
> `evidence/20261126_scaffold/`.

## 1. Gates

| Gate | Command | Level | Result | Evidence |
|---|---|---|---|---|
| Repository gate | `bash scripts/check_repo_gate.sh` | L0 | pass | `evidence/20261126_console_port/gate.txt` |
| Unit tests (manifest, document set, no-hardware scan) | `python3 -m pytest -q tests` | L1 | pass (13 tests) | `tests/` |
| Backend suite | `backend/.venv/bin/python -m pytest tests` | L1 | pass (154 passed) | `evidence/20261126_console_port/backend-tests.txt` |
| Frontend production build | `npm run build` in `frontend/` | L0 | pass | `evidence/20261126_console_port/frontend-build.txt` |
| Mock HTTP API | covered by the backend suite | L2 | pass (mock only) | `backend/tests/` |
| Playwright browser E2E | `npm run test:e2e` | L3 | not run in this port | — |
| Legacy gateway integration | — | L2/L3 | not exercised | — |
| Hardware | — | L5 | not applicable; no session authorized | — |
| CI | `.github/workflows/ci.yml` jobs `studio-gate`, `console-tests`, `frontend-build` | L0/L1 | unverified until the first run | — |

## 2. Verified claims

| Claim | Level | Evidence |
|---|---|---|
| The manifest parses, matches `VERSION`, validates against the v1 schema and pins the v0.1.0 contract hash | L0 | `capability.yaml`; `tests/test_contract.py` |
| `hardware_access: none` and an empty runtime surface are declared | L0 | `capability.yaml`; `tests/test_contract.py` |
| No device path, DDS/ROS interface, vendor topic, motion service or base-intent topic appears in scanned code, config or scripts | L0 | `tests/test_no_hardware.py` |
| No model weight, session database, operator env file or oversized file is tracked | L0 | `scripts/check_repo_gate.sh` |
| The required document set exists and relative links resolve | L0 | `scripts/check_repo_gate.sh` |
| The ported backend's mock, safety, pose and task paths pass | L1/L2 | 154 passed |
| The ported frontend type-checks and builds | L0 | `tsc -b && vite build` |
| The ported configs resolve without the baseline workspace | L0 | `configs/takeover.yaml`; `configs/calibration/` |
| The vendor-derived URDF is not shipped; the visual tests use a project fixture | L0 | `backend/tests/fixtures/armpi_ultra_test.urdf`; `docs/MIGRATION.md` |

## 3. Not verified

| Item | Reason | Risk |
|---|---|---|
| `/v1` gateway client and read-only diagnostics | Not implemented (M1) | No ecosystem gateway contract is exercised |
| Operator session store and authorization gate | Not implemented (M2) | No session can be opened, degraded or sealed |
| Console `/v1` wiring and joint/gripper debug | The ported UI uses the legacy adapter | The legacy transport is not the ecosystem contract |
| Trajectory inspection and evidence export | No M2 session record | No record can be replayed or exported |
| Playwright browser E2E | Not run in this port | UI regressions are not yet caught in CI |
| Legacy gateway integration | No gateway in this repository | The ported hardware path is unproven here |
| Lease, deadman and fail-closed behavior | No `/v1` client | Cannot be tested before M1/M2 |
| Hardware behavior | No session, no runtime | Nothing to validate on the robot |

## 4. Limitations

- The repository now contains runtime console code, but no robot-side gateway.
  The frozen `ros2_gateway` remains in `jcheng-manipulation` and is not part of
  this repository.
- The legacy HTTP adapter (`/health`, `X-Armpi-Token`) is retained only to run
  the ported console against an already-deployed frozen gateway. It is not a
  compatibility shim for the `/v1` contract.
- The production URDF is vendor-derived and is not shipped. The visual
  workbench needs `ARMPI_URDF_PATH`; tests use a project-authored fixture.
- The ported SQLite event store is a legacy console log, not the M2 session
  store. Session databases remain outside git.
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
