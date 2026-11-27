# Studio operator tools

These scripts were ported with the console from the frozen
`jcheng-manipulation` baseline. They call the **local Studio backend** over
HTTP; they never open a device, join DDS or talk to ROS. The robot-side legacy
gateway (`armpi-debug-studio/ros2_gateway`) stayed in the baseline and is not
part of this repository.

Motion runners are operator-only. They require an explicit `--execute` flag
and a clear-workspace acknowledgement; they are never used by CI, a service
unit or an unattended process. The console is not an e-stop.

## Start the console

`start_studio.sh` starts the backend on `127.0.0.1`. Mock mode needs no robot;
legacy hardware mode points the backend at an already-running legacy gateway.

```bash
# mock mode (default; no robot, no gateway)
tools/studio/start_studio.sh

# offline demo (full mock UI, DEMO banner, never executes motion)
tools/studio/start_studio.sh --demo

# read-only legacy gateway mode
ARMPI_GATEWAY_URL=http://<gateway-host>:8765 \
ARMPI_GATEWAY_TOKEN_FILE=~/.config/armpi-studio/gateway-token \
  tools/studio/start_studio.sh --hardware

# legacy pose takeover (motion; operator present)
ARMPI_GATEWAY_URL=http://<gateway-host>:8766 \
ARMPI_GATEWAY_TOKEN_FILE=~/.config/armpi-studio/gateway-token \
ARMPI_TAKEOVER_ACK=I_AM_AT_THE_ROBOT_WITH_A_CLEAR_WORKSPACE \
  tools/studio/start_studio.sh --takeover
```

The token file must be mode `0600`; the script refuses to start otherwise. It
does not fetch a token over SSH, does not start a ROS process and does not kill
a process that already holds the port. The event database defaults to
`~/.local/state/armpi-studio/studio.db`; the log defaults to
`~/.local/state/armpi-studio/console.log`.

Open <http://127.0.0.1:8000>. Stop with the printed PID.

The laptop watchdog and the robot-side health/restart policy belong to
[`JCHENG-Robotics/jcheng-ops`](https://github.com/JCHENG-Robotics/jcheng-ops),
not this repository.

## Pose takeover (G7)

`--takeover` exports `ARMPI_HARDWARE_MOTION=1`, `ARMPI_MOTION_SCOPE=pose` and
the acknowledgement, and defaults the gateway to **8766**. The pose gateway
must already be running with a pose-mode motion contract; the baseline
procedure is
[`armpi-debug-studio/ros2_gateway/deploy/takeover.md`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/armpi-debug-studio/ros2_gateway/deploy/takeover.md).

No-motion verification of a running pose backend (never calls execute):

```bash
python3 tools/studio/verify_takeover.py \
  --backend http://127.0.0.1:8010 \
  --gateway http://<gateway-host>:8766 \
  --out /tmp/g7_no_motion.json
```

The staged real sequence (preview first; `--execute` is the only motion path):

```bash
python3 tools/studio/g7_first_motion.py \
  --backend http://127.0.0.1:8000 \
  --session operator-onsite \
  --out /tmp/g7_first_motion.json \
  --execute --i-have-clear-workspace --steps view_table,grasp_low,view_table_return
```

It aborts on any rejected preview, `Failed` or `Unknown`; it never retries.
Baseline evidence:
[`evidence/studio_g7/20261121/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_g7/20261121/README.md).

## Task takeover (G8)

With the arm at the `view_table` preset and the legacy pose gateway started
with `--pointcloud-topic /depth_cam/depth_registered/points`, one command plans
the **7-phase fast task** from a live registered cloud and runs it phase by
phase. Each pose phase is one dense S-curve; each grip phase is one timed raw
bus-servo command.

```bash
# plan only (read-only: capture + locate + deep-family fit + validation)
python3 tools/studio/g8_task_run.py --backend http://127.0.0.1:8000 \
  --out /tmp/g8_plan.json

# execute (operator beside the robot; every phase bus-verified)
python3 tools/studio/g8_task_run.py --backend http://127.0.0.1:8000 \
  --session operator-g8 --out /tmp/g8_run.json \
  --execute --i-have-clear-workspace
```

For a measured non-cube object pass `--form <id>` (for example
`--form block_lying`). The ids, measured j4 depth, gripper closure and aim
correction live in `configs/takeover.yaml` `task.forms`; the reference page is
[`docs/phase2/OBJECT_GRASP_CATALOG.md`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/docs/phase2/OBJECT_GRASP_CATALOG.md).
Omit `--form` for the 39.4 mm cube. A `refused` form stops with
`FORM_REFUSED`; the VLT carton (`pose`) stops with `FORM_POSE_ONLY` and is
executed through pose takeover. Treat the first run of a form as a
commissioning run.

The plan is refused unless the arm is within 0.12 rad of the view and every
command passes the joint limits, the profile velocity cap and the FK gate. A
`TOP_CLIPPED_RECOVERED` warning means the Aurora's min-range sphere (~0.25 m)
hid the near part of the top face and the centre was rebuilt from the intact
far edge with the measured 39.4 mm cube. The runner aborts on a rejected
preview or a non-`Completed` event and never retries. Baseline evidence:
[`evidence/studio_g8/20261121/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_g8/20261121/README.md).

### Out-of-band cube (G8.1)

If the plan answers `APPROACH_REQUIRED`, the run still starts: it pulses the
base toward `(0.230, 0)` (up to three bounded pulses, each followed by a fresh
cloud and a new plan) and continues with the arm phases automatically. The
standalone G8.1 runner is still available for a base-only alignment:

```bash
python3 tools/studio/g8_approach.py --backend http://127.0.0.1:8000 \
  --out /tmp/g8_approach.json

python3 tools/studio/g8_approach.py --backend http://127.0.0.1:8000 \
  --session operator-g81 --out /tmp/g8_approach.json --execute \
  --i-have-clear-workspace
```

The base is open-loop: the pulse response is `verification: open_loop` and the
camera re-measurement is the only confirmation. Baseline evidence:
[`evidence/studio_g81/20261121/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_g81/20261121/README.md).

## Environment overrides

| Variable | Default |
|---|---|
| `PORT` | `8000` |
| `ARMPI_GATEWAY_URL` | unset (mock mode) |
| `ARMPI_GATEWAY_TOKEN_FILE` | unset; file must be `0600` |
| `ARMPI_STUDIO_STATE_DIR` | `~/.local/state/armpi-studio` |
| `DB_PATH` | `$ARMPI_STUDIO_STATE_DIR/studio.db` |
| `LOG` | `$ARMPI_STUDIO_STATE_DIR/console.log` |

The backend binds to `127.0.0.1` only. Reach it from another device with an SSH
tunnel if the operator runbook allows it; never bind `0.0.0.0`.
