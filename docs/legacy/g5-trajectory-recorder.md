# G5 read-only trajectory recorder

> Status: **implemented and validated on the robot for a no-motion capture and
> one supervised moving `camera_forward` capture (2026-11-18)**. The moving
> capture is a small 0.89 s motion with a real **command stream**; it is not a
> grasp or a broad tracking study. The recorder is subscribe-only, is not part
> of the gateway HTTP service, and never enters the safety or motion chain.

> **Corrected 2026-11-21.** `/controller_manager/servo_states` is **not** a bus
> read. The vendor `ServoManager.set_position` writes the commanded pulse into
> the cache that both `/joint_states` and `/servo_states` publish. The G5
> `feedback` stream is a second view of the command (including the vendor's
> interpolation step), so its "tracking error" is plan-vs-command, not servo
> tracking. The actual registers require the `GetBusServoState` service; see
> [`g7-pose-takeover.md`](g7-pose-takeover.md) and
> [`evidence/studio_g7/20261121/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_g7/20261121/README.md).
> The G5 dataset keeps its raw values, but `feedback.truth` must be read as
> `commanded`.

## Scope

The recorder closes the data-capture gap left by G2:

- `planned` comes from the MoveIt plan that is about to be executed;
- `commanded` comes from the vendor controller's joint-state command echo;
- `feedback` comes from the vendor `servo_states` **command cache** (labelled
  `measured` in the historical dataset; corrected to `commanded` here);
- the result is a G2 `schema_version: 1` dataset that can be imported unchanged
  through `POST /api/trajectories` or the Trajectory Inspector page.

It does **not** plan, execute or dispatch motion. The operator runs the motion
with the existing MoveIt tool; the recorder only listens.

## Streams and topics

| Stream | Topic / source | Truth | Observed rate |
|---|---|---|---|
| `commanded` | `/controller_manager/joint_states` | `commanded` | ~48.8 Hz (command echo) |
| `feedback` | `/controller_manager/servo_states` | **`commanded`** (historical label `measured`) | ~47.7 Hz (command cache) |
| `planned` | MoveIt plan JSON (`--trajectory-json`) | `planned` | waypoint count from the plan |

`servo_states` (`servo_controller_msgs/msg/ServoStateList`) publishes raw servo
pulses. The recorder converts them with the vendor mapping:

```text
joint1..joint5 = servo 6..2
joint3 is inverted: (500 - pulse) * rad_per_tick
others:             (pulse - 500) * rad_per_tick
rad_per_tick = (240° / 360°) * 2π / 1000
```

A live check on 2026-11-18 matched the `joint_states` values exactly (for
example `joint1 501 → 0.00418879 rad`, `joint3 800 → -1.25664 rad`). A pulse
outside `0..1000` aborts the capture instead of being converted; this is the
known vendor-node crash trigger seen during the joint-limit survey.

## Start alignment

The dataset timebase is `shared_trajectory_start`, recorded with one monotonic
clock inside the recorder:

1. In the default `--mode motion`, the first commanded sample is the baseline.
2. The recorder arms when any commanded joint changes by at least
   `--motion-threshold` radians (default `0.002`, below one servo tick of
   `0.00419` rad).
3. The last unchanged sample defines `t=0`; the planned trajectory's
   `time_from_start` therefore starts at the same hold state. Alignment is
   within one controller period (about 20 ms), not a cross-device latency
   claim. The first moving capture used `0.005`, which is below one tick but
   large enough that a joint could move one tick without arming; the first
   sample was therefore one controller period into the motion. Use the default
   for future captures.
4. `--mode manual` starts at the first received sample. It is intended for
   no-motion checks and for hand-written fixtures; the operator then owns the
   planned offset.

The recorder refuses to overwrite an existing output file unless `--overwrite`
is passed, and caps `--duration` at 300 s.

## Run on the robot

The recorder is part of the deployed gateway package (it is not started by the
systemd unit). Run it in the vendor container with the vendor ROS environment:

```bash
sudo docker exec -u ubuntu ArmPiUltra /bin/bash -lc '
  source /opt/ros/humble/setup.bash
  source /home/ubuntu/ros2_ws/install/setup.bash
  cd /home/ubuntu/shared/armpi_gateway_deploy
  python3 -m armpi_debug_gateway.recorder \
    --mode motion --duration 15 \
    --planned-json /tmp/camera_forward_plan.json \
    --capture-out /home/ubuntu/shared/g5_evidence/capture.json \
    --dataset-out /home/ubuntu/shared/g5_evidence/dataset.json \
    --name "camera_forward real capture" \
    --operator "$USER"
'
```

A half-second status line reports how many samples are buffered and whether
the recorder is armed. `Ctrl-C` stops the session and still writes the partial
capture.

### Planned stream

`tools/phase2/real_moveit_execute.py` now has `--trajectory-json`:

```bash
python3 real_moveit_execute.py camera_forward \
  --trajectory-json /tmp/camera_forward_plan.json
```

The recorder reads the planned file when it finishes, so the file may be
written by the execute run while the recorder is still collecting. The JSON
contains `joint_names`, `points` (`t_s`, `positions`, optional `velocities`) and
the plan provenance. The recorder selects the arm joints and rejects missing
or non-increasing data.

### Offline assembly

If the planned file is produced after the capture (or the recorder was started
without `--dataset-out`), assemble locally:

```bash
armpi-debug-studio/backend/.venv/bin/python \
  armpi-debug-studio/tools/build_g5_dataset.py \
  --capture evidence/studio_g5/20261118/static_capture.json \
  --planned-json /tmp/camera_forward_plan.json \
  --out /tmp/g5_dataset.json \
  --name "camera_forward real capture"
```

`dataset_from_capture` preserves the capture notes, topics, abort flags and
sample counters in `metadata`.

## Import into G2

The output is a normal G2 dataset:

```bash
curl -s -X POST -H 'Content-Type: application/json' \
  --data-binary @/tmp/g5_dataset.json \
  http://127.0.0.1:8000/api/trajectories
```

`feedback.truth=measured` means tracking metrics are computed. The static
validation produced `max_abs_error_rad=0` because nothing moved; that is a
correct no-motion result, not evidence of tracking accuracy.

## Validation evidence (2026-11-18)

Raw files are in [`evidence/studio_g5/20261118/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_g5/20261118/README.md).

```text
static capture        5 s, no motion
commanded samples     242 (~48.16 Hz)
measured samples      241 (~48.15 Hz)
planned stream        2-point hold fixture
G2 import             accepted; tracking available; 0 rad error (static)

moving capture        supervised camera_forward execute, 0.892 s plan
commanded samples     712 (~47.37 Hz)
measured samples      711 (~47.38 Hz)
planned stream        10 MoveIt waypoints from the executed plan
G2 tracking           max 0.008321 rad, RMSE 0.003962 rad,
                      mean 0.003659 rad, final 0.004048 rad
G2 command fidelity   max 0.008326 rad, RMSE 0.003963 rad
hardware Playwright   3 passed (LIVE battery/camera, live /tf, GATE_REJECTED)
gateway /tf           11 dynamic + 8 static edges; freshest ~15-50 ms
base_link -> link5    gateway composition vs tf2_echo: < 0.4 mm
```

The moving capture's error is dominated by servo pulse quantization
(`0.00419 rad` per tick) and by the one-controller-period start offset from the
`0.005` arm threshold used in that run. It is a real measured trajectory, but
it is not a calibrated tracking-accuracy result. The `/tf` comparison is
independent only in transport: `tf2_echo` and the gateway subscribe to the same
vendor `/tf` graph. It verifies the gateway's edge cache and the Workbench
composition, not the vendor kinematics.

## Safety isolation

- The recorder imports `JointState` and `ServoStateList`; it creates no
  publisher, service client, action client or motion timer. A source-scan test
  fails if a write path is added.
- It does not call the bus-servo `set_state`, torque or position services.
- It does not start or restart the vendor container or the vendor ROS stack.
- It writes files only at the end of a session (atomic replace) and refuses to
  overwrite unless asked.
- An out-of-range servo pulse stops the session; the partial capture is kept
  with `aborted=true` and the issue in `recorder.abort_issues`.

## Limitations

1. **One small moving capture only.** The evidence is a supervised
   `camera_forward` move of 0.892 s, not a grasp, a repeatability study or a
   broad tracking benchmark. The remaining motion work needs new supervised
   runs and a stable start-alignment convention.
2. The planned stream is only as exact as the file supplied. Use the
   `--trajectory-json` written by the execution run; a separate plan-only run
   may produce a different trajectory.
3. Cross-device latency is not measured. The timebase is the recorder's
   monotonic clock; the planned stream uses the plan's relative
   `time_from_start`.
4. `rosbag2` import is still not implemented. A bag remains a metadata
   reference in G3; convert it externally before importing.
5. Joint mapping and limits are verified for the operating envelope
   (`verified: true` from 2026-11-19); the stops are pose-dependent
   self-collision limits, not a global mechanical claim. `r_joint` is excluded
   from the arm stream.
6. The feedback is bus-servo position, not an independent encoder. It is
   measured at the servo, but it still depends on the vendor controller's
   reporting path.
