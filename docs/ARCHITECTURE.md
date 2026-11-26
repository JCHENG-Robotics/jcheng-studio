# Architecture — jcheng-studio

> Status: **Experimental** as of 2026-11-26. Version 0.1.0.
> v0.1.0 is documentation and admission only: no backend, no UI and no runtime
> process. The gateway client is planned for M1.

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
paths. v0.1.0 claims no runtime call. From M1 the client implements
`GET /v1/health` (unauthenticated liveness), `GET /v1/contract`,
`GET /v1/state` and `GET /v1/safety`, and one WebSocket subscription. Any
endpoint the Studio needs that is not in the OpenAPI document is added to
`jcheng-interfaces` first.

The older frozen Studio in `jcheng-manipulation` used a pre-ecosystem gateway
(`/health`, `X-Armpi-Token`) and a local `/api/health`. Those are **not** the
contract of this repository. The new gateway uses `/v1/*` and `Authorization:
Bearer`; the local `/api/health` endpoint of the Studio backend remains a
liveness probe for the `jcheng-ops` watchdog and carries no robot state.

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
