# Gateway clients — jcheng-studio

> Status: **Implemented and verified against mock gateways and the real
> `jcheng-system` read-only gateway (L2), 2026-11-26.**
> Version 0.7.0 / milestones M1a (REST), M1b (WebSocket) and M2b (intent);
> M3 replay is documented in [`SESSIONS.md`](SESSIONS.md).
> No robot was used.

## 1. Purpose

The console reaches the robot only through a `jcheng-system` gateway. The
read-only clients implement the published contract in `jcheng-interfaces` so
the operator can see whether a gateway is reachable, which contract it runs and
what state/safety it reports. The M2b intent client forwards one joint/gripper
command only after an authorized session and with an **operator-supplied
system lease**; the Studio never acquires or holds a lease.

## 2. Surface

| Item | Value |
|---|---|
| REST transport | HTTP/1.1 over `urllib`; no DDS/RMW, no device, no ROS |
| WS transport | `websockets` sync client; one `/v1/ws/state` subscription |
| Intent transport | HTTP POST; `/v1/motion/validate` + `/v1/motion/execute` only |
| Authentication | `Authorization: Bearer <token>`; only `GET /v1/health` is unauthenticated |
| REST endpoints | `GET /v1/health`, `/v1/contract`, `/v1/state`, `/v1/safety` |
| WS endpoint | `GET /v1/ws/state` upgrade; `hello`, then `state` + `safety` at ~10 Hz |
| Intent endpoints | `POST /v1/motion/validate`, `POST /v1/motion/execute` (Idempotency-Key) |
| Lease methods | none: the Studio never calls `/v1/leases`; the operator supplies the lease |
| Contract identity | pinned to `sha256:0b8d72…24b42`; a mismatch is reported and fails closed |
| Backend routes | `GET /api/gateway/v1/status`, `GET /api/gateway/v1/stream`; `POST /api/session/intent/joint|gripper` |
| UI | Diagnostics → “Gateway v1 · 只读诊断” and the “操作会话 · M2” intent panel |

## 3. Configuration

| Variable | Meaning |
|---|---|
| `ARMPI_GATEWAY_V1_URL` | Base URL, for example `http://<gateway-host>:8766`; unset means not configured |
| `ARMPI_GATEWAY_V1_TOKEN` | Bearer token; never logged, never returned to the browser |
| `ARMPI_GATEWAY_V1_CONTRACT_HASH` | Optional override; default is the pinned v0.1.0 hash |
| `ARMPI_GATEWAY_V1_TIMEOUT_S` | Connect/read timeout, default 2.0 s; must be positive |

These are separate from the legacy `ARMPI_GATEWAY_URL` / `ARMPI_GATEWAY_TOKEN`
used by the ported frozen adapter. The `/v1` clients never fall back to the
legacy paths.

## 4. Fail-closed behavior

| Condition | Result |
|---|---|
| URL not configured | `configured: false`, `ready: false`, `NOT_CONFIGURED` |
| Connection refused / timeout | `reachable: false`, `ready: false`, `UNREACHABLE` / `STREAM_CLOSED` |
| Missing token | REST health may succeed; authenticated reads report `TOKEN_MISSING`; `ready: false` |
| `401` / `403` | `authenticated: false`, `ready: false`; no credential retry loop |
| Contract hash mismatch | `contract_match: false`, `ready: false`, `CONTRACT_MISMATCH` |
| State not fresh | `state_fresh: false`, `ready: false` |
| Stop latched or safety fault | `ready: false`; the value is displayed, not cleared |
| Invalid REST payload | `INVALID_PAYLOAD`; no state is fabricated |
| WS first envelope not `hello` | `STREAM_CONTRACT`; no state is accepted |
| WS envelope v/type/id/ts/payload invalid | `STREAM_CONTRACT`; the stream fails closed |

`ready` is a read-only readiness flag: the gateway is reachable and
authenticated, the contract matches, the state is fresh and the safety state is
not `STOP_LATCHED`/`FAULT`. It is not a motion-ready signal; the system layer
owns that decision.

## 5. WebSocket stream (M1b)

The `jcheng-system` gateways (both the mock `jcheng_gateway` and the read-only
`jcheng_telemetry` gateway) implement the server side at
`GET /v1/ws/state`; the system change is commit `c036fe4` on
`feat/ws-state-20261126`.

- After the upgrade the server sends one `hello` envelope, then `state` and
  `safety` envelopes at about 10 Hz, using
  `jcheng-interfaces/schemas/ws/jcheng-ws.v1.json`.
- The Studio validates `v == 1`, the type set, a non-empty `id`, `ts`, the
  payload object, the `hello`-first rule, the `RobotState` fields and the
  `SafetyState` fields before exposing anything to the UI.
- `GatewayV1Stream.stream()` reconnects with linear, bounded backoff
  (`max_attempts`, default 3); an auth failure or a contract violation is not
  retried.
- The Diagnostics “流探测” action opens one short subscription and reports the
  envelope sequence, state truth, safety state and latency. It sends no
  intent and stores no session.
- Client ping is answered by the server; the Studio stream client uses the
  `websockets` library, which handles protocol ping/pong.

## 6. Intent client (M2b)

`backend/app/adapters/gateway_v1_intent.py` is a separate POST-only client for
exactly two endpoints:

1. `POST /v1/motion/validate` with a `MotionCommand`;
2. `POST /v1/motion/execute` with the same command and an
   `Idempotency-Key` header.

It is reachable only through `POST /api/session/intent/joint` or
`/api/session/intent/gripper`, which require an M2a session in the
`authorized` state. The **operator supplies a lease ID issued by the system
layer**; the Studio never calls `/v1/leases` and has no lease or stop method.
The command carries `source=studio:<session>`, the pinned contract, bus
verification and a measured-state requirement. A validation refusal is
recorded as `intent_rejected`; an UNKNOWN result is recorded as
`intent_unknown` and degrades the session. No retry is attempted.

## 7. Not in this slice

- No lease acquisition, renewal, release or revoke.
- No base pulse, trajectory execution or stop endpoint; only one joint step or
  one gripper command.
- No `lease`/`motion`/`event` envelope handling beyond validating the generic
  envelope shape; only `hello`/`state`/`safety` are surfaced.
- No persistent stream recording; the probe is deliberately short.
- No hardware validation.

## 8. Evidence

- `backend/tests/test_gateway_v1.py`: 10 REST tests against a loopback mock
  gateway.
- `backend/tests/test_gateway_v1_stream.py`: 8 WebSocket tests covering
  hello/state/safety, Bearer enforcement, contract mismatch, hello-first,
  bounded reconnect, unreachable gateway and the stream route.
- `backend/tests/test_gateway_v1_intent.py`: 8 intent tests covering
  unauthorized refusal, joint/gripper commands, validation rejection, missing
  lease, UNKNOWN degradation, missing gateway and the absence of lease/stop
  methods. The mock records every path; no `/v1/leases` call occurs.
- [`../evidence/20261126_gateway_v1/`](../evidence/20261126_gateway_v1/README.md),
  [`../evidence/20261126_gateway_ws/`](../evidence/20261126_gateway_ws/README.md)
  and [`../evidence/20261126_intent/`](../evidence/20261126_intent/README.md).
- Real interop: the Studio client read `hello → state → safety` from the
  `jcheng-system` read-only gateway on loopback; see
  [`../evidence/20261126_gateway_ws/integration.txt`](../evidence/20261126_gateway_ws/integration.txt).

## 9. Related

- [`INTEGRATION.md`](INTEGRATION.md) — gateway contract and failure semantics.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — planes and invariants.
- [`ROADMAP.md`](../ROADMAP.md) — M1a/M1b complete, M2 next.
- [`SESSIONS.md`](SESSIONS.md) — operator sessions (M2).
