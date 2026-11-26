# jcheng-studio roadmap

> Status: **v0.2.0** as of 2026-11-26. The frozen console is ported and its
> offline tests pass; the `/v1` gateway client, operator session store and
> hardware acceptance are not implemented.

## Current state

| Item | State | Evidence |
|---|---|---|
| Admission manifest (`capability.yaml`) | Complete (2026-11-26) | `capability.yaml`; `tests/test_contract.py` |
| Repository scaffold, gate and CI | Complete (2026-11-26) | `scripts/check_repo_gate.sh`; CI `studio-gate` |
| Console port (backend, UI, configs, tools) | Complete (2026-11-26) | `backend/tests/`; `frontend/`; [`docs/MIGRATION.md`](docs/MIGRATION.md) |
| Mock HTTP API and offline validation | Complete (2026-11-26) | 154 passed; `evidence/20261126_console_port/` |
| Gateway client (`/v1`, read-only) | Not started | — |
| Operator session store and authorization gate | Planned | — |
| Console `/v1` wiring and joint/gripper debug | Planned | — |
| Trajectory inspection and evidence export | Planned | — |
| Playwright browser E2E in CI | Planned | Ported specs; not run here |
| Hardware acceptance | Not started | — |

## Milestones

| Milestone | Scope | State | Exit criterion | Evidence |
|---|---|---|---|---|
| M0 | Repository scaffold and admission manifest | Complete (2026-11-26) | The document set, gate and CI `studio-gate` pass; the manifest claims no ROS runtime surface | `evidence/20261126_scaffold/` |
| M0.5 | Port the frozen console | Complete (2026-11-26) | Backend and frontend are present; mock tests, the production build and the no-hardware scan pass; no hardware session | `evidence/20261126_console_port/` |
| M1 | Read-only `/v1` gateway client and diagnostics | Planned | The client implements the published gateway contract for health, contract, state and safety; a mock gateway proves read-only behavior and rejection of a contract mismatch; no motion | — |
| M2 | Operator sessions and joint/gripper debug | Planned | A session record is opened, degraded, closed and exported with a verified hash; the authorization gate and fail-closed paths are proven against a mock gateway (L3); no hardware | — |
| M3 | Trajectory inspection and experiment records | Planned | Recorded sessions replay offline (L4); commanded and measured values are compared without relabelling; exports carry versions, contract hash and calibration identifiers | — |
| M4 | Supervised hardware acceptance | Planned | Every motion session is separately authorized; the system layer applies the lease, limits and stop latch; L5 evidence is recorded alongside the system layer's own | — |

## Next

1. **M1 — read-only `/v1` gateway client.** Add a `gateway_v1` client for
   `GET /v1/health`, `/v1/contract`, `/v1/state`, `/v1/safety` and one
   WebSocket subscription, with contract-hash admission and a loopback-bound
   diagnostics view. Prove health, contract, state and safety reads against a
   mock gateway. Retire the ported legacy HTTP adapter from the default path;
   the frozen gateway remains documented in
   [`docs/legacy/`](docs/legacy/README.md).
   Prerequisites: `jcheng-interfaces` v0.1.0 and an operator-selected gateway
   URL and token on the laptop. No robot session is required.
2. **M2 — operator sessions and joint/gripper debug.** Add the append-only
   session store and the authorization gate, with the record schema frozen in
   [`docs/SESSIONS.md`](docs/SESSIONS.md). The ported SQLite event store is a
   legacy console log, not the M2 session store. Prerequisites: M1 accepted;
   the system layer's lease and deadman behavior proven on the mock stack.
3. **Gateway disposition.** Decide whether the frozen `ros2_gateway` is
   superseded by `jcheng-system`'s `/v1` gateway or ported as a system
   capability. This repository does not receive DDS code.

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
