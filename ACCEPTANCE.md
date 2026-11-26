# Acceptance report — jcheng-studio

> Status: **pass** as of 2026-11-26 for v0.1.0.
> Operator: JerichoCheng. Reviewer: pending.
> Scope: repository scaffold, documentation set and admission manifest.
> No backend, console UI or gateway call exists. No motion command is sent, no
> device is opened and no DDS domain is joined.

## 1. Gates

| Gate | Command | Level | Result | Evidence |
|---|---|---|---|---|
| Repository gate | `bash scripts/check_repo_gate.sh` | L0 | pass | `evidence/20261126_scaffold/gate.txt` |
| Unit tests | `python3 -m pytest -q tests` | L1 | pass (13 tests) | `tests/` |
| CI | `.github/workflows/ci.yml` job `studio-gate` | L0/L1 | pass | `evidence/20261126_scaffold/README.md` |
| Manifest schema | `jsonschema` against `capability-manifest.v1.schema.json` | L0 | pass | `evidence/20261126_scaffold/gate.txt` |
| Mock integration | — | L2 | not applicable (no runtime) | — |
| Mock end-to-end | — | L3 | not applicable (no runtime) | — |
| Replay | — | L4 | not applicable (no session store) | — |
| Hardware | — | L5 | not applicable; no hardware session authorized | — |

## 2. Verified claims

| Claim | Level | Evidence |
|---|---|---|
| The manifest parses, matches `VERSION`, validates against the v1 schema and pins the v0.1.0 contract hash | L0 | `capability.yaml`; `tests/test_contract.py` |
| `hardware_access: none` and an empty runtime surface are declared | L0 | `capability.yaml`; `tests/test_contract.py` |
| No device path, DDS/ROS interface, vendor topic, motion service or base-intent topic appears in code, config or scripts | L0 | `tests/test_no_hardware.py` |
| No model weight, session database, operator env file or oversized file is tracked | L0 | `scripts/check_repo_gate.sh` |
| The required document set exists and relative links resolve | L0 | `scripts/check_repo_gate.sh` |
| The same gate passes in CI on `main` | L0/L1 | `evidence/20261126_scaffold/README.md` |

## 3. Not verified

| Item | Reason | Risk |
|---|---|---|
| Gateway client and read-only diagnostics | Not implemented (M1) | No gateway contract is exercised |
| Operator session store and authorization gate | Not implemented (M2) | No session can be opened, degraded or sealed |
| Console UI and joint/gripper debug | Not implemented (M2) | No operator workflow exists |
| Trajectory inspection and evidence export | Not implemented (M3) | No record can be replayed or exported |
| Lease, deadman and fail-closed behavior | No runtime client | Cannot be tested before M2 |
| Hardware behavior | No session, no runtime | Nothing to validate on the robot |

## 4. Limitations

- The repository is a scaffold. It is not used by the running robot or by the
  current Debug Studio, which remains in the frozen `jcheng-manipulation`
  baseline.
- No gateway client, session store, console UI or export exists.
- No motion authority and no e-stop. The platform has no e-stop, brake, encoder
  or self-collision model; a software stop is not an e-stop.
- `/joint_states` is a command cache, not a measurement; any future session
  that needs pose truth must carry the system layer's bus read.
- The Studio cannot acquire a gateway token over SSH and must not be restarted
  just because the robot is unreachable.
- Synthetic or mock results will never be reported as hardware validation.
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
