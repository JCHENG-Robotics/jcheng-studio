# jcheng-studio

[简体中文](README.zh-CN.md) | **English**

> Status: **Experimental** as of 2026-11-26. Version 0.1.0.
> Admission-only scaffold: the repository fixes the operator-console boundary
> and the gateway integration rules, but contains no backend, no UI and no
> runtime code. Nothing in it is used by the running robot.
> English canonical; see [README.zh-CN.md](README.zh-CN.md) for the Chinese translation.

`jcheng-studio` is the L4 operator console of the JCHENG ecosystem — the
ecosystem home of the ArmPi Debug Studio. It is the human-facing surface for
connection and diagnostics, joint and gripper debugging, trajectory inspection
and experiment records on the ArmPi Ultra. It reaches the robot only through
the `jcheng-system` gateways over HTTP/WS: it joins no DDS domain, opens no
device and never executes motion. Every action it proposes is validated and
executed — or rejected — by the system layer.

## Scope and boundary

| Item | This repository |
|---|---|
| Layer | L4 operator application (operator console), per the repository taxonomy |
| Runs on | Operator laptop: browser UI and a local backend; never on the robot |
| Inputs | Gateway HTTP/WS responses (`/v1/state`, `/v1/safety`, `/v1/contract`, events) and operator input |
| Outputs | Operator session records and exported evidence, stored outside this repository; from M1, motion **intent** forwarded to the system gateway |
| Motion authority | None. The system layer owns the control lease, deadman, joint/base limits, stop latch and `ExecuteMotion` |
| Hardware access | `none` |
| Owned devices | none |
| Forbidden | joining DDS domains 0/41/42, device access, vendor topics, `rclpy`, vendor SDK calls, direct `ExecuteMotion` or `/cmd_vel`, SSH to the robot, tokens in git, fabricated or interpolated robot state |

The system layer is the only origin of motion and the perception layer is the
only source of advisory targets. The Studio displays what the gateways report,
labels commanded values separately from measured ones, and never treats
`/joint_states` — a command cache in the vendor stack — as a measurement.
The console is not an e-stop: the platform has no e-stop, brake, encoder or
self-collision model.

## Repository layout

```text
jcheng-studio/
├── capability.yaml                 # admission manifest (offline, hardware_access: none)
├── docs/
│   ├── ARCHITECTURE.md             # console, backend, session and gateway planes
│   ├── INTEGRATION.md              # gateway contract, admission and failure semantics
│   └── SESSIONS.md                 # operator sessions, authorization and records
├── tests/
│   ├── test_contract.py            # manifest and document-set checks
│   └── test_no_hardware.py         # static no-hardware / no-DDS / no-motion scan
├── scripts/check_repo_gate.sh      # repository gate (local and CI)
├── evidence/20261126_scaffold/     # M0 evidence record
├── VERSION
├── README.md / README.zh-CN.md
├── ROADMAP.md / CHANGELOG.md / ACCEPTANCE.md
└── LICENSE-REVIEW.md
```

## Requirements

- Python 3.10 or newer (CI uses 3.12), `pytest` 8.3.4 and `PyYAML` 6.0.2.
- No ROS installation for v0.1.0 and none at any stage: the Studio is an HTTP/WS
  client and never joins a DDS graph.
- A `jcheng-system` gateway reachable on the operator network for any runtime
  work; the gate itself needs no robot and no network listener.
- Operator-side storage for session records and exports, outside this
  repository (`~/.local/state/armpi-studio/`). Gateway credentials live in
  `~/.config/armpi-studio/studio.env` with mode 0600 and never enter git.

## Quick start

```bash
python3 -m pytest -q tests
bash scripts/check_repo_gate.sh
```

The gate checks the document set, the admission manifest, relative links,
secrets, session databases, operator env files, model weights, recorded data
and the two test modules. It touches no robot, opens no device and joins no
DDS domain.

## Validation status

| Claim | Status | Evidence |
|---|---|---|
| Repository gate and unit tests pass | Verified (L0/L1, 2026-11-26) | `evidence/20261126_scaffold/`; [CI run](https://github.com/JCHENG-Robotics/jcheng-studio/actions/runs/45736077396) |
| Manifest declares `hardware_access: none` and an empty runtime surface | Verified (L0, 2026-11-26) | `tests/test_contract.py` |
| No hardware, DDS, vendor-topic or motion call exists in the tree | Verified (L0, 2026-11-26) | `tests/test_no_hardware.py` |
| Gateway client, console UI or operator session | Unverified | Not implemented (M1–M3) |

Full report: [ACCEPTANCE.md](ACCEPTANCE.md).

## Related repositories

- [`jcheng-interfaces`](https://github.com/JCHENG-Robotics/jcheng-interfaces) — gateway OpenAPI, WS envelope, message schemas and contract hash.
- [`jcheng-system`](https://github.com/JCHENG-Robotics/jcheng-system) — privileged runtime; gateway owner and the only origin of motion.
- [`jcheng-perception`](https://github.com/JCHENG-Robotics/jcheng-perception) — advisory perception targets, surfaced through the gateway.
- [`jcheng-ops`](https://github.com/JCHENG-Robotics/jcheng-ops) — Studio watchdog and unattended health, owned outside this repository.
- [`jcheng-manipulation`](https://github.com/JerichoCheng/jcheng-manipulation) — frozen `v1.0.0` baseline that hosts the current Debug Studio code.
- [`.github`](https://github.com/JCHENG-Robotics/.github) — ecosystem roadmap, changelog, acceptance and the repository taxonomy.

## License

Pending third-party and copyright review; no `LICENSE` file has been added.
Session records and exported evidence carry their own handling rules and are
never covered by this repository's eventual license. See
[LICENSE-REVIEW.md](LICENSE-REVIEW.md).
