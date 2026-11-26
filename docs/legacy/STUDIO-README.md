# ArmPi Debug Studio

> **Migration record (2026-11-26).** This is the baseline Studio README,
> written for the frozen `jcheng-manipulation` repository. Its test counts,
> gateway claims and hardware evidence describe that baseline. In
> `jcheng-studio` the console is ported and the offline tests pass, but no
> hardware session has been run; the robot-side gateway is not part of this
> repository. See [`../MIGRATION.md`](../MIGRATION.md).

A safe, evidence-first debugging console for the ArmPi Ultra 5-DoF arm.

**Current milestone: G8 fast task path (merged chassis approach + S-curves;
hardware motion off by default).**
G1–G8.1 are implemented: the Mock path, the read-only ROS 2 gateway, G2–G5
offline/analysis tooling, the G6 supervised write path, the G7 five-joint
pose + gripper takeover, the G8 camera-to-task takeover and the G8.1 bounded
chassis approach. One run now covers the base approach, the 7 arm phases and
the put-back, with dense S-curve waypoints at the measured 4.0/20 envelope.
The operator UI and its explanations are **Chinese**; protocol codes
(`Completed`, `commanded`, `LIVE`, …) stay visible in tooltips/details so the
screen still cross-references logs, CSVs and evidence bundles.
Hardware motion
still requires `ARMPI_HARDWARE_MOTION=1`; G6 adds the fixed single-joint
acknowledgement and G7/G8 use a separate pose acknowledgement plus a pose-mode
gateway with a velocity cap and an FK workspace/table gate. There is no e-stop.
The vendor SDK/Driver are referenced read-only and are **not** copied into this
directory.

**Current verification (2026-11-23):** 148 backend tests, 93 gateway tests and
16 Playwright tests (10 mock + 3 hardware + 3 pose/task). The dated status
notes below preserve the counts at the time they were written.

> Status note (2026-11-15): this directory is the G1 **Mock** slice imported into
> the main repository. It is **not** a PRD G1 pass: E7 (ROS 2 read-only gateway)
> and supervised hardware acceptance are not implemented. See
> `docs/audit/2026-11-15/PROJECT_STATUS_AUDIT.md` and `DECISION_REQUIRED.md`.

> Status note (2026-11-17, later): camera/battery and the read-only gateway are
> verified on the real robot over direct Ethernet, and the gateway now runs as
> an enabled systemd service (clean SIGTERM stop/start verified; reboot test
> deferred). `pytest` reports **48 passed**, `npm run build` succeeds, the
> Playwright mock E2E suite reports **7 passed**, gateway `/battery`,
> `/camera/status`, `/camera/snapshot` and `/state` return live data, and the
> backend `hardware` mode rejects execute with `GATE_REJECTED`. See
> [`docs/e7-gateway-verification.md`](e7-gateway-verification.md) and
> [`frontend/e2e/README.md`](../../frontend/e2e/README.md). Hardware motion remains
> disabled.

> Status note (2026-11-18): the **G2 Trajectory Inspector** is implemented for
> offline JSON datasets. Planned / commanded / feedback streams are stored
> separately, metrics are recomputable from CSV, and replay is client-side only.
> The bundled demo is explicitly `simulated`; no hardware trajectory has been
> captured. Backend `pytest` reports **56 passed**, the Playwright mock E2E suite
> reports **8 passed**. See
> [`docs/g2-trajectory-inspector.md`](g2-trajectory-inspector.md) and
> [`evidence/studio_g2/20261118/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_g2/20261118/README.md).

> Status note (2026-11-18, later): the **G3 Experiment Lab** is implemented for
> offline experiment definitions and append-only paired trials, with failure
> codes, G2 dataset references, CSV/JSON export and the Phase 3 paired analysis
> (seeded bootstrap, sign test, McNemar). The bundled demo is `simulated`.
> Backend `pytest` reports **65 passed**, the Playwright mock E2E suite reports
> **9 passed**. See [`docs/g3-experiment-lab.md`](g3-experiment-lab.md) and
> [`evidence/studio_g3/20261118/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_g3/20261118/README.md).

> Status note (2026-11-18, later): the **G4 Visual Workbench** is implemented
> as a read-only URDF forward-kinematics view plus optional gateway `/tf`,
> camera/calibration/TF warnings and metadata-only camera references. It has no
> motion path and never enters the safety chain. Backend `pytest` reports
> **74 passed**, the Playwright mock E2E suite reports **10 passed**. See
> [`docs/g4-visual-workbench.md`](g4-visual-workbench.md) and
> [`evidence/studio_g4/20261118/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_g4/20261118/README.md).

> Status note (2026-11-18, updated 2026-11-21): the **G5 read-only trajectory
> recorder** is implemented. It subscribes to the vendor command echo and
> `/controller_manager/servo_states`, which the 2026-11-21 discovery showed is
> the **vendor command cache**, not a bus read. The G5 dataset's
> `feedback.truth=measured` label must therefore be read as `commanded`; its
> "tracking" metrics are plan-vs-command. The actual registers come from
> `GetBusServoState` (see G7). A 5 s no-motion capture produced 242 commanded /
> 241 cache samples at ~48 Hz; a later **supervised `camera_forward` execute**
> produced a 0.892 s, 712/711-sample moving capture. The gateway `/tf` path is
> verified on the robot (11 dynamic + 8 static edges, `base_link→link5` within
> 0.4 mm of `tf2_echo`), and the gated hardware Playwright suite passes 3/3.
> See [`docs/g5-trajectory-recorder.md`](g5-trajectory-recorder.md) and
> [`evidence/studio_g5/20261118/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_g5/20261118/README.md).

> Status note (2026-11-18, latest): the **G6 supervised motion write path** is
> implemented. It is **off by default** and requires both
> `ARMPI_HARDWARE_MOTION=1` and, while limits are unverified, the fixed
> `ARMPI_SUPERVISED_MOTION_ACK` phrase. The backend enforces one joint per
> command, a pulse-aligned 0.096342 rad step cap and a 1.0 s duration floor;
> the gateway independently revalidates against measured servo pulses. A
> second supervised gateway instance was started on `8766` and a supervised
> backend reached `safety.ready=true`; out-of-limit and over-step commands were
> rejected and **the arm did not move** (measured delta 0.0 rad). No valid
> dispatch was sent. Backend `pytest` reports **93 passed**, gateway **47
> passed** (counts refreshed 2026-11-20; 92/38 at the G6 verification), mock
> Playwright **10 passed**, hardware Playwright **3 passed**.
> See [`docs/g6-motion-write-path.md`](g6-motion-write-path.md),
> [`ros2_gateway/deploy/supervised-motion.md`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/armpi-debug-studio/ros2_gateway/deploy/supervised-motion.md)
> and [`evidence/studio_g6/20261118/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_g6/20261118/README.md).

> Status note (2026-11-21, latest): the **G7 pose takeover** is implemented and
> **verified with real motion**. A command is one whole five-joint pose or one
> `r_joint` gripper command. The backend enforces a 0.5 rad/s velocity cap and
> an FK gate over 21 samples of the straight joint-space interpolation: the
> `armpi_safety` workspace box, the measured table plane + 10 mm, and the
> low-pose yaw rule. The gateway has its own `mode=pose` contract, a gripper
> command range (170…700 raw) and timed gripper completion.
>
> **Correction:** `/controller_manager/servo_states` is the vendor **command
> cache**, not measured feedback. The gateway now reads the actual registers
> with `GetBusServoState` after the interpolation and completes an arm command
> only within ±12 pulses; a cache-only completion is `Unknown`. A full
> `view_table → grasp_low → view_table` + gripper sequence ran on the robot:
> the low pose actual was `502/339/928/336/499` (max 4 pulses from target), and
> the gripper settled at 184 (open) / 645 (close) while the cache said 700.
> Backend `pytest` reports **108 passed**, gateway **62 passed**, mock
> Playwright **10 passed**, pose-scope mock Playwright **2 passed**, hardware
> Playwright **3 passed**. See
> [`docs/g7-pose-takeover.md`](g7-pose-takeover.md),
> [`ros2_gateway/deploy/takeover.md`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/armpi-debug-studio/ros2_gateway/deploy/takeover.md) and
> [`evidence/studio_g7/20261121/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_g7/20261121/README.md).

> Status note (2026-11-21, fast task path): the G8 task run now executes the
> arm with **dense S-curves** (4.0 rad/s / 20 rad/s², the 2026-11-20 measured
> envelope) instead of one long constant-velocity point, groups the old 13
> commands into **7 operator phases**, drops the separate `lift_150` move, and
> performs the **G8.1 base approach inside the same run** (the standalone
> "底盘接近" button is gone). Every S-curve segment is re-validated by the
> gateway (velocity, discrete acceleration, measured start, endpoint equality)
> before one `ServosPosition` is published per segment. Backend `pytest`
> reports **136 passed**, gateway **80 passed**, pose-scope mock Playwright
> **3 passed**. **Real-robot run (2026-11-21): PASS** — 7 phases, 5.466 s
> commanded / 13.46 s wall (the old 13-step plan was 33.56 s), `close_pre`
> 309, `close_grip` 414, `held: true`, landing 2.27 mm planar; the first
> attempt exposed and fixed a missing `fast` flag in the gateway payload. See
> [`docs/g8-task-takeover.md`](g8-task-takeover.md) and
> [`evidence/studio_g8_fast/20261121/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_g8_fast/20261121/README.md).

## Status

| Area | Status | Evidence |
|---|---|---|
| Mock adapter + fault injection | Implemented | `backend/app/adapters/mock.py`, 41 baseline tests + 5 peripheral tests |
| Backend REST + WebSocket | Implemented | `backend/app/api/routes.py` |
| Safety core (validation/gate/lease/token/idempotency) | Implemented | `backend/app/safety/`, `backend/tests/test_safety.py` |
| SQLite append-only event log + CSV export | Implemented | `backend/app/persistence/store.py` |
| Dashboard / Joint Control / Diagnostics / Trajectory Inspector / Experiment Lab / Event Timeline / Settings | Implemented | `frontend/src/pages/` |
| Frontend type-check + production build | Verified | `tsc -b && vite build` |
| Mock HTTP end-to-end | Verified | preview → execute → Completed; see below |
| Playwright browser E2E (mock) | **Verified (2026-11-21)** | `npm run test:e2e`, 10 passed; [`frontend/e2e/README.md`](../../frontend/e2e/README.md) |
| Playwright browser E2E (pose-scope mock) | **Verified (2026-11-21)** | `E2E_POSE=1 npm run test:e2e`, 3 passed; preset → preview → execute, gripper preview, and the full 7-phase task run in mock; no robot |
| Playwright browser E2E (hardware) | **Verified read-only (2026-11-18)** | `npm run test:e2e:hardware`, 3 passed against the private gateway; environment-gated, never in CI |
| Camera + battery (Mock) | Implemented | `/api/peripherals`, Dashboard panels |
| Camera + battery (hardware) | **Verified read-only (2026-11-17)** | live RGB 640×400 `bgr8` and 7.424 V battery via gateway |
| Read-only ROS 2 gateway for camera/battery/state/tf | **Verified read-only + persistent service (2026-11-17); `/tf` verified 2026-11-18** | [`docs/e7-gateway-verification.md`](e7-gateway-verification.md), [`ros2_gateway/deploy/README.md`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/armpi-debug-studio/ros2_gateway/deploy/README.md) |
| G2 Trajectory Inspector (offline) | **Implemented, offline-validated (2026-11-18)** | [`docs/g2-trajectory-inspector.md`](g2-trajectory-inspector.md); 3-stream JSON import, recomputable metrics, client-side replay |
| G3 Experiment Lab (offline) | **Implemented, offline-validated (2026-11-18)** | [`docs/g3-experiment-lab.md`](g3-experiment-lab.md); immutable definitions, append-only paired trials, failure codes, CSV/JSON export, paired analysis |
| G4 Visual Workbench (read-only) | **Implemented; live `/tf` verified (2026-11-18)** | [`docs/g4-visual-workbench.md`](g4-visual-workbench.md); URDF FK + optional gateway `/tf`, camera/calibration/TF warnings, metadata-only references |
| G5 read-only trajectory recorder | **Implemented; no-motion + one supervised moving capture (2026-11-18)** | [`docs/g5-trajectory-recorder.md`](g5-trajectory-recorder.md); command + **command-cache** streams (not measured), MoveIt plan JSON, G2 dataset output; `camera_forward` 0.892 s moving capture imported; corrected 2026-11-21 |
| G6 supervised motion write path | **Implemented; verified without motion (2026-11-18); completion corrected 2026-11-21** | [`docs/g6-motion-write-path.md`](g6-motion-write-path.md); single joint, 0.096342 rad cap, **bus-read completion ±8 pulses**, latched `Unknown`; the old cache-based completion is no longer accepted |
| G7 pose takeover | **Implemented; verified with real motion and bus-servo completion (2026-11-21)** | [`docs/g7-pose-takeover.md`](g7-pose-takeover.md); 5-joint pose or gripper, 0.5 rad/s cap, FK workspace/table/low-yaw gate, 5 presets; view→grasp→view + gripper actual registers 502/339/928/336/499 and 184/645 |
| G8 task takeover | **Hardware-verified (2026-11-21): 13-step plan and the fast 7-phase path** | [`docs/g8-task-takeover.md`](g8-task-takeover.md); live point cloud → locate → deep-family fit → 7 phases → grip verify → put-back; dense S-curves at 4.0 rad/s / 20 rad/s², gateway per-segment validation; 13-step grasp actual 504/339/930/421/499, `close_grip` raw 416, landing +3.6 mm x; fast run 5.466 s commanded / 13.46 s wall, `close_grip` 414, `held: true`, landing 2.27 mm planar; min-range clip recovery (`TOP_CLIPPED_RECOVERED`) |
| G8.1 chassis approach | **Hardware-verified (2026-11-21), now merged into the run** | cube at x 0.357 m → two bounded base pulses (94 mm + ~42 mm) → target radius 0.2351 m → `close_grip` raw 412, `held: true`, landing +0.5 mm x; the fast run pulsed 13 mm inside the task run and re-planned; `APPROACH_REQUIRED` plan; gateway `POST /chassis/pulse` (duty ≤10, ≤2 s), `verification: open_loop`; locate region 0.15–0.55 m |
| G8 object forms (2026-11-23 catalog) | **Implemented + unit/mock-tested 2026-11-24; not yet hardware-run as an integrated task** | [`docs/phase2/OBJECT_GRASP_CATALOG.md`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/docs/phase2/OBJECT_GRASP_CATALOG.md), `configs/takeover.yaml` `task.forms`; blue block lying/upright, Ø29 cylinder standing, VLT carton (pose-only), lying cylinder/sachet refused; measured j4 held exactly, aim +27 mm for the cylinder; 任务接管 page selector and `--form` runner option |
| Hardware motion | **Off by default; G6 step or G7/G8 pose opt-in only; the G8.1 base pulse needs `--enable-motion` + the `chassis` block** | `ARMPI_HARDWARE_MOTION=1` + `ARMPI_SUPERVISED_MOTION_ACK` (step) or `ARMPI_MOTION_SCOPE=pose` + `ARMPI_TAKEOVER_ACK` (pose); otherwise `capabilities.hardware_motion=false` |
| Gripper (`r_joint`) | **G7/G8 pose scope only; excluded from G1/step** | `configs/takeover.yaml`; measured command range 170…700 raw, timed completion, 100 raw/s validation cap |
| 3D / depth-cloud workbench | **Not implemented** | Settings → Roadmap |

No hardware was accessed while building or testing the Mock slice: no serial
port, no ROS node, no robot motion. The read-only gateway was later verified on
the robot host (2026-11-17), its `/tf` path on 2026-11-18, and the G5 recorder
captured a 5 s no-motion segment plus one supervised `camera_forward` motion.
Hardware joint positions now come from measured `servo_states`; the command
echo remains available separately. Studio motion is off by default: a G6
single-joint supervised instance or a G7/G8 pose-mode instance can enable it,
each behind its own fixed acknowledgement phrase.

## Architecture

```text
Browser (React + TS)
   │ HTTP / WebSocket
   ▼
FastAPI backend
   ├── SafetySupervisor / LeaseManager / PreviewTokenManager
   ├── MockRobotAdapter (no ROS, no serial)
   ├── HardwareAdapter (read-only by default; G6 step / G7 pose motion opt-in)
   ├── SQLite event store (append-only, WAL)
   └── HTTP client ──> armpi_debug_gateway (robot host, ROS 2)
                          ├── /battery  <- /ros_robot_controller/battery
                          ├── /camera   <- /depth_cam/rgb/image_raw
                          ├── /state    <- joint_states + servo_states
                          └── /motion/* -> servo_controller (supervised only)
```

The browser never talks to ROS or a serial port. The backend exposes named
business actions with structured validation results, not raw device commands.

## Layout

```text
armpi-debug-studio/
├── backend/
│   ├── app/
│   │   ├── api/            REST + WebSocket routes
│   │   ├── adapters/       mock and hardware-stub implementations
│   │   ├── domain/         enums, models, state machine, mapping, trajectory analysis
│   │   ├── persistence/    SQLite event store
│   │   ├── safety/         validation, supervisor, lease, preview token
│   │   ├── config.py
│   │   ├── service.py
│   │   └── main.py
│   └── tests/              93 unit/API tests
├── configs/
│   └── joint_mapping.yaml  adopted EEPROM-bounded mapping (verified 2026-11-19)
├── ros2_gateway/           read-only gateway by default (runs on the robot)
│   ├── armpi_debug_gateway/  gateway, /tf cache, G5 recorder
│   ├── deploy/             host systemd unit, runner, token rotation
│   └── tests/              47 unit tests (no ROS needed)
└── frontend/
    ├── e2e/                Playwright suite (mock mode)
    ├── src/api/            typed client + zod contract checks
    ├── src/components/     AppShell and shared UI primitives
    ├── src/pages/          eight console pages (G1 five + G2 + G3 + G4)
    └── src/store/          zustand state + persisted display preferences
```

## Quick start (Mock mode; no ROS, no hardware)

> **Path note:** the commands below are relative to `armpi-debug-studio/`.
> If your prompt already ends in `armpi-debug-studio`, run `cd backend`, not
> `cd armpi-debug-studio/backend`. The Python project file is
> `backend/pyproject.toml`; the Node project file is `frontend/package.json`.

### Backend

```bash
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pip install -e ../../packages/jcheng_core --no-deps
uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

Useful environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `ARMPI_MODE` | `mock` | `mock` or `hardware` |
| `DB_PATH` | `../data/studio.db` | SQLite event store |
| `ARMPI_JOINT_MAPPING` | `../configs/joint_mapping.yaml` | mapping file |
| `BIND_HOST` | `127.0.0.1` | non-loopback binds require `ARMPI_BACKEND_TOKEN` |
| `ARMPI_BACKEND_TOKEN` | unset | optional shared token for `/api` and WebSocket auth; required for a non-loopback bind |
| `ARMPI_HARDWARE_MOTION` | `false` | supervised write path opt-in; requires a gateway URL and token |
| `ARMPI_SUPERVISED_MOTION_ACK` | unset | fixed phrase `I_HAVE_SUPPORT_AND_A_CLEAR_WORKSPACE`; required for any supervised hardware motion (`ARMPI_HARDWARE_MOTION=1`); the joint mapping itself is verified from 2026-11-19 |
| `ARMPI_GATEWAY_URL` | unset | e.g. `http://10.42.0.218:8765` (read-only, direct Ethernet) or `:8766` (supervised); WiFi is not reachable after the 2026-11-18 lockdown |
| `ARMPI_GATEWAY_TOKEN` | unset | shared token; never commit it |
| `ARMPI_CAMERA_GATEWAY_URL` | falls back to `ARMPI_GATEWAY_URL` | optional camera-only gateway; set to `http://10.42.0.218:8765` in pose takeover so `/camera/*` and `/aux-camera/*` come from the read-only instance while motion stays on 8766 |
| `ARMPI_CAMERA_GATEWAY_TOKEN` | falls back to `ARMPI_GATEWAY_TOKEN` | token for the camera gateway; both instances use the same token file |

`tools/studio/start_studio.sh --takeover` sets `CAMERA_GATEWAY_PORT=8765`
automatically; override it only if the read-only gateway moves.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open <http://127.0.0.1:5173>. The page must display `SIMULATION` and Mock data.
There is no serial access, no ROS node, and no hardware write path.

For a production build:

```bash
cd frontend
npm run build
```

If `frontend/dist/index.html` exists, the FastAPI app serves it at `/`.

### Tests

```bash
cd backend
. .venv/bin/activate
pytest                       # 93 passed

cd ../ros2_gateway
../backend/.venv/bin/python -m pytest   # 47 passed (no ROS needed)

cd ../frontend
npm run test:e2e             # builds, then 10 mock Playwright tests
npm run test:e2e:hardware    # gated read-only run; see e2e/README.md
```

The E2E suite starts its own mock backend on `127.0.0.1:8766` with a temporary
SQLite database and serves `frontend/dist`; it never touches the robot. The
hardware suite requires `ARMPI_E2E_HARDWARE=1`, a private gateway URL and a
token file, and is never part of CI. See
[`frontend/e2e/README.md`](../../frontend/e2e/README.md).

## Access from another device

> Replace `<user>` and `<M70Q_TAILSCALE_IP>` with your own SSH user and host.

G1 has **no login/authentication**. Do not expose the backend or the Vite dev
server to the public internet. Prefer SSH tunneling over a private link.

### Recommended: SSH tunnel, one port

On the machine running the backend:

```bash
cd frontend
npm install
npm run build
cd ../backend
. .venv/bin/activate
uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

On the other device:

```bash
ssh -L 8000:127.0.0.1:8000 <user>@<M70Q_TAILSCALE_IP>
```

Open <http://127.0.0.1:8000> on that device. FastAPI serves the built frontend
and the API on the same origin. If the backend was already running before
`npm run build`, restart it so the static mount is created.

### Remote Vite dev server over a trusted LAN

Keep the backend on localhost and expose only the Vite dev server:

```bash
# terminal 1: backend
cd backend
. .venv/bin/activate
uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000

# terminal 2: frontend
cd frontend
npm run dev -- --host 0.0.0.0
```

On another device on the same trusted LAN, open
`http://<M70Q_LAN_IP>:5173`. Vite proxies `/api` and `/ws` to the remote
backend. Allow TCP 5173 only on the trusted network.

### Frontend on the other device

If Node is not installed on the robot/remote host, run Vite on the other device:

```bash
# other device: tunnel the backend port
ssh -L 8000:127.0.0.1:8000 <user>@<M70Q_TAILSCALE_IP>

# other device, second terminal: copy the frontend once and run it
scp -r <user>@<M70Q_TAILSCALE_IP>:~/projects/ros2-industrial-arm-learning/armpi-debug-studio/frontend .
cd frontend
npm install
npm run dev
```

Open <http://127.0.0.1:5173> on that device. Vite proxies to the tunneled
`127.0.0.1:8000`.

### Direct binding (trusted LAN only)

If you explicitly want the backend reachable without a tunnel, it must use
the shared token:

```bash
ARMPI_BACKEND_TOKEN='<long-random-secret>' \
BIND_HOST=0.0.0.0 PORT=8000 \
.venv/bin/python -m uvicorn app.main:create_app --factory \
  --host 0.0.0.0 --port 8000
```

Build the frontend with the same token so it sends `X-Armpi-Token` (and the
WebSocket/CSV query token):

```bash
cd ../frontend
VITE_API_TOKEN='<same-secret>' npm run build
```

The backend refuses a non-loopback bind without `ARMPI_BACKEND_TOKEN`; this
is checked at startup, not per request. The token is embedded in the built
frontend, so terminate TLS at a reverse proxy that you control and never
port-forward the backend on the public internet. `/api/health` stays
unauthenticated for liveness; every other `/api` path requires the token.

If you serve the frontend from a different origin and call the API directly
instead of through the Vite proxy, the CORS allowlist only includes
`localhost:5173` and the `X-Armpi-Token` header. Use one of the tunnel/proxy
recipes above, or add an explicit CORS review before exposing it.

## Camera and battery

### Mock mode

The Dashboard shows a synthetic camera frame (`SVG`, labelled `MOCK CAMERA`) and
a 2S battery gauge around `7.65 V`. Both are explicitly `truth=simulated`.
Use Diagnostics → Mock fault injection to test:

- `battery_low` — low-voltage warning
- `battery_missing` — battery `Unavailable`
- `camera_offline` — camera `Unavailable`

Mock camera frames are generated placeholders; they are **not** camera data.

### Real hardware (read-only gateway)

Run `ros2_gateway/` on the robot host (see `ros2_gateway/README.md`). It
subscribes to:

| Data | Topic | Truth |
|---|---|---|
| Battery | `/ros_robot_controller/battery` (`std_msgs/UInt16`, mV) | `measured` |
| Camera | `/depth_cam/rgb/image_raw` (`sensor_msgs/Image`) | `measured` |
| Joint state | `/controller_manager/joint_states` | `commanded` (echo, not encoder feedback) |

Then start the backend with:

```bash
export ARMPI_MODE=hardware
export ARMPI_GATEWAY_URL=http://127.0.0.1:8765
export ARMPI_GATEWAY_TOKEN='replace-with-a-long-random-token'
uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

The gateway has **no motion endpoints**: `/motion/dispatch` and `/motion/stop`
return `501 NOT_IMPLEMENTED`. `hardware_motion` stays `false`; real joint motion
still requires mapping/limit verification and Board approval.

## API surface

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | mode, source label, capabilities |
| GET | `/api/state` | latest `RobotState` |
| GET | `/api/connection` | four-segment connection evidence |
| GET | `/api/peripherals` | battery + main camera + aux (D435i UVC) status |
| GET | `/api/peripherals/camera/snapshot` | latest main camera frame (JPEG or Mock SVG) |
| GET | `/api/peripherals/aux-camera/snapshot` | latest D435i UVC color frame (JPEG or Mock SVG) |
| GET | `/api/safety` | safety gate checks |
| GET | `/api/diagnostics` | mapping, defects, environment, probe availability |
| POST | `/api/diagnostics/probe` | simulated single-shot probe (Mock only) |
| POST | `/api/control/acquire` | acquire the single control lease |
| POST | `/api/control/heartbeat` | renew the lease |
| POST | `/api/control/release` | release the lease |
| POST | `/api/motion/preview` | validate target(s) and issue a single-use token; optional `profile` (dense S-curve) or `fast` (task gripper) |
| POST | `/api/motion/execute` | lease + token + safety gate → dispatch; a profile sends the dense waypoints |
| POST | `/api/motion/stop` | software stop request; four-state result |
| GET | `/api/events` | append-only audit events |
| GET | `/api/events/export.csv` | CSV export with schema version |
| GET | `/api/trajectories` | offline dataset summaries (G2) |
| POST | `/api/trajectories` | validate + analyze + store a dataset (no motion) |
| POST | `/api/trajectories/demo` | deterministic simulated demo dataset |
| GET | `/api/trajectories/{id}` | dataset + stored analysis |
| POST | `/api/trajectories/{id}/recompute` | re-run analysis and compare |
| GET | `/api/trajectories/{id}/export.csv` | aligned CSV (`sample=feedback\|commanded`) |
| GET | `/api/trajectories/{id}/export.json` | dataset + analysis + metadata |
| GET | `/api/experiments` | experiment summaries + failure-code map (G3) |
| POST | `/api/experiments` | create an immutable experiment definition |
| POST | `/api/experiments/demo` | deterministic simulated 16-pair demo |
| GET | `/api/experiments/{id}` | definition + plan + trials + analysis |
| POST | `/api/experiments/{id}/trials` | append one paired trial |
| GET | `/api/experiments/{id}/analysis` | recompute the paired analysis |
| GET | `/api/experiments/{id}/export.csv` | trial rows for script recomputation |
| GET | `/api/experiments/{id}/export.json` | definition + trials + analysis |
| GET | `/api/visual` | URDF FK frames + camera/calibration/TF metadata (G4) |
| GET | `/api/visual/camera-references` | metadata-only camera references |
| POST | `/api/visual/camera-references` | save a timestamp/calibration/TF reference |
| POST | `/api/mock/faults` | inject a Mock fault |
| POST | `/api/mock/reset` | clear Mock faults/state |
| WS | `/api/ws/state` | state stream (10 Hz) |
| WS | `/api/ws/events` | event stream |

`POST /api/motion/execute` requires an `Idempotency-Key` header (or body
`idempotency_key`) and the `command_id` + `preview_token` returned by preview.

## Safety invariants

- The backend never opens a serial port and never imports `rclpy`.
- The `ros2_gateway` process only subscribes; it has no motion publisher.
- Hardware Mode advertises `hardware_motion=false`; execute requests are
  rejected with `GATE_REJECTED`.
- Every execute requires a valid control lease, a valid single-use preview
  token, and all safety checks passing.
- The state machine never produces `Acknowledged`; the vendor SDK has no ACK.
- `Dispatched → Completed` is illegal; observed state must be explicit.
- Events are append-only. A storage failure latches a critical fault and locks
  out further motion.
- The UI says **停止请求** (stop request), never **急停** (emergency stop).
- Mock values are labelled `SIMULATION` and `truth=simulated` everywhere.

## Verification performed (2026-11-14)

```text
backend:  41 passed
frontend: tsc -b && vite build → success
HTTP E2E: health ok; state SIMULATION/5 joints; lease; preview valid;
          execute → Dispatched; idempotent replay → same command;
          after 1.2 s → joint1 position 0.1 rad, truth=simulated;
          events Requested/Validated/Dispatched/Observed/Completed;
          empty_feedback → all joints unavailable; reset → valid;
          stop → StopObserved with "not a hardware emergency stop";
          CSV export contains event_id.
```

These are Mock/simulation results. **They are not hardware validation.** No
ArmPi device was connected, no serial port was opened, and no motion command
was sent to a robot.

## Hardware read-only verification (2026-11-17)

```text
backend:  46 passed (41 baseline + 5 peripheral)
frontend: tsc -b && vite build → success
gateway:  /health all ready, motion_enabled=false
          /battery 7424 mV / 51.2% / truth=measured
          /camera  640x400 bgr8, 8.5 fps, JPEG snapshot
          /state   5 joints + gripper, truth=commanded
          no token → 401; POST /motion/dispatch → 501
backend:  mode=hardware; hardware_read=true; hardware_motion=false
          /api/peripherals battery + camera measured/valid
          /api/connection driver/gateway/feedback online
          preview valid → execute GATE_REJECTED
          blockers: mapping_verified, hardware_motion_enabled
UI:       LIVE; camera measured 640x400 bgr8; battery 7.42 V / 51%;
          camera renders a live frame, not the Mock SVG
```

Evidence:
[`evidence/studio_gateway/20261117/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_gateway/20261117/) and
[`docs/e7-gateway-verification.md`](e7-gateway-verification.md). The raw
camera image is private and not committed.

## G2 trajectory verification (2026-11-18, offline only)

```text
backend:  56 passed (48 existing + 8 trajectory tests)
frontend: tsc -b && vite build → success
browser:  8 Playwright mock tests passed (new: Trajectory Inspector)
demo:     deterministic simulated 5-joint dataset; feedback truth=simulated
analysis: command fidelity + tracking metrics; CSV recompute matches backend
replay:   client-side clock only; no new audit event during play/pause
```

No robot, gateway, serial port or ROS node was involved. The demo feedback is
synthetic and is not hardware validation. Evidence:
[`evidence/studio_g2/20261118/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_g2/20261118/). Contract and
metric definitions:
[`docs/g2-trajectory-inspector.md`](g2-trajectory-inspector.md).

## G3 experiment verification (2026-11-18, offline only)

```text
backend:  65 passed (56 existing + 9 experiment tests)
frontend: tsc -b && vite build → success
browser:  9 Playwright mock tests passed (new: Experiment Lab)
demo:     16 simulated pairs / 32 trials; data_origin=simulated
analysis: seeded bootstrap + exact sign/McNemar; CSV recompute matches backend
warning:  binary endpoint explicitly underpowered (not significance-tested)
```

No robot, gateway, serial port or ROS node was involved. The demo is synthetic
and is not hardware validation. Evidence:
[`evidence/studio_g3/20261118/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_g3/20261118/). Contract,
failure codes and analysis definitions:
[`docs/g3-experiment-lab.md`](g3-experiment-lab.md).

## G4 visual verification (2026-11-18, read-only)

```text
backend:  74 passed (65 existing + 8 visual tests + 1 cache-header test)
frontend: tsc -b && vite build → success
browser:  10 Playwright mock tests passed (new: Visual Workbench)
model:    URDF FK camera_forward mount matches SRDF note (x≈0.154, z≈0.282 m)
camera:   mock 640×400, truth=simulated; optical frame from measured hand-eye
gateway:  /tf endpoint and composition covered by no-ROS tests; live robot TF verified later (see G5)
safety:   read-only; camera references store metadata only; no motion events
```

No robot, gateway, serial port or ROS node was involved in the G4 build. URDF
limits were provisional at that build; they are verified for the operating
envelope since 2026-11-19. The camera snapshot is synthetic in mock mode, and
the model view is a read-only projection (not a live TF window). Evidence:
[`evidence/studio_g4/20261118/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_g4/20261118/). Contract and
warnings: [`docs/g4-visual-workbench.md`](g4-visual-workbench.md).

## G5 trajectory recorder and live TF verification (2026-11-18)

```text
backend:          76 passed (74 existing + G5 contract + token-payload test)
gateway:          24 passed (12 existing + 12 capture/TF tests)
frontend:         tsc -b && vite build → success
mock browser:      10 Playwright tests passed
hardware browser:  3 Playwright tests passed (LIVE, live /tf, GATE_REJECTED)
static recorder:   5 s no motion; 242 commanded / 241 measured at ~48 Hz
moving recorder:   supervised camera_forward, 0.892 s; 712/711 samples
G2 moving import:  tracking max 0.008321 rad / RMSE 0.003962 rad
live /tf:          11 dynamic + 8 static edges; status=ok; freshest ~15-50 ms
link5 comparison:  gateway composition vs tf2_echo < 0.4 mm
```

The recorder only subscribes; it creates no publisher, service client or motion
endpoint. The hardware Playwright run is environment-gated and never runs in
CI. The one moving capture is a small `camera_forward` move, not a grasp or a
tracking benchmark. Evidence:
[`evidence/studio_g5/20261118/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_g5/20261118/). Contract and
limitations: [`docs/g5-trajectory-recorder.md`](g5-trajectory-recorder.md).

## Known limitations

1. The read-only gateway runs as an enabled host systemd service and stop/start
   with clean SIGTERM is verified. A full reboot test now **passes** (2026-11-20,
   `NRestarts=0` for the gateway, D435i depth and aux-camera units); a
   vendor-container restart still triggers the vendor `startup_check`, which
   moves the arm. Both `/joint_states` and `/controller_manager/servo_states`
   are the vendor **command cache**; the actual registers are
   `GetBusServoState` reads, and the Studio labels the continuous stream
   `commanded`. Rotate the shared token with `deploy/rotate-token.sh` before
   sharing the robot; the script is provided but was not exercised in this run.
2. Hardware motion is **off by default**. G6 adds a supervised single-joint
   write path and G7 adds a five-joint pose + gripper path, each behind its own
   gateway instance and acknowledgement phrase. G7 is not a planner: the FK gate
   is TCP-only and nominal, there is **no self-collision model**, no trajectory
   streaming and no MoveIt action dispatch. The vendor driver defects (no ACK,
   blocking reads, `joint_states` command echo) are not repaired.
3. The joint mapping and limits are **verified** (`verified: true` from
   2026-11-19, `mapping_verified=true` for the operating envelope). All five arm
   joints were hand-surveyed with torque released; joint2's high bound was
   tightened to 775 and the live stack was reloaded on 2026-11-20. The stops are
   pose-dependent self-collision limits, so this is not a global mechanical-limit
   claim. See [`docs/phase2/JOINT_LIMITS.md`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/docs/phase2/JOINT_LIMITS.md).
4. Backend API is unauthenticated and bound to localhost by default. The
   gateway uses a shared token. Do not expose either on a LAN/public interface
   without a separate security review.
5. The committed Playwright suite covers mock mode by default; the gated
   hardware spec runs only with `ARMPI_E2E_HARDWARE=1` against a private
   gateway. There are no visual-regression baselines.
6. The vendor container has no `cv_bridge`; the gateway uses its numpy fallback
   for `bgr8`/`rgb8`/`mono8`. Other encodings report an error frame instead of
   being guessed.
7. The gateway uses the default RMW (Fast DDS). Topics interoperate with the
   vendor CycloneDDS nodes, but services do not; the read-only gateway does not
   call services. Any future service call must run with the vendor `.zshrc`
   environment (`RMW_IMPLEMENTATION=rmw_cyclonedds_cpp`, `CYCLONEDDS_URI`).
8. G2 has no **moving** hardware trajectory and no rosbag2 import yet; a G5
   no-motion capture imported successfully, the demo is synthetic, and the
   timebase is a declared shared start (no latency claim).
9. G3 is offline-only: no hardware trial exists, artifact references are not
   read or validated, trial records cannot be edited/voided, and the demo's
   binary endpoint is deliberately underpowered.
10. G4's gateway `/tf` path is now verified on the robot, including a
    `base_link→link5` comparison with `tf2_echo` below 0.4 mm. Without that
    endpoint, frames fall back to URDF forward kinematics from the current
    joint truth (commanded in hardware mode); URDF meshes remain model
    estimates (joint limits are verified for the operating envelope since
    2026-11-19), the view is an SVG projection rather than a mesh render, and
    saved camera references contain metadata only.
11. G5 has one **small supervised moving capture** (`camera_forward`, 0.892 s),
    not a grasp, repeatability study or tracking benchmark. Its measured error
    is dominated by servo pulse quantization and a one-period start offset. The
    planned stream must match the executed plan, `rosbag2` is still not
    imported, and the timebase is the recorder's monotonic clock rather than a
    cross-device synchronization claim.

## Next steps

1. Verify the gripper closed loop on a real object: command close, record the
   settled `GetBusServoState` register, and confirm the timed completion is not
   mistaken for a force/grip proof.
2. Add a **continuous** measured-position option if one is needed: the actual
   read is currently one-shot at completion; the vendor `GetBusServoState`
   service would have to be polled at a low rate (with the negative-register
   guard), or the vendor driver patched to publish a real register stream.
3. ~~Add a task-level takeover (G8)~~ — **done 2026-11-21** (camera → locate →
   fit → 13-step task, bus-verified; the pose gateway captures the registered
   cloud on demand). Next: G8.1, a chassis approach for cubes outside the
   direct-grasp band, using the front D435i depth + base pulses.
4. Correct the G5 recorder if its feedback label matters for new data: it
   subscribes to the command cache; a real bus-read stream or an explicit
   `commanded` label is required.
5. Keep `docs/phase2/JOINT_LIMITS.md`, the safety plane and the pose presets in
   sync whenever the mapping or the base pose changes.

Completed on 2026-11-19/21 (were on previous lists): formal joint-limit survey;
the G6 single-joint supervised path; the G7 five-joint pose and gripper path
verified with **real motion** and bus-servo completion (low pose within 4
pulses; gripper settled 184/645); the G8 camera-to-task takeover verified with a
real 13-step run (grasp actual 504/339/930/421/499, `close_grip` raw 416,
landing 3.6 mm) including the min-range clip recovery; the command-cache
discovery and the cache-only completion rejection. Earlier: gateway `/tf` verified; hardware-mode
Playwright gated behind `ARMPI_E2E_HARDWARE=1` and passing 3/3; the G5 recorder
with a supervised moving `camera_forward` capture imported into G2 (now known
to be a command-cache capture).
