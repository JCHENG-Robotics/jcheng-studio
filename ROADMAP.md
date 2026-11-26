# jcheng-studio roadmap

> Status: **v0.5.0** as of 2026-11-26. The frozen console is ported; the
> read-only `/v1` REST/WS clients and the M2a operator-session store are
> verified. M2b joint/gripper intent and hardware acceptance are not
> implemented.

## Current state

| Item | State | Evidence |
|---|---|---|
| Admission manifest (`capability.yaml`) | Complete (2026-11-26) | `capability.yaml`; `tests/test_contract.py` |
| Repository scaffold, gate and CI | Complete (2026-11-26) | `scripts/check_repo_gate.sh`; CI `studio-gate` |
| Console port (backend, UI, configs, tools) | Complete (2026-11-26) | `backend/tests/`; `frontend/`; [`docs/MIGRATION.md`](docs/MIGRATION.md) |
| Mock HTTP API and offline validation | Complete (2026-11-26) | 179 passed; `evidence/20261126_sessions/` |
| Read-only `/v1` REST client (health, contract, state, safety) | Complete (2026-11-26) | [`backend/app/adapters/gateway_v1.py`](backend/app/adapters/gateway_v1.py); [`docs/GATEWAY_V1.md`](docs/GATEWAY_V1.md) |
| Read-only `/v1/ws/state` subscription (M1b) | Complete (2026-11-26) | [`backend/app/adapters/gateway_v1_stream.py`](backend/app/adapters/gateway_v1_stream.py); `evidence/20261126_gateway_ws/` |
| Diagnostics page wired to `/api/gateway/v1/status` and `/api/gateway/v1/stream` | Complete (2026-11-26) | `frontend/src/pages/Diagnostics.tsx` |
| M2a operator session store and authorization gate | Complete (2026-11-26) | [`backend/app/sessions/store.py`](backend/app/sessions/store.py); [`docs/SESSIONS.md`](docs/SESSIONS.md); `evidence/20261126_sessions/` |
| M2b console `/v1` joint/gripper intent | Planned | — |
| Trajectory inspection and evidence export | Planned | — |
| Playwright browser E2E in CI | Planned | Ported specs; not run here |
| Hardware acceptance | Not started | — |

## Milestones

| Milestone | Scope | State | Exit criterion | Evidence |
|---|---|---|---|---|
| M0 | Repository scaffold and admission manifest | Complete (2026-11-26) | The document set, gate and CI `studio-gate` pass; the manifest claims no ROS runtime surface | `evidence/20261126_scaffold/` |
| M0.5 | Port the frozen console | Complete (2026-11-26) | Backend and frontend are present; mock tests, the production build and the no-hardware scan pass; no hardware session | `evidence/20261126_console_port/` |
| M1a | Read-only `/v1` REST client and diagnostics | Complete (2026-11-26) | The client implements health, contract, state and safety; a mock gateway proves Bearer enforcement, contract-mismatch rejection, stale/unreachable handling and the absence of mutation methods; no motion | `evidence/20261126_gateway_v1/` |
| M1b | `/v1` WebSocket subscription | Complete (2026-11-26) | One subscription validates `jcheng-ws.v1.json` envelopes, requires hello first, reconnects with bounded backoff and sends no intent; mock and real read-only gateways verified; no hardware | `evidence/20261126_gateway_ws/` |
| M2a | Operator session store and authorization gate | Complete (2026-11-26) | A session record is opened, degraded, closed and exported with a verified hash; authorization refusal, tamper detection, redaction and restart-close are proven against a mock `/v1` status; no hardware | `evidence/20261126_sessions/` |
| M2b | Console `/v1` joint/gripper intent | Planned | One operator intent is forwarded through the system gateway's lease/validate/execute path against a mock gateway; fail-closed paths are proven (L3); no hardware | — |
| M3 | Trajectory inspection and experiment records | Planned | Recorded sessions replay offline (L4); commanded and measured values are compared without relabelling; exports carry versions, contract hash and calibration identifiers | — |
| M4 | Supervised hardware acceptance | Planned | Every motion session is separately authorized; the system layer applies the lease, limits and stop latch; L5 evidence is recorded alongside the system layer's own | — |

## Next

1. **M2b — joint/gripper intent.** Extend the `/v1` client with the system
   gateway's lease, validate and execute path for one joint step or one gripper
   command, always behind an authorized M2a session. Prove lease-required,
   stale-state, contract-mismatch and stop-latched refusals against a mock
   system gateway (L3); no hardware.
2. **Gateway disposition — decided 2026-11-26.** The frozen DDS
   `ros2_gateway` is not ported into this repository; `jcheng-system`'s `/v1`
   gateway is the target. The legacy HTTP adapter and the legacy G7/G8 runners
   remain a transitional operator path only, are disabled by default and are
   not admitted by `capability.yaml`. Task/takeover endpoints still needed by
   the console must be added to `jcheng-interfaces` and `jcheng-system` first.

## Out of scope

- Joining any DDS domain, using `rclpy` or the `ros2` CLI, or publishing vendor
  topics.
- Opening cameras, serial, CAN, GPIO or any robot device.
- Calling `ExecuteMotion`, publishing `/cmd_vel` or `/move_action`, or writing a
  servo or motor.
- Holding the system's control lease or replacing its safety policy.
- SSH to the robot, fetching tokens over SSH, or proxying arbitrary URLs.
- Storing session databases, exports, recordings, tokens or weights in git.
- Acting as an e-stop or a safety-rated device. The platform has no e-stop,
  brake, encoder or self-collision model; a software stop is not an e-stop.

## Dependencies

- `jcheng-interfaces` `>=0.1,<0.2`; contract hash
  `sha256:0b8d72ced7f54d444d6851d8b99a51a78e2bed3f687324f51a15f76557f24b42`.
- `jcheng-system` API `>=0.1,<0.2`; the gateway is the only robot-facing path
  and the system layer remains the only origin of motion.
- `jcheng-ops` owns the Studio watchdog and the restart policy on the laptop.
- The console was ported from the frozen `jcheng-manipulation` baseline
  (`a08b8b6`, 2026-11-26 working tree). The robot-side gateway, the runtime
  session database, the hardware evidence and the vendor-derived URDF were
  deliberately not copied; see [`docs/MIGRATION.md`](docs/MIGRATION.md).
- Real-robot work requires separate, explicit operator authorization per
  session and is never triggered by CI or unattended runs.
