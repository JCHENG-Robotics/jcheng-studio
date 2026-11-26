# Read-only `/v1` gateway client — jcheng-studio

> Status: **Implemented and verified against a mock gateway (L2), 2026-11-26.**
> Version 0.3.0 / milestone M1a. The WebSocket subscription is M1b and is not
> implemented yet. No robot or real gateway was used.

## 1. Purpose

The console reaches the robot only through a `jcheng-system` gateway. This
client implements the read-only slice of the published contract in
`jcheng-interfaces` (`schemas/openapi/jcheng-gateway.v1.yaml`) so the operator
can see whether a gateway is reachable, which contract it runs and what
state/safety it reports — without a lease and without a motion path.

## 2. Surface

| Item | Value |
|---|---|
| Transport | HTTP/1.1 over `urllib`; no DDS/RMW, no device, no ROS |
| Authentication | `Authorization: Bearer <token>` for contract/state/safety; health is unauthenticated |
| Endpoints | `GET /v1/health`, `GET /v1/contract`, `GET /v1/state`, `GET /v1/safety` |
| Mutation methods | none: the client has no POST, lease, motion, arm, disarm or stop method |
| Contract identity | pinned to `sha256:0b8d72…24b42`; a mismatch is reported and fails closed |
| Backend route | `GET /api/gateway/v1/status` |
| UI | Diagnostics → “Gateway v1 · 只读诊断” |

## 3. Configuration

| Variable | Meaning |
|---|---|
| `ARMPI_GATEWAY_V1_URL` | Base URL, for example `http://<gateway-host>:8766`; unset means not configured |
| `ARMPI_GATEWAY_V1_TOKEN` | Bearer token; never logged, never returned to the browser |
| `ARMPI_GATEWAY_V1_CONTRACT_HASH` | Optional override; default is the pinned v0.1.0 hash |
| `ARMPI_GATEWAY_V1_TIMEOUT_S` | Per-request timeout, default 2.0 s; must be positive |

These are separate from the legacy `ARMPI_GATEWAY_URL` / `ARMPI_GATEWAY_TOKEN`
used by the ported frozen adapter. The `/v1` client never falls back to the
legacy paths.

## 4. Fail-closed behavior

| Condition | Result |
|---|---|
| URL not configured | `configured: false`, `ready: false`, `NOT_CONFIGURED` |
| Connection refused / timeout | `reachable: false`, `ready: false`, `UNREACHABLE` |
| Missing token | health may succeed; authenticated reads report `TOKEN_MISSING`; `ready: false` |
| `401` / `403` | `authenticated: false`, `ready: false`; no credential retry loop |
| Contract hash mismatch | `contract_match: false`, `ready: false`, `CONTRACT_MISMATCH` |
| State not fresh | `state_fresh: false`, `ready: false` |
| Stop latched or safety fault | `ready: false`; the value is displayed, not cleared |
| Invalid payload | `INVALID_PAYLOAD`; no state is fabricated |

`ready` is a read-only readiness flag: health is `ok`, all four reads are
authenticated, the contract matches, the state is fresh and the safety state is
not `STOP_LATCHED`/`FAULT`. It is not a motion-ready signal; the system layer
owns that decision.

## 5. Not in this slice

- The WebSocket subscription (`jcheng-ws.v1.json`) is M1b.
- No lease acquisition, renewal, release or revoke.
- No `ExecuteMotion`, `/cmd_vel`, servo, base or gripper write.
- No session record; the M2 session store is still planned.
- No hardware validation. The evidence below is a mock gateway only.

## 6. Evidence

- `backend/tests/test_gateway_v1.py`: 10 tests against a loopback mock gateway,
  including unauthenticated health, Bearer enforcement, contract mismatch,
  unreachable gateway, missing token, no mutation methods and the
  `/api/gateway/v1/status` route. Every recorded request is a `GET`.
- [`../evidence/20261126_gateway_v1/`](../evidence/20261126_gateway_v1/README.md).

## 7. Related

- [`INTEGRATION.md`](INTEGRATION.md) — gateway contract and failure semantics.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — planes and invariants.
- [`ROADMAP.md`](../ROADMAP.md) — M1a complete, M1b WebSocket planned.
- [`SESSIONS.md`](SESSIONS.md) — operator sessions (M2).
