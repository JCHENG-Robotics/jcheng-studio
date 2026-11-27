# Integration — jcheng-studio

> Status: **Experimental** as of 2026-11-27. v0.8.0 ports the frozen console
> backend and UI, implements the read-only `/v1` REST and `/v1/ws/state`
> clients, the M2a operator-session store, the M2b joint/gripper intent path
> and the M3 offline replay. The 2026-11-27 authority-hardening pass removes
> legacy motion from the default runtime: the Studio only consumes
> System-issued intent and the system stop proxy. The ported legacy HTTP
> adapter is documented in section 2.

## 1. Admission and discovery

The system reads `capability.yaml` and checks the admission fields. For v0.7.0
it finds:

| Field | Value | Meaning |
|---|---|---|
| `name` | `jcheng-studio` | application identity |
| `version` | `0.7.0` | must match `VERSION` |
| `contract.interfaces` | `>=0.1,<0.2` | pinned contract range |
| `contract.contract_hash` | `sha256:0b8d72…24b42` | must match the interfaces manifest |
| `safety.class` | `observer` | intent forwarder; the system layer executes motion |
| `safety.scopes` | `["arm", "gripper"]` | intent scopes; the Studio holds no authority |
| `safety.requires_lease` | `true` | the operator supplies a system-issued lease; the Studio is never the lease holder |
| `hardware_access` | `none` | hard boundary |
| `runtime` | empty | no ROS domain, no topics, no services |

Because the **ROS** runtime surface is empty, admission means "no ROS object
to discover". The ported backend is an HTTP process, not a ROS node, and never
appears in a DDS graph; the system layer does not need to arbitrate with it.
The `/v1` client keeps the ROS runtime surface empty: HTTP/WS is not a ROS
interface.

## 2. Gateway transport and contract

The Studio reaches the robot only through a `jcheng-system` gateway. There is
no second path: no SSH, no vendor SDK, no ROS topic, no serial device.

| Item | Rule |
|---|---|
| Base URL | Operator configuration (`ARMPI_GATEWAY_V1_URL`); no address is hard-coded |
| Authentication | `Authorization: Bearer <token>` per the gateway OpenAPI; the token comes from `ARMPI_GATEWAY_V1_TOKEN` (operator env/file, 0600) |
| Health | `GET /v1/health` is unauthenticated liveness; it carries no motion authority |
| Contract | `GET /v1/contract` returns the deployed contract identity; a hash mismatch refuses the session |
| State and safety | `GET /v1/state`, `GET /v1/safety` (M1a); the live stream is `/v1/ws/state` (M1b) |
| WebSocket | One `/v1/ws/state` subscription using `jcheng-ws.v1.json` (`hello`, `state`, `safety`, `lease`, `motion`, `event`, `ping`, `pong`); `hello`/`state`/`safety` surfaced |
| Intent (M2b) | `POST /v1/motion/validate` and `/v1/motion/execute` for one joint/gripper command, hardware-only and opt-in (`ARMPI_SYSTEM_INTENT=1`), only in an authorized session and with an operator-supplied system lease; no `/v1/leases` call |
| Ports | Deployment detail, not contract: `jcheng-system` documents 8766 (supervised) and 8767 (read-only). The Studio reads the configured URL and assumes nothing |
| Middleware | No DDS, no RMW, no `CYCLONEDDS_URI`. HTTP/WS only |

The legacy adapter uses the separate `ARMPI_GATEWAY_URL` and
`ARMPI_GATEWAY_TOKEN`; the `/v1` client never falls back to those names or
paths.

The authoritative documents are
[`jcheng-gateway.v1.yaml`](https://github.com/JCHENG-Robotics/jcheng-interfaces/blob/main/schemas/openapi/jcheng-gateway.v1.yaml)
and [`jcheng-ws.v1.json`](https://github.com/JCHENG-Robotics/jcheng-interfaces/blob/main/schemas/ws/jcheng-ws.v1.json).
Endpoints the Studio needs that are absent from the OpenAPI document are added
to `jcheng-interfaces` first — the Studio does not invent a private endpoint.

### Legacy pre-ecosystem adapter (ported v0.2.0)

The ported console includes the frozen Studio's older gateway client
(`backend/app/adapters/gateway.py`), which uses `/health` with an
`X-Armpi-Token` header. It is used only in `ARMPI_MODE=hardware` (read-only) or
the deprecated `ARMPI_MODE=legacy`, and exists so the ported console can run
against an already-deployed frozen gateway. During migration:

- The local backend keeps `/api/health` as a liveness probe for the
  `jcheng-ops` watchdog. It reports backend liveness only and never robot state.
- The legacy adapter is **not** a contract of this repository. New features
  must not use it, and it does not become a compatibility shim for `/v1`.
- The `/v1` client (M1) uses `Authorization: Bearer` and does not carry a
  compatibility shim for `/health` or for the old token header.
- The legacy **motion** transport (dispatch/chassis/stop) is removed from the
  default runtime; see the deprecation rules below.

### Legacy motion deprecation

Legacy motion is **default-disabled and isolated**, not deleted. The classes and
endpoints remain for the operator's migration window, but the default and
hardware modes cannot dispatch, pulse or locally stop motion.

| Surface | Default behavior | Deprecated exception |
|---|---|---|
| `POST /api/motion/execute` | `410 Gone`, `LEGACY_MOTION_DISABLED`, with a migration hint | `ARMPI_MODE=legacy` **and** `ARMPI_ENABLE_LEGACY_MOTION=1` |
| `POST /api/chassis/pulse` | `410 Gone` (same code) | same double switch |
| `POST /api/motion/stop` | `410 Gone` (same code) | same double switch |

- The double switch requires `ARMPI_MODE=legacy`; setting
  `ARMPI_ENABLE_LEGACY_MOTION=1` in any other mode is ignored with a startup
  warning, and `ARMPI_MODE=legacy` without the switch fails startup.
- Legacy mode also requires `ARMPI_GATEWAY_URL` and `ARMPI_GATEWAY_TOKEN` and
  keeps the joint-mapping verification / supervised-ack rules. The frozen
  gateway is deprecated; no new feature may depend on it.
- The hard block is enforced twice: at the HTTP route and inside
  `MotionOperations` / `HardwareAdapter`, so a direct service or adapter call
  is refused as well. The default code paths never mention
  `/motion/dispatch`; that path lives only in `backend/app/legacy_motion.py`.
- A `mock` process must never reach a real system gateway. At startup,
  `ARMPI_MODE=mock` with `ARMPI_GATEWAY_V1_URL` / `ARMPI_GATEWAY_V1_TOKEN`
  clears both values, disables the system stop, and records a startup warning.

**Migration.** Use the single intent path and the single stop path:

1. `POST /api/session/intent/joint` or `.../gripper` with a System-issued
   `lease_id`; requires `ARMPI_MODE=hardware` and `ARMPI_SYSTEM_INTENT=1`.
2. `POST /api/teleop/stop` for the system `POST /v1/motion/stop` (no Studio
   lease, no local motion adapter).

## 3. Operator sessions and authorization

The Studio performs no motion on its own and never holds a lease. It forwards
operator **intent** to the gateway, where the system layer applies the lease,
deadman, limits and stop latch.

- `POST /api/session/intent/joint` and `.../gripper` are hardware-only and
  opt-in: outside `ARMPI_MODE=hardware` they answer `409`
  (`SYSTEM_INTENT_REQUIRES_HARDWARE`), and with `ARMPI_SYSTEM_INTENT` unset
  they answer `403` (`SYSTEM_INTENT_DISABLED`).
- The local `/api/control/acquire|heartbeat|release` endpoints manage a **UI
  concurrency lock**, not a motion lease. Their responses carry
  `authority: "ui_concurrency_lock"` and `motion_authorization: false`. Motion
  never depends on this lock: the operator's System-issued lease is forwarded
  and validated by the system layer.

- Opening a session is an explicit operator action with a written purpose and a
  robot identity. It is never a startup side effect, a CI step or a scheduled
  job.
- Every real-robot motion requires separate, explicit, per-session operator
  authorization. A previous session's approval does not carry over.
- The session record includes the gateway URL (without the token), the contract
  hash, the software commit, the robot profile and the calibration identifiers
  in force.
- The Studio never acquires a token over SSH and never reads the robot's token
  file. The 2026-11-24 incident — a Studio restart that killed a healthy
  backend and then failed to fetch the gateway token over SSH — is the reason
  this is a hard rule, not a preference.

The full session record, lifecycle and export rules are in
[SESSIONS.md](SESSIONS.md).

## 4. Failure semantics

| Condition | Required behavior |
|---|---|
| Gateway unreachable | `Disconnected`; keep the backend alive; never kill it because the robot is down |
| `GET /v1/health` fails but the WebSocket is open | `Degraded`; read-only until both agree |
| Contract hash mismatch | Refuse the session; no fallback gateway |
| `401` / `403` from the gateway | Fail closed; no credential retry loop; prompt the operator |
| State or safety older than the agreed bound | `Degraded`; display with age; no new intent |
| WebSocket reconnect | Bounded backoff with jitter; no intent while disconnected |
| Session store unavailable | Refuse new actions; an unrecorded action is not permitted |
| Studio backend restart | Session stays closed and is re-authorized |
| Two operators | One session at a time; a second session request is refused, not queued |

The Studio has no authority to reset the system layer. A wedged gateway is the
system layer's incident, handled by `jcheng-ops` and the operator runbook.

## 5. Security and operations

- The backend binds to `127.0.0.1`. There is no public listener and no
  cross-network teleoperation.
- The gateway token lives only in `~/.config/armpi-studio/studio.env` (0600).
  It is never logged, exported, committed or placed in a URL query string.
- The Studio repository ships no systemd unit for the robot and no DDS
  configuration. The laptop watchdog and its unit belong to `jcheng-ops`.
- Session databases and exports live under `~/.local/state/armpi-studio/` and
  stay outside git.
- The Studio never restarts the vendor container, never restarts a robot
  gateway and never sends a motion command from an unattended process.
- The watchdog may restart the Studio backend. A restart is not a motion
  command and does not re-open a session.

## 6. Verification stages

| Stage | Evidence |
|---|---|
| v0.1.0 admission | `python3 -m pytest -q tests`; `bash scripts/check_repo_gate.sh` |
| v0.2.0 console port | backend tests, frontend build, no-hardware scan; mock API paths only (L0–L2) |
| v0.3.0 M1a read-only `/v1` | health, contract, state and safety tests against a mock gateway; Bearer, mismatch and unreachable paths (L2) |
| v0.4.0 M1b WebSocket | subscription, envelope, hello-first and reconnect tests against a mock WebSocket server; loopback interop with the real `jcheng-system` read-only gateway (L2) |
| v0.5.0 M2a sessions | lifecycle, authorization refusal, seal/export hash, tamper detection, redaction and restart-close tests; no hardware (L2) |
| v0.6.0 M2b intent | mock `/v1/motion/validate` + `/execute`; operator-supplied lease; unauthorized, rejected, missing-lease and UNKNOWN paths; no `/v1/leases` call (L2) |
| v0.7.0 M3 replay | offline session analysis; commanded/measured separate; delta only with a recorded joint order; hashed export with provenance (L2/L4) |
| 2026-11-27 authority hardening | `backend/tests/test_legacy_motion_disabled.py`: the three legacy endpoints answer 410 by default and never call the adapter; intent is refused in mock mode and without the opt-in; the stop goes through the system proxy; `Settings.from_env` enforces the legacy double switch and the mock/v1 guard; static scan proves no default module names `/motion/dispatch`; local lease checks are absent from motion ops (L1/L2) |
| M4 supervised hardware | separately authorized L5 session with system limits and stop latch active |

No hardware claim is valid before M4. Synthetic, mock and replay results are
never reported as hardware validation.

## 7. Related

- [`jcheng-interfaces`](https://github.com/JCHENG-Robotics/jcheng-interfaces) — gateway OpenAPI, WS envelope, message schemas.
- [`jcheng-system`](https://github.com/JCHENG-Robotics/jcheng-system) — gateway owner, lease and motion authority.
- [`jcheng-ops`](https://github.com/JCHENG-Robotics/jcheng-ops) — Studio watchdog, gateway restart policy and incident runbook.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — planes, session states and invariants.
- [`SESSIONS.md`](SESSIONS.md) — session record and authorization rules.
- [README](../README.md) / [ROADMAP](../ROADMAP.md) / [ACCEPTANCE](../ACCEPTANCE.md).
