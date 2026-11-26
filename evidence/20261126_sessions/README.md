# M2a operator-session evidence — 2026-11-26

Scope: the append-only operator-session store, the authorization gate, the
record seal and export. No robot, DDS domain, device or motion command was
used. The gateway readiness used in tests is a mock `/v1` status dict.

## Source

- Studio version 0.5.0.
- Store: `backend/app/sessions/store.py`.
- Operations/API: `backend/app/application/session_ops.py`,
  `backend/app/api/routes.py`.
- UI: Diagnostics → “操作会话 · M2”.
- Contract: `docs/SESSIONS.md` (`schema_version: 1`, frozen at M2a).

## Commands and results

| Check | Command | Result |
|---|---|---|
| Backend suite | `backend/.venv/bin/python -m pytest tests` | 179 passed |
| Session tests | `backend/.venv/bin/python -m pytest tests/test_sessions.py` | 7 passed |
| Static tests | `python3 -m pytest -q tests` | 13 passed |
| Frontend build | `cd frontend && npm run build` | pass |
| Repository gate | `bash scripts/check_repo_gate.sh` | pass |

Artifacts: [`backend-tests.txt`](backend-tests.txt),
[`frontend-build.txt`](frontend-build.txt), [`gate.txt`](gate.txt).

## What the tests prove

- A session opens, authorizes, degrades, closes and exports with a matching
  SHA-256 record hash.
- A second active session is refused; a closed session cannot be closed again.
- Authorization is refused and the session degraded when `/v1` is not ready or
  the contract does not match.
- A tampered event is detected on export (`SESSION_HASH_MISMATCH`).
- The gateway token and URL credentials/query are not stored or exported.
- A backend restart closes a session left active.
- The session path has no lease, motion or device call.

## Not covered

- M2b joint/gripper intent through `/v1` leases and motion.
- A real gateway or robot.
- Playwright browser E2E. The UI was built but not browser-tested here.
