# Console migration record — 2026-11-26

This repository is the ecosystem home of the ArmPi Debug Studio. v0.2.0 ports
the frozen console from `jcheng-manipulation` and keeps the L4 boundary: the
console is an HTTP/WS client of the system gateways and owns no device, DDS
domain or motion authority.

## 1. Source

| Item | Value |
|---|---|
| Repository | [`JerichoCheng/jcheng-manipulation`](https://github.com/JerichoCheng/jcheng-manipulation) |
| Path | `armpi-debug-studio/` and `tools/studio/` |
| HEAD at port | `a08b8b6` (`fix/vision-boot-20261124`) |
| Working tree | 2026-11-26, including uncommitted G8 form/takeover edits |
| Baseline verification | 154 backend tests, 93 gateway tests, 16 Playwright tests (baseline record) |
| License | Baseline is MIT; vendor-derived assets are excluded (see below) |

The port is a **copy**. The frozen baseline is left intact so its v1.0.0 tree,
uncommitted changes and evidence remain addressable.

## 2. What was ported

| Asset | Destination | Notes |
|---|---|---|
| FastAPI backend | `backend/` | Mock adapter, legacy HTTP gateway adapter, safety/preview/lease core, SQLite event store, G2/G3/G4/G8 operations |
| Shared pure-Python primitives | `packages/jcheng_core/` | `jcheng_stats` and `jcheng_vision`; required by the experiment and task paths |
| React/TypeScript console | `frontend/` | Eight pages, typed API client, Playwright specs |
| Runtime configs | `configs/` | Joint mapping, pose/task takeover contract, visual model |
| Hand-eye calibration | `configs/calibration/` | 2026-11-18 table-down TSAI result, used by `task.hand_eye` |
| Backend tests | `backend/tests/` | 164 passed in this repository (includes the /v1 client tests) |
| Console runners and reference tools | `tools/` and `tools/studio/` | HTTP clients of the local backend; operator-run only. `tools/verify_tf_snapshot.py` composes a captured `/tf` snapshot; `tools/phase2/motion_profile.py` is the pure-Python S-curve reference used by the profile tests. |
| Design records | `docs/legacy/` | G1–G8, E7 and the baseline Studio README |
| Product requirements | `docs/PRD.md` | Original PRD v1.0 |

## 3. What was deliberately not ported

| Asset | Reason | Owner |
|---|---|---|
| `ros2_gateway/` | ROS 2/DDS process that publishes servo commands; L2 hardware gateway. Copying it would violate this repository's `hardware_access: none`, the no-DDS static gate and the single-motion-entry rule. | `jcheng-system` (migration not part of this task) |
| `data/studio.db` | Runtime SQLite event store; session data must stay outside git. | Operator laptop, outside the repository |
| `evidence/studio_*`, `evidence/g8_*` | Hardware evidence belongs to the baseline commit and its gateway. Links point at the baseline repository. | Frozen baseline |
| Vendor-derived URDF/xacro and meshes | `jcheng-manipulation/THIRD_PARTY_NOTICES.md` states the model assets are not relicensed under MIT. The console reads them only when `ARMPI_URDF_PATH` points at a copy. | Frozen baseline / robot description |
| Gateway-coupled tools (`build_g5_dataset.py`, `export_gateway_motion_config.py`) | They import the legacy gateway's pure-Python modules and export its motion contract; they are not console tools. | Frozen baseline / future `jcheng-system` gateway |
| SSH preflight and robot camera launch | The Studio must not SSH to the robot or start ROS processes. `tools/studio/start_studio.sh` was rewritten as a loopback-only console launcher. | `jcheng-ops` / operator runbook |
| `.venv`, `node_modules`, `dist`, build caches | Generated dependencies and artifacts are not source. | Local install |

## 4. Boundary adaptations

1. **Vendor labels scrubbed from scanned code.** Mock/serial/battery/aux-camera
   labels now use `mock://` and `gateway-*` names. `tests/test_no_hardware.py`
   passes over the whole tree.
2. **Legacy HTTP adapter is explicit.** `backend/app/adapters/gateway.py` is the
   frozen baseline client (`/health`, `X-Armpi-Token`). It is reachable only in
   `ARMPI_MODE=hardware` and is not the ecosystem `/v1` contract. The `/v1`
   client is the M1 milestone; new features must not use the legacy adapter.
3. **URDF path is external.** `backend/app/config.py` defaults to
   `configs/urdf/armpi_ultra.xacro`, which is intentionally absent. Tests use a
   project-authored kinematic fixture; the visual workbench degrades to
   `available: false` when the real description is not configured. The fixture
   contains only the joint origins, axes, limits and mimic relations needed by
   the FK tests; it has no vendor mesh, inertial or collision data.
4. **Launcher is loopback-only.** `tools/studio/start_studio.sh` starts the
   backend on `127.0.0.1`, stores the event database under
   `~/.local/state/armpi-studio/`, and never fetches a token over SSH. A busy
   port is reported, never killed.
5. **Authoring format.** The baseline files are ported with their comments and
   Chinese operator labels. No vendor source, firmware, driver or SDK was
   copied.

## 5. Verification in this repository

| Check | Result | Level |
|---|---|---|
| `python3 -m pytest -q tests` (manifest, document set, static no-hardware scan) | pass | L0 |
| `backend/.venv/bin/python -m pytest backend/tests` | 164 passed | L1 |
| `npm run build` in `frontend/` | pass | L0 |
| `bash scripts/check_repo_gate.sh` | pass | L0 |
| Mock HTTP API paths (preview, execute, peripherals, task plan) | covered by the backend suite | L2 (mock) |
| Read-only `/v1` REST client (health, contract, state, safety) | 10 mock-gateway tests; Bearer, mismatch and unreachable paths | L2 (mock) |
| Playwright browser E2E | not run in this port | Unverified |
| Legacy gateway integration | not exercised here | Unverified |
| `/v1` WebSocket subscription | not implemented | Not started (M1b) |
| Hardware motion | no session authorized | Unverified |

Evidence: [`evidence/20261126_console_port/`](../evidence/20261126_console_port/README.md)
and [`evidence/20261126_gateway_v1/`](../evidence/20261126_gateway_v1/README.md).

## 6. Follow-ups

1. **M1a — read-only `/v1` REST client (complete 2026-11-26).** Implemented
   `GET /v1/health`, `/v1/contract`, `/v1/state`, `/v1/safety` with Bearer auth
   and contract-hash pinning; proven against a mock gateway. See
   [`GATEWAY_V1.md`](GATEWAY_V1.md). **M1b — WebSocket subscription** remains.
2. **M2 — operator sessions.** Add the append-only session store and
   authorization gate from `docs/SESSIONS.md`; the ported SQLite event store is
   not that store.
3. **Gateway disposition — decided 2026-11-26.** `jcheng-system`'s `/v1`
   gateway is the target; the frozen `ros2_gateway` is not ported into this
   repository. The legacy adapter/runners remain transitional, disabled by
   default and not admitted. Task/takeover endpoints the console still needs
   must be added to `jcheng-interfaces` and `jcheng-system` first.
4. **License review.** Record the ported-code authorship and third-party
   inventory in `LICENSE-REVIEW.md`; add `THIRD_PARTY_NOTICES.md` only if the
   review requires it.
