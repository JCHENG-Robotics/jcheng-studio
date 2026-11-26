# M3 offline replay evidence — 2026-11-26

Scope: offline replay of M2a session records and M2b intent events. The analysis
is pure Python; it does not contact a gateway, robot or device.

## Source

- Studio version 0.7.0.
- Analysis: `backend/app/analysis/session_replay.py`.
- API: `GET /api/session/{id}/analysis`, `/analysis/export`.
- UI: Diagnostics → “会话回放 · M3”.
- Input contract: `docs/SESSIONS.md` (`schema_version: 1`).

## Commands and results

| Check | Command | Result |
|---|---|---|
| Backend suite | `backend/.venv/bin/python -m pytest tests` | 191 passed |
| Replay tests | `backend/.venv/bin/python -m pytest tests/test_session_replay.py` | 4 passed |
| Static tests | `python3 -m pytest -q tests` | 13 passed |
| Frontend build | `cd frontend && npm run build` | pass |
| Repository gate | `bash scripts/check_repo_gate.sh` | pass |

Artifacts: [`backend-tests.txt`](backend-tests.txt),
[`frontend-build.txt`](frontend-build.txt), [`gate.txt`](gate.txt).

## What the tests prove

- `commanded_target_rad` and `gateway_measured_positions` remain separate
  fields with their own truth labels.
- A joint delta is computed only for a `COMPLETED` `JOINT_STEP` and only when
  the session recorded `joint_order`; otherwise it is `null` with a limitation.
- A gripper intent is not given a fabricated measured delta.
- Rejections, UNKNOWN and pending intents are counted separately.
- The exported analysis is self-describing (studio version/commit, contract
  hash, calibration) and its `analysis_hash` verifies.

## Not covered

- A fresh bus re-read: `measured` is the gateway MotionResult array.
- A gripper measured position: the result does not carry one.
- Hardware validation.
- Playwright browser E2E. The UI was built but not browser-tested here.
