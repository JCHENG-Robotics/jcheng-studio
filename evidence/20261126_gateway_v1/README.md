# Read-only `/v1` client evidence — 2026-11-26

Scope: M1a, the read-only `/v1` REST client and the Diagnostics panel. The
mock gateway is a loopback HTTP server in `backend/tests/test_gateway_v1.py`.
No robot, real gateway, DDS domain or device was used.

## Source

- `jcheng-studio` version 0.3.0.
- Client: `backend/app/adapters/gateway_v1.py`.
- Backend route: `GET /api/gateway/v1/status`.
- UI: Diagnostics → “Gateway v1 · 只读诊断”.

## Commands and results

| Check | Command | Result |
|---|---|---|
| Backend suite | `backend/.venv/bin/python -m pytest tests` | 164 passed |
| `/v1` client tests | `backend/.venv/bin/python -m pytest tests/test_gateway_v1.py` | 10 passed |
| Static tests | `python3 -m pytest -q tests` | 13 passed |
| Frontend build | `cd frontend && npm run build` | pass |
| Repository gate | `bash scripts/check_repo_gate.sh` | pass |

Artifacts: [`backend-tests.txt`](backend-tests.txt),
[`frontend-build.txt`](frontend-build.txt), [`gate.txt`](gate.txt).

## What the tests prove

- `GET /v1/health` is unauthenticated and carries the contract identity.
- `GET /v1/contract`, `/v1/state` and `/v1/safety` require
  `Authorization: Bearer`.
- A contract-hash mismatch yields `contract_match: false` and `ready: false`.
- A missing token, an unreachable gateway, stale state and a latched stop fail
  closed in the status payload.
- The client has no POST, execute, stop, lease, arm or disarm method.
- The mock server records every request; the suite asserts every recorded
  method is `GET`.

## Not covered

- The `/v1` WebSocket subscription (M1b).
- A real `jcheng-system` gateway.
- Leases, motion intent, sessions and hardware. None is implemented here.
