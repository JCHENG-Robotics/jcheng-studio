# jcheng-studio

[简体中文](README.zh-CN.md) | **English**

> Status: **Experimental** as of 2026-11-26. Version 0.2.0.
> The frozen ArmPi Debug Studio console has been ported from the
> `jcheng-manipulation` baseline. The backend and UI run offline and in mock
> mode; the robot-side gateway remains outside this repository and the
> `/v1` gateway client is still the M1 milestone. No hardware session was run
> in this repository.
> English canonical; see [README.zh-CN.md](README.zh-CN.md) for the Chinese translation.

`jcheng-studio` is the L4 operator console of the JCHENG ecosystem — the
ecosystem home of the ArmPi Debug Studio. It is the human-facing surface for
connection and diagnostics, joint and gripper debugging, trajectory inspection
and experiment records on the ArmPi Ultra. It reaches the robot only through a
`jcheng-system` gateway over HTTP/WS: it joins no DDS domain, opens no device
and never executes motion. Every action it proposes is validated and executed
— or rejected — by the system layer.

## Scope and boundary

| Item | This repository |
|---|---|
| Layer | L4 operator application (operator console), per the repository taxonomy |
| Runs on | Operator laptop: browser UI and a local backend; never on the robot |
| Inputs | Gateway HTTP/WS responses (`/v1/state`, `/v1/safety`, `/v1/contract`, events) and operator input |
| Outputs | Operator session records and exported evidence, stored outside this repository; motion **intent** forwarded to the system gateway |
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

## What is ported

The ported console is the frozen baseline's G1–G8.1 surface, adapted to this
repository's boundary. The complete source and exclusion record is in
[MIGRATION.md](docs/MIGRATION.md).

| Asset | Location | State |
|---|---|---|
| FastAPI backend, mock adapter and legacy HTTP adapter | `backend/` | Ported; offline tests pass |
| React/TypeScript console with eight pages | `frontend/` | Ported; production build passes |
| Joint mapping, pose/task contract and visual model | `configs/` | Ported |
| Hand-eye calibration used by the task contract | `configs/calibration/` | Ported (2026-11-18 TSAI result) |
| Operator runners | `tools/` and `tools/studio/` | Ported; operator-run only |
| G1–G8.1 and E7 design records | `docs/legacy/` | Migrated records; links point at the baseline |
| Product requirements | `docs/PRD.md` | Migrated PRD v1.0 |

Not ported: the robot-side `ros2_gateway/` (ROS 2/DDS, L2), the runtime
`data/studio.db`, the hardware evidence bundles and the vendor-derived URDF.
The legacy HTTP adapter targets the frozen gateway (`/health`,
`X-Armpi-Token`); it is not the ecosystem `/v1` contract.

## Repository layout

```text
jcheng-studio/
├── backend/
│   ├── app/                     # FastAPI app, adapters, safety core, domain, persistence
│   └── tests/                   # 154 passed; mock and legacy-adapter paths
├── packages/jcheng_core/        # shared pure-Python stats and point-cloud primitives
├── configs/                     # joint mapping, takeover contract, visual model, calibration
├── frontend/
│   ├── src/                     # React console pages and typed API client
│   └── e2e/                     # Playwright specs; mock suite only in CI
├── tools/
│   ├── studio/                  # launcher and operator runners (G7/G8)
│   └── ...                      # TF snapshot helper and S-curve reference
├── docs/
│   ├── ARCHITECTURE.md          # console, backend, session and gateway planes
│   ├── INTEGRATION.md           # gateway contract, admission and failure semantics
│   ├── SESSIONS.md              # operator sessions, authorization and records
│   ├── MIGRATION.md             # source, exclusions and boundary adaptations
│   ├── PRD.md                   # product requirements
│   └── legacy/                  # G1–G8.1 and E7 records
├── capability.yaml              # admission manifest (offline, hardware_access: none)
├── tests/                       # manifest, document-set and no-hardware checks
├── scripts/check_repo_gate.sh   # repository gate (local and CI)
├── evidence/20261126_scaffold/  # M0 evidence record
├── evidence/20261126_console_port/  # v0.2.0 port evidence
├── VERSION
├── README.md / README.zh-CN.md
├── ROADMAP.md / CHANGELOG.md / ACCEPTANCE.md
└── LICENSE-REVIEW.md
```

## Requirements

- Python 3.11–3.12 with `pip`; the backend pins FastAPI, uvicorn, pydantic,
  PyYAML and numpy. `pytest` and `httpx` are the test extras. The local
  `packages/jcheng_core` package supplies the shared statistics and point-cloud
  primitives and is installed separately (editable, no PyPI dependency).
- Node.js 20 and npm for the frontend; `npm ci` installs the locked toolchain.
- No ROS installation at any stage: the Studio is an HTTP/WS client and never
  joins a DDS graph.
- A `jcheng-system` gateway reachable on the operator network for any runtime
  work; the gate itself needs no robot and no network listener.
- Operator-side storage for session records and exports, outside this
  repository (`~/.local/state/armpi-studio/`). Gateway credentials live in
  `~/.config/armpi-studio/studio.env` (0600) and never enter git.
- The production URDF is vendor-derived and is not shipped. Set
  `ARMPI_URDF_PATH` to a read-only copy if the visual workbench needs it; the
  tests use a project-authored kinematic fixture.

## Quick start

```bash
# Static gate, admission manifest and document set.
python3 -m pytest -q tests
bash scripts/check_repo_gate.sh

# Backend (mock mode; no robot, no gateway).
python3 -m venv backend/.venv
cd backend
.venv/bin/pip install -e '.[dev]'
.venv/bin/pip install -e ../packages/jcheng_core --no-deps
.venv/bin/python -m pytest tests
cd ..

# Frontend build.
cd frontend && npm ci && npm run build; cd ..

# Start the console (mock by default).
tools/studio/start_studio.sh
```

The gate checks the document set, the admission manifest, relative links,
secrets, session databases, operator env files, model weights, recorded data
and the test modules. It touches no robot, opens no device and joins no DDS
domain.

## Validation status

| Claim | Status | Evidence |
|---|---|---|
| Repository gate, manifest and document set pass | Verified (L0, 2026-11-26) | [`evidence/20261126_console_port/`](evidence/20261126_console_port/README.md) |
| Backend suite: 154 passed | Verified (L1, 2026-11-26) | [`evidence/20261126_console_port/backend-tests.txt`](evidence/20261126_console_port/backend-tests.txt) |
| Frontend production build passes | Verified (L0, 2026-11-26) | [`evidence/20261126_console_port/frontend-build.txt`](evidence/20261126_console_port/frontend-build.txt) |
| No device path, DDS/ROS interface, vendor topic or motion call appears in scanned code, config or scripts | Verified (L0, 2026-11-26) | `tests/test_no_hardware.py`; [gate record](evidence/20261126_console_port/gate.txt) |
| Mock HTTP API paths (preview, execute, peripherals, task plan) | Verified (L2, mock only, 2026-11-26) | `backend/tests/` |
| Playwright browser E2E | Unverified | Not run in this port |
| Legacy gateway integration (read-only or motion) | Unverified | The legacy adapter is not exercised here |
| `/v1` gateway client, operator sessions and the M2 session store | Not started | M1/M2 roadmap |
| Hardware motion | Unverified | No session authorized; no hardware evidence in this repository |

Full report: [ACCEPTANCE.md](ACCEPTANCE.md).

## Related repositories

- [`jcheng-interfaces`](https://github.com/JCHENG-Robotics/jcheng-interfaces) — gateway OpenAPI, WS envelope, message schemas and contract hash.
- [`jcheng-system`](https://github.com/JCHENG-Robotics/jcheng-system) — privileged runtime; gateway owner and the only origin of motion.
- [`jcheng-perception`](https://github.com/JCHENG-Robotics/jcheng-perception) — advisory perception targets, surfaced through the gateway.
- [`jcheng-ops`](https://github.com/JCHENG-Robotics/jcheng-ops) — Studio watchdog and unattended health, owned outside this repository.
- [`jcheng-manipulation`](https://github.com/JerichoCheng/jcheng-manipulation) — frozen `v1.0.0` baseline that supplied the ported console and still hosts the robot-side gateway and hardware evidence.
- [`.github`](https://github.com/JCHENG-Robotics/.github) — ecosystem roadmap, changelog, acceptance and the repository taxonomy.

## License

Pending third-party and copyright review; no `LICENSE` file has been added.
The ported console originates in the MIT-licensed frozen baseline; the
vendor-derived model assets were not copied. Session records and exported
evidence carry their own handling rules and are never covered by this
repository's eventual license. See [LICENSE-REVIEW.md](LICENSE-REVIEW.md).
