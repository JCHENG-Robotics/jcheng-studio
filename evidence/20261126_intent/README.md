# M2b joint/gripper intent evidence — 2026-11-26

Scope: the authorized-session intent path, the operator-supplied lease and the
`/v1/motion/validate` + `/v1/motion/execute` calls. The mock gateway runs on
loopback. No robot, DDS domain or device was used.

## Source

- Studio version 0.6.0.
- Intent client: `backend/app/adapters/gateway_v1_intent.py`.
- Operations/API: `backend/app/application/session_ops.py`,
  `backend/app/api/routes.py`.
- UI: Diagnostics → “操作会话 · M2” → M2b intent panel.
- Contract: `jcheng-interfaces` MotionCommand / MotionResult / ValidationReport.

## Commands and results

| Check | Command | Result |
|---|---|---|
| Backend suite | `backend/.venv/bin/python -m pytest tests` | 187 passed |
| Intent tests | `backend/.venv/bin/python -m pytest tests/test_gateway_v1_intent.py` | 8 passed |
| Session tests | `backend/.venv/bin/python -m pytest tests/test_sessions.py` | 7 passed |
| Static tests | `python3 -m pytest -q tests` | 13 passed |
| Frontend build | `cd frontend && npm run build` | pass |
| Repository gate | `bash scripts/check_repo_gate.sh` | pass |

Artifacts: [`backend-tests.txt`](backend-tests.txt),
[`frontend-build.txt`](frontend-build.txt), [`gate.txt`](gate.txt).

## What the tests prove

- Intent requires an M2a session in `authorized` state.
- The operator supplies a lease; the Studio sends exactly
  `/v1/motion/validate` and `/v1/motion/execute` and **never** `/v1/leases`.
- The joint payload is `JOINT_STEP`/`step` with one joint and target; the
  gripper payload is `GRIPPER`/`gripper` with bus verification.
- A validation refusal records `intent_rejected` and does not execute.
- An UNKNOWN execution records `intent_unknown` and degrades the session.
- Missing lease, missing gateway and unauthorized session fail closed.
- The intent client has no lease or stop method.

## Not covered

- A real gateway or robot, real lease issuance or real safety/bus behavior.
- Base pulse, trajectory, stop or a lease-acquisition UI.
- Playwright browser E2E. The UI was built but not browser-tested here.
