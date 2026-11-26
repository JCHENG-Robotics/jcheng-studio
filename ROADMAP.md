# jcheng-studio roadmap

> Status: **v0.1.0** as of 2026-11-26. Admission-only scaffold complete; no
> backend, no UI and no gateway call exist.

## Current state

| Item | State | Evidence |
|---|---|---|
| Admission manifest (`capability.yaml`) | Complete (2026-11-26) | `capability.yaml`; `tests/test_contract.py` |
| Repository scaffold, gate and CI | Complete (2026-11-26) | `scripts/check_repo_gate.sh`; CI `studio-gate` |
| Gateway client (read-only) | Planned | — |
| Operator session store and authorization gate | Planned | — |
| Console UI and joint/gripper debug | Planned | — |
| Trajectory inspection and evidence export | Planned | — |
| Hardware acceptance | Not started | — |

## Milestones

| Milestone | Scope | State | Exit criterion | Evidence |
|---|---|---|---|---|
| M0 | Repository scaffold and admission manifest | Complete (2026-11-26) | The document set, gate and CI `studio-gate` pass; the manifest claims no runtime surface | `evidence/20261126_scaffold/` |
| M1 | Read-only gateway client and diagnostics | Planned | The client implements the published gateway contract for health, contract, state and safety; a mock gateway proves read-only behavior and rejection of a contract mismatch; no motion | — |
| M2 | Operator sessions and joint/gripper debug | Planned | A session record is opened, degraded, closed and exported with a verified hash; the authorization gate and fail-closed paths are proven against a mock gateway (L3); no hardware | — |
| M3 | Trajectory inspection and experiment records | Planned | Recorded sessions replay offline (L4); commanded and measured values are compared without relabelling; exports carry versions, contract hash and calibration identifiers | — |
| M4 | Supervised hardware acceptance | Planned | Every motion session is separately authorized; the system layer applies the lease, limits and stop latch; L5 evidence is recorded alongside the system layer's own | — |

## Next

1. **M1 — read-only gateway client and diagnostics.** Implement the HTTP/WS
   client against `jcheng-interfaces` (`jcheng-gateway.v1.yaml`,
   `jcheng-ws.v1.json`), a loopback-bound backend and a read-only diagnostics
   view. Prove health, contract, state and safety reads against a mock gateway.
   Prerequisites: the gateway contract in `jcheng-interfaces` v0.1.0; an
   operator-selected gateway URL and token on the laptop. No robot session is
   required.
2. **M2 — operator sessions and joint/gripper debug.** Add the append-only
   session store, the authorization gate and intent forwarding, with the record
   schema frozen in `docs/SESSIONS.md`. Prerequisites: M1 accepted; the system
   layer's lease and deadman behavior already proven on the mock stack.

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
- The frozen `jcheng-manipulation` baseline hosts the current Debug Studio
  code. Reusing or porting any of it is an M1 decision with its own license and
  review record; v0.1.0 assumes nothing about it.
- Real-robot work requires separate, explicit operator authorization per
  session and is never triggered by CI or unattended runs.
