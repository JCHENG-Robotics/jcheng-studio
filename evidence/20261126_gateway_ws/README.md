# Read-only `/v1/ws/state` evidence — 2026-11-26

Scope: M1b, the Studio's read-only WebSocket subscription to
`jcheng-system`'s `/v1/ws/state`, plus the Diagnostics “流探测” action. No
robot, DDS domain, device or motion command was used.

## Source

- Studio version 0.4.0.
- Client: `backend/app/adapters/gateway_v1_stream.py`.
- Backend route: `GET /api/gateway/v1/stream`.
- Server side: `jcheng-system` commit `c036fe4` on `feat/ws-state-20261126`
  (`jcheng_system_core/ws.py`, both HTTP gateways).
- UI: Diagnostics → “Gateway v1 · 只读诊断” → “流探测”.

## Commands and results

| Check | Command | Result |
|---|---|---|
| Backend suite | `backend/.venv/bin/python -m pytest tests` | 172 passed |
| WS client tests | `backend/.venv/bin/python -m pytest tests/test_gateway_v1_stream.py` | 8 passed |
| Static tests | `python3 -m pytest -q tests` | 13 passed |
| Frontend build | `cd frontend && npm run build` | pass |
| Repository gate | `bash scripts/check_repo_gate.sh` | pass |
| Real system gateway interop | loopback read-only gateway + Studio WS probe | hello → state → safety, `truth=measured`, `DISARMED`, contract match |

Artifacts: [`backend-tests.txt`](backend-tests.txt),
[`frontend-build.txt`](frontend-build.txt), [`gate.txt`](gate.txt),
[`integration.txt`](integration.txt).

## What the tests prove

- The first envelope must be `hello`; `state`/`safety` are validated against
  the published message schemas before display.
- A wrong token, a contract mismatch, an invalid first envelope, an
  unreachable gateway and a disconnect fail closed.
- `stream()` reconnects with bounded attempts; auth/contract failures are not
  retried.
- The client has no POST, execute, stop, lease, arm or disarm method.
- The real `jcheng-system` read-only gateway serves the same stream; the
  Studio client reads `hello → state → safety` from it on loopback.

## Not covered

- A persistent recorded session (M2).
- Lease, motion intent or any write path.
- Hardware validation. The gateway and Studio ran on loopback only.
