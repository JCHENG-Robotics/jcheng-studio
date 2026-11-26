# Integration — jcheng-studio

> Status: **Experimental** as of 2026-11-26. v0.2.0 ports the frozen console
> backend and UI. The `/v1` gateway client is still M1, and the ported legacy
> HTTP adapter is documented in section 2. Everything marked M1/M2 remains
> planned.

## 1. Admission and discovery

The system reads `capability.yaml` and checks the admission fields. For v0.2.0
it finds:

| Field | Value | Meaning |
|---|---|---|
| `name` | `jcheng-studio` | application identity |
| `version` | `0.1.0` | must match `VERSION` |
| `contract.interfaces` | `>=0.1,<0.2` | pinned contract range |
| `contract.contract_hash` | `sha256:0b8d72…24b42` | must match the interfaces manifest |
| `safety.class` | `offline` | no runtime object to discover |
| `safety.scopes` | `[]` | the Studio holds no motion scope |
| `safety.requires_lease` | `true` | required by the schema; the Studio is never the lease holder |
| `hardware_access` | `none` | hard boundary |
| `runtime` | empty | no ROS domain, no topics, no services |

Because the **ROS** runtime surface is empty, admission means "no ROS object
to discover". The ported backend is an HTTP process, not a ROS node, and never
appears in a DDS graph; the system layer does not need to arbitrate with it.
When M1 adds the `/v1` gateway client, the ROS runtime surface stays empty:
HTTP/WS is not a ROS interface.

## 2. Gateway transport and contract

The Studio reaches the robot only through a `jcheng-system` gateway. There is
no second path: no SSH, no vendor SDK, no ROS topic, no serial device.

| Item | Rule |
|---|---|
| Base URL | Operator configuration (`ARMPI_GATEWAY_URL`); no address is hard-coded |
| Authentication | `Authorization: Bearer <token>` per the gateway OpenAPI; the token is read from `~/.config/armpi-studio/studio.env` (0600) |
| Health | `GET /v1/health` is unauthenticated liveness; it carries no motion authority |
| Contract | `GET /v1/contract` returns the deployed contract identity; a hash mismatch refuses the session |
| State and safety | `GET /v1/state`, `GET /v1/safety`, plus the WebSocket stream |
| WebSocket | One subscription using `jcheng-ws.v1.json` (`hello`, `state`, `safety`, `lease`, `motion`, `event`, `ping`, `pong`) |
| Ports | Deployment detail, not contract: `jcheng-system` documents 8766 (supervised) and 8767 (read-only). The Studio reads the configured URL and assumes nothing |
| Middleware | No DDS, no RMW, no `CYCLONEDDS_URI`. HTTP/WS only |

The authoritative documents are
[`jcheng-gateway.v1.yaml`](https://github.com/JCHENG-Robotics/jcheng-interfaces/blob/main/schemas/openapi/jcheng-gateway.v1.yaml)
and [`jcheng-ws.v1.json`](https://github.com/JCHENG-Robotics/jcheng-interfaces/blob/main/schemas/ws/jcheng-ws.v1.json).
Endpoints the Studio needs that are absent from the OpenAPI document are added
to `jcheng-interfaces` first — the Studio does not invent a private endpoint.

### Legacy pre-ecosystem adapter (ported v0.2.0)

The ported console includes the frozen Studio's older gateway client
(`backend/app/adapters/gateway.py`), which uses `/health` with an
`X-Armpi-Token` header. It is enabled only in `ARMPI_MODE=hardware` and exists
so the ported console can run against an already-deployed frozen gateway.
During migration:

- The local backend keeps `/api/health` as a liveness probe for the
  `jcheng-ops` watchdog. It reports backend liveness only and never robot state.
- The legacy adapter is **not** a contract of this repository. New features
  must not use it, and it does not become a compatibility shim for `/v1`.
- The `/v1` client (M1) uses `Authorization: Bearer` and does not carry a
  compatibility shim for `/health` or for the old token header.

## 3. Operator sessions and authorization

The Studio performs no motion on its own and never holds a lease. It forwards
operator **intent** to the gateway, where the system layer applies the lease,
deadman, limits and stop latch.

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
| v0.2.0 console port | backend tests (154 passed), frontend build, no-hardware scan; mock API paths only (L0–L2) |
| M1 gateway client | contract, health, state and safety read-only tests against a mock gateway (L2) |
| M2 session and joint debug | mock gateway proves lease-required, stale-state and fail-closed paths (L3) |
| M3 trajectory inspection | replay of recorded sessions (L4); no robot |
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
