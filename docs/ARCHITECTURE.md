# Architecture — jcheng-studio

> Status: **Experimental** as of 2026-11-26. Version 0.7.0.
> v0.7.0 ports the frozen console backend and UI, adds the read-only `/v1`
> REST and `/v1/ws/state` clients, the M2a session store, the M2b joint/gripper
> intent path and the M3 offline replay. The ported legacy HTTP adapter is
> documented in [`MIGRATION.md`](MIGRATION.md) and is not the ecosystem
> contract.

## 1. Position in the ecosystem

`jcheng-studio` is an L4 operator application. It sits above the system layer
in the dependency direction and is the only repository that owns the operator
console. It does not own a capability, a device or a motion path.

```text
operator browser
      │  local HTTP (127.0.0.1 only)
      ▼
Studio backend (operator laptop) ── session store (outside git)
      │  HTTP + WebSocket, bearer token
      ▼
jcheng-system gateway
      │  control lease, deadman, joint/base limits, stop latch
      ▼
hardware bridge -> bus servos / base board
```

Rules that come from the ecosystem and are not negotiable here:

- The system layer is the **only** origin of motion. The Studio never calls
  `ExecuteMotion`, never publishes `/cmd_vel` or `/move_action`, and never
  writes a servo or motor device.
- The Studio is an **HTTP/WS client**. It joins no DDS domain — not the vendor
  domain 0, not the privileged domain 41, not the capability domain 42 — and
  uses neither `rclpy` nor the `ros2` CLI. There is no RMW to match.
- Cross-repository contracts live only in `jcheng-interfaces`: the gateway
  OpenAPI document, the WebSocket envelope and the message schemas.
- `hardware_access: none` in `capability.yaml`. No camera, serial, CAN, GPIO or
  vendor SDK is opened, now or later.
- `/joint_states` from the vendor stack is a **command cache**, not a
  measurement. The Studio labels commanded and measured values separately and
  must not present one as the other.
- The console is **not** an e-stop. The platform has no e-stop, brake, encoder
  or self-collision model; the system layer's stop latch is a software stop.

### 1.1 Ported console (v0.4.0)

| Component | Location | Transport | Boundary |
|---|---|---|---|
| Browser UI | `frontend/` | HTTP/WS to the local backend | Holds no gateway token; never calls the gateway directly |
| Backend | `backend/` | HTTP/WS to the browser; read-only `/v1` HTTP/WS and legacy HTTP | No DDS, device, vendor SDK or motion execution |
| `/v1` read-only REST client | `backend/app/adapters/gateway_v1.py` | HTTP, Bearer, GET only | health/contract/state/safety; no lease, motion or stop |
| `/v1/ws/state` read-only client | `backend/app/adapters/gateway_v1_stream.py` | WebSocket, Bearer, one subscription | hello/state/safety; bounded reconnect; no intent |
| `/v1` intent client | `backend/app/adapters/gateway_v1_intent.py` | HTTP POST, Bearer | validate/execute one joint/gripper intent; operator-supplied lease; no lease acquisition |
| Offline replay | `backend/app/analysis/session_replay.py` | In-process, files | commanded/measured separated; delta only with joint order; hashed export |
| Session store | `backend/app/sessions/store.py` | SQLite outside git | Append-only events; authorization gate; no lease |
| Mock adapter | `backend/app/adapters/mock.py` | In-process | Synthetic data; labelled `simulated` |
| Legacy HTTP adapter | `backend/app/adapters/gateway.py` | HTTP `/health` + `X-Armpi-Token` | Frozen-baseline compatibility; not the `/v1` contract |
| Shared primitives | `packages/jcheng_core/` | In-process | Pure NumPy statistics and point-cloud math |
| Configs | `configs/` | Files | Joint mapping, pose/task contract, visual model, calibration |
| Operator runners | `tools/` | HTTP to the local backend | Operator-only; motion requires explicit flags |

The robot-side `ros2_gateway/` was **not** ported. It is a ROS 2/DDS L2
gateway and stays in the frozen baseline; the disposition is decided in
[`ROADMAP.md`](../ROADMAP.md): `jcheng-system`'s `/v1` gateway is the target.
The read-only `/v1` REST (M1a) and WebSocket (M1b) clients are implemented.
The legacy adapter remains a transitional operator path
and is not admitted by `capability.yaml`.

### 1.2 M2a operator-session store

`backend/app/sessions/store.py` keeps one active session at a time in SQLite
under `~/.local/state/armpi-studio/`. Events are append-only; the session row
is a materialized current state. Closing writes a `closed` event and a SHA-256
seal over the canonical record; export verifies the seal. Authorization calls
the read-only `/v1` status and refuses unless the gateway is configured,
reachable, authenticated, contract-matching, fresh and not stop-latched.
Failures degrade the session. No token is stored; the gateway URL drops
userinfo, query and fragment. A restart closes sessions left active. M2b adds
joint/gripper intent: the operator supplies a system-issued lease, the Studio
submits validate/execute, records the result, and never acquires or holds a
lease itself. There is no base pulse, trajectory or stop endpoint in M2b. M3
replays a session record offline: `commanded` targets and gateway `measured`
positions stay separate, a joint delta is computed only for a COMPLETED step
with a recorded joint order, and the export is hashed with versions, contract
and calibration provenance.

## 2. Planes

| Plane | Runs on | Responsibility | Prohibited |
|---|---|---|---|
| Presentation | Operator browser | Render state, safety, diagnostics, timelines and session controls | Holding a gateway token, calling the gateway directly, hiding staleness |
| Backend | Operator laptop, bound to `127.0.0.1` | Serve the UI, proxy the gateway, enforce session states, write session records | Listening on a public interface, proxying arbitrary URLs, storing tokens in git |
| Session store | Operator laptop, outside git | Append-only session events, action records and exports | Being committed, being silently rewritten, holding secrets |
| Gateway client | Operator laptop | REST calls, one WebSocket subscription, reconnect with backoff | Retry storms, unbounded queues, fabricated state on disconnect |
| Evidence export | Operator laptop, outside git | Redacted session and experiment records for acceptance | Including gateway tokens, SSH material or unrelated personal data |

The presentation plane never talks to the robot. The backend is the single
process that holds the gateway credential and it binds to the loopback
interface; a browser tab that can reach the gateway directly would bypass the
session gate.

## 3. Operator session state model

The exact state names are fixed at M2. The model below is the contract the
backend must implement; it is deliberately fail-closed.

| State | Entry condition | Allowed actions |
|---|---|---|
| `Disconnected` | No gateway reachable or health probe failing | Local diagnostics; open a session record but send nothing |
| `Read-only` | Gateway reachable, no operator session authorized | `GET` state, safety, contract and events; no `POST`, no intent |
| `Session open` | Operator explicitly authorized this session; token valid; contract hash matches | Read, display and — from M2 — forward operator intent to the gateway |
| `Degraded` | State or safety stream stale, partial or contradictory | Display the last value with its age; accept no new intent |
| `Closed` | Operator ended the session, or a fail-closed condition fired | Seal the record; return to `Read-only` or `Disconnected` |

Rules:

- Any ambiguity resolves to the more restrictive state. A missing timestamp is
  not a fresh timestamp.
- A session never re-arms itself. After `Degraded`, `Fault` or `Closed`, the
  operator authorizes again explicitly.
- Opening a session is an operator action, never a startup side effect, a CI
  step or a scheduled job.
- The Studio never holds the system's control lease. It forwards intent; the
  system layer decides whether a lease exists and whether motion proceeds.

## 4. Transport and contract

| Item | Source | Use |
|---|---|---|
| Gateway REST | `jcheng-interfaces/schemas/openapi/jcheng-gateway.v1.yaml` | Health, contract, state, safety, leases, motion intent, events |
| WebSocket envelope | `jcheng-interfaces/schemas/ws/jcheng-ws.v1.json` | `hello`, `state`, `safety`, `lease`, `motion`, `event`, `ping`, `pong` |
| Message payloads | `jcheng-interfaces/schemas/messages.v1.schema.json` | `RobotState`, `SafetyState`, `ControlLease`, `MotionResult` |
| Contract identity | `contract_hash` in `capability.yaml` | Admission; reject a gateway whose contract does not match |

Endpoints used by the Studio are the published gateway contract, not invented
paths. v0.4.0 implements the read-only REST slice: `GET /v1/health`
(unauthenticated liveness), `GET /v1/contract`, `GET /v1/state` and
`GET /v1/safety` with Bearer authentication and contract-hash pinning, plus the
read-only `GET /v1/ws/state` subscription (`hello`, `state`, `safety`). Any
endpoint the Studio needs that is not in the OpenAPI document is added to
`jcheng-interfaces` first.

The ported legacy adapter (`backend/app/adapters/gateway.py`) still uses the
pre-ecosystem gateway (`/health`, `X-Armpi-Token`). It is enabled only in
`ARMPI_MODE=hardware`, is not the contract of this repository, and must not be
used by new features. The `/v1` client does not carry a compatibility shim for
`/health` or the old token header. The local `/api/health` endpoint of the
Studio backend remains a liveness probe for the `jcheng-ops` watchdog and
carries no robot state.

## 5. Invariants

| Invariant | Enforcement |
|---|---|
| No hardware access | `capability.yaml`; `tests/test_no_hardware.py` |
| No DDS domain, `rclpy`, `ros2` CLI or vendor topic | static scan; review of every transport change |
| No motion execution or `/cmd_vel` publication | static scan; gateway client review; system lease |
| No fabricated, interpolated or held state | session state model; stale values displayed with age |
| Gateway token only in `~/.config/armpi-studio/studio.env` (0600) | gate scan; `.gitignore`; review |
| No session database, export or recording in git | gate scan; `.gitignore` |
| No vendor code copied | review; differences recorded as documents |
| Fail closed | no intent without a valid token, matching contract and fresh state |
| One session record per operator session | append-only store; export includes versions and hashes |

## 6. Failure semantics

| Condition | Required behavior |
|---|---|
| Gateway unreachable | Stay `Disconnected`; keep the local UI alive; never kill a healthy backend because the robot is down |
| Health probe failing but REST answering | `Degraded`; read-only until both agree |
| Contract hash mismatch | Refuse the session; do not fall back to an older gateway |
| Token missing, expired or rejected | Fail closed; no retry loop; prompt the operator |
| State or safety stream older than the agreed bound | `Degraded`; show the age; accept no new intent |
| WebSocket drops | Reconnect with bounded backoff; no intent while disconnected |
| Session store write fails | Stop accepting actions; an action without a record is not allowed |
| Backend crashes | The watchdog may restart it; the session stays closed and must be re-authorized |

A restart is not a motion command. Watchdog restart policy is owned by
`jcheng-ops`, not by this repository.

## 7. Non-goals

- Replacing the system layer's safety policy, lease or limits.
- Becoming a general-purpose web terminal for the robot or proxying arbitrary
  URLs.
- Rebuilding perception, motion planning or the frozen `jcheng-manipulation`
  baseline.
- Remote internet control, unattended motion or cross-network real-time
  teleoperation.
- Acting as an e-stop or a safety-rated device.

## 8. Related

- [`jcheng-interfaces`](https://github.com/JCHENG-Robotics/jcheng-interfaces) — gateway and WS contracts.
- [`jcheng-system`](https://github.com/JCHENG-Robotics/jcheng-system) — gateway owner and sole motion authority.
- [`jcheng-ops`](https://github.com/JCHENG-Robotics/jcheng-ops) — Studio watchdog and unattended health.
- [`INTEGRATION.md`](INTEGRATION.md) — admission, transport and failure rules.
- [`SESSIONS.md`](SESSIONS.md) — operator sessions, authorization and records.
- [README](../README.md) / [ROADMAP](../ROADMAP.md) / [ACCEPTANCE](../ACCEPTANCE.md).
