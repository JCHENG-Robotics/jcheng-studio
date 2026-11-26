# E7 read-only gateway — hardware verification

Date: 2026-11-17

Scope: real ArmPi Ultra Pi 5, direct Ethernet, read-only gateway + backend
`hardware` mode + Dashboard. **No motion was dispatched.**

## 1. Environment

| Item | Value |
|---|---|
| Robot | Pi 5, `eth0=10.42.0.218/24`, vendor ROS 2 Humble stack running |
| ROS runtime | vendor container `ArmPiUltra` (`ros:humble`, host network) |
| Laptop | studio backend `127.0.0.1:8000`; direct link `10.42.0.1/24` |
| Gateway | `armpi_debug_gateway` bound to `10.42.0.218:8765`, token required |

The container has OpenCV 4.10 and numpy 1.26 but no `cv_bridge`. The gateway
therefore used its numpy fallback (`ros2_gateway/armpi_debug_gateway/imaging.py`)
to decode the `bgr8` RGB stream. This was the first live exercise of that path.

## 2. Gateway results

```text
GET /health            status=ok; ros/battery/camera/joints ready; motion_enabled=false
GET /battery           7424 mV; 51.2%; 2S; truth=measured; validity=valid
GET /camera/status     640x400 bgr8; 8.5 fps; truth=measured
GET /camera/snapshot   JPEG 640x400; 20415 bytes
GET /state             5 joints + gripper; truth=commanded
GET /health (no token) 401
POST /motion/dispatch  501 NOT_IMPLEMENTED
```

Raw responses: [`evidence/studio_gateway/20261117/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_gateway/20261117/).

## 3. Backend hardware mode

Started with:

```bash
export ARMPI_MODE=hardware
export ARMPI_GATEWAY_URL=http://10.42.0.218:8765
export ARMPI_GATEWAY_TOKEN='<session token>'
export DB_PATH=/tmp/armpi_studio_hardware_20261117.db
uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

Results:

```text
GET /api/health        mode=hardware; source_label=LIVE; hardware_motion=false; hardware_read=true
GET /api/peripherals   battery + camera measured/valid; 7.424 V; 640x400; 8.5 fps
GET /api/connection    driver/gateway/feedback online; serial unknown (vendor-owned)
GET /api/state         joints commanded/valid; limits provisional
GET /api/safety        blockers: mapping_verified, hardware_motion_enabled
POST /api/motion/preview   valid; single-use token issued
POST /api/motion/execute   GATE_REJECTED; same two blockers
```

The execute request carried a valid lease, a valid preview token and an
`Idempotency-Key`; the backend still rejected it before any adapter dispatch.
The gateway has no motion endpoint, so two independent layers kept the arm
still.

## 4. Dashboard (browser)

The built frontend was served by FastAPI and opened at
`http://127.0.0.1:8000/`:

- Header: `Source LIVE`, `Connection ReadOnly`, `Feedback age` in milliseconds.
- Camera · RGB: `measured`, `valid`, 640×400, `bgr8`, ~5.8–8.5 fps, a live
  workspace frame (not the Mock SVG).
- Battery · 2S: `measured`, `ok`, 7.42 V, 51%, source
  `armpi_debug_gateway`, `valid`.
- Connection chain: driver/gateway/feedback `online`.
- Safety gate: 2 blockers (`mapping_verified`, `hardware_motion_enabled`).
- Joint table: 5 joints, `commanded`/`valid`, limits marked provisional.
- Active faults: `HARDWARE_MOTION_DISABLED`.

The browser DOM snapshot and a screenshot were taken during the run. The
screenshot contains the live workspace image and is **not** committed; the
numerical evidence and the exact UI text are in the evidence directory.

## 5. Persistent service (2026-11-17, later)

The manual `docker exec` start was replaced by a host systemd unit:

| Item | Value |
|---|---|
| Unit | `armpi-gateway.service` (`enabled`, `active`) |
| Start | `/usr/local/lib/armpi-gateway/armpi-gateway-run.sh` → `docker exec` into `ArmPiUltra` |
| Bind | `10.42.0.218:8765` |
| Token | `/home/pi/docker/tmp/armpi_gateway_deploy/token` (mode 0600, uid 1000) |
| Container restart policy | `always` (pre-existing) |

Verified:

```text
systemctl is-enabled armpi-gateway   enabled
systemctl stop                        clean SIGTERM, "received signal 15", service inactive
in-container process after stop       none; port 8765 free
systemctl start                       active; GET /health 200
GET /health                           ros/battery/camera/joints ready, motion_enabled=false
GET /battery                          7424 mV
GET /camera/status                    640x400 bgr8
GET /health without token             401
```

The backend was re-checked against this service in `hardware` mode:
`/api/health` was `LIVE` (`hardware_motion=false`, `hardware_read=true`),
`/api/peripherals` returned 7.424 V and 640×400, preview was valid, and execute
returned **403 `GATE_REJECTED`** with blockers `mapping_verified` and
`hardware_motion_enabled`. No motion was dispatched. Raw output:
[`persistent/backend_hardware_20261117.txt`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_gateway/20261117/persistent/backend_hardware_20261117.txt).

Not verified: a full reboot or a vendor-container restart. Both trigger the
vendor `startup_check`, which moves the arm, so the operator deferred them.
The unit is enabled, `Requires=docker.service`, waits up to 60 s for the
container, and `Restart=always`; the reboot path is therefore configured but
not hardware-tested. See
[`ros2_gateway/deploy/README.md`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/armpi-debug-studio/ros2_gateway/deploy/README.md).

## 6. What this does NOT prove

- No hardware motion: execute is blocked, and the gateway has no write path.
- No encoder feedback: `/controller_manager/joint_states` is a command echo.
- **Superseded 2026-11-19:** joint limits were provisional at the time of this
  2026-11-17 verification; the physical torque-off survey has since completed
  and `mapping_verified=true` for the operating envelope. See
  [`docs/phase2/JOINT_LIMITS.md`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/docs/phase2/JOINT_LIMITS.md).
- No reboot/container-restart test at the time of this 2026-11-17 verification
  (see section 5). **Superseded 2026-11-20:** the full reboot test passed with
  `NRestarts=0`; a container restart still moves the arm via `startup_check`.
- No hardware-mode Playwright run: the committed suite covers mock mode only;
  the hardware UI check above was a one-off browser session.
- No authentication review: the gateway uses a shared token and was bound only
  to the direct-Ethernet address. Do not expose it on a shared/public network.
- The gateway currently uses the default RMW (Fast DDS). DDS topics are
  readable across it and the vendor CycloneDDS stack, but services are not
  interoperable. This is harmless for the read-only gateway, which subscribes
  only; any future service call must run with the vendor `.zshrc` environment.

## 7. Reproduce

See
[`evidence/studio_gateway/20261117/README.md`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_gateway/20261117/README.md)
for the copy/start commands and the full payload list. Rebuild the frontend
(`npm run build`) before starting the backend if `frontend/dist` is stale.
