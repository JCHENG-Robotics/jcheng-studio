# G7 — Pose takeover

Status: **implemented and verified with real motion on 2026-11-21**. A full
sequence ran on the robot — `view_table → grasp_low → view_table`, then gripper
open/close — and every arm completion is a **`GetBusServoState` bus read**, not
the vendor command cache. The default gateway and backend are still read-only;
pose takeover is a separate, explicitly acknowledged operating scope.

> **Correction (2026-11-21):** the vendor
> `/controller_manager/servo_states` topic is **not** a measured position. The
> vendor `ServoManager.set_position` writes the commanded pulse into the cache
> that both `/joint_states` and `/servo_states` publish. The actual registers
> are only available through the `GetBusServoState` service, and they differ
> from the cache by up to ~8 pulses (observed) — the gripper close cache says
> 700 while the settled register is ~645. G6's cache-based completion and G5's
> "measured feedback" are affected; see
> [`evidence/studio_g7/20261121/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_g7/20261121/README.md).

## Why this milestone

G1–G5 built a read-only console plus offline analysis. G6 added a deliberately
tiny write path: one arm joint, 23 pulses (0.096 rad), one message. That is a
commissioning probe, not a control surface — the operator cannot put the arm
at a working pose with it, and the gripper was excluded entirely.

G7 closes that gap without pretending the Studio is a planner. One command is
either **one whole five-joint pose** or **one gripper command**, and the
backend applies the three checks that are backed by measured evidence:

1. the verified joint limits from `joint_mapping.yaml`,
2. a **velocity cap** because the vendor interpolates one `ServosPosition`
   linearly over `duration` (no acceleration limit exists in this path),
3. an FK gate over the **straight joint-space interpolation**: the
   `armpi_safety` workspace box, the measured tilted table plane with its
   10 mm clearance, and the measured low-pose yaw rule.

The Studio is not a collision checker. The gate is TCP-only and nominal-FK
based; it does not model self-collision. The operator-facing contract says so
in the UI banner and in every preview.

## Defense in depth

A pose command must pass six independent layers. No layer trusts a previous
one.

| Layer | Enforces | Where |
|---|---|---|
| 1. Backend startup | `ARMPI_MOTION_SCOPE=pose` requires `ARMPI_HARDWARE_MOTION=1`, a verified mapping, a gateway URL/token, and the fixed `ARMPI_TAKEOVER_ACK=I_AM_AT_THE_ROBOT_WITH_A_CLEAR_WORKSPACE` phrase. | `backend/app/config.py` |
| 2. Backend validation | exactly the five arm joints **or** exactly `r_joint`; verified joint bounds; velocity ≤ 0.5 rad/s; gripper command range and 120 raw/s tracking speed; duration 1…10 s. | `backend/app/safety/validation.py` |
| 3. FK pose gate | 21 samples of the straight interpolation; workspace box; table plane + 10 mm; low-pose yaw ≤ ±0.20 rad below TCP z 0.09 m. The measured start is exempt so a non-compliant arm can always be commanded back. | `backend/app/safety/pose_gate.py` |
| 4. Backend safety gate | lease, position-source freshness, no active fault, interlock, gateway motion capability, and a position for every arm joint. The continuous source is the command cache (`commanded`); each move is bus-verified. `GATEWAY_SCOPE_MISMATCH` blocks a pose backend pointed at a step gateway. | `backend/app/safety/supervisor.py` |
| 5. Gateway validation | independent JSON contract: `mode=pose`, five-joint-or-gripper, velocity cap, per-joint pulse bounds, fresh command-cache pulses, one active command. | `ros2_gateway/armpi_debug_gateway/motion.py` |
| 6. Bus verification | after `duration + settle`, one guarded `GetBusServoState` read; an arm target completes only when the **actual registers** are within ±12 pulses, otherwise `Unknown` is latched. The gripper is timed and records the settled register. The read is skipped if a joint's last pulse is below 25 (a negative register crashes the vendor node). | `ros2_gateway/armpi_debug_gateway/node.py` |
| 7. Vendor driver | publishes one `ServosPosition` with `position_unit=pulse` and the duration; the vendor controller clamps 0..1000 and interpolates. | `ros2_gateway/armpi_debug_gateway/node.py` |

The gateway is a separate process. A backend bug cannot publish an
out-of-limit pulse, a partial pose, an over-speed move, a stale-feedback
command, or a second command while one is executing. A command cannot claim
`Completed` from the vendor command cache.

## Effective limits

| Item | Value |
|---|---|
| Command shape | exactly 5 arm joints, or exactly `r_joint` |
| Velocity | ≤ **0.5 rad/s** per joint (`armpi_safety` vetoes above 1.2) |
| Duration | 1.0 … 10.0 s (arm); 1.0 … 6.0 s (gripper) |
| Position limits | adopted 2026-11-19 EEPROM/measured bounds (joint2 high 775) |
| Gripper command range | raw **170 (open) … 700 (close)**; observed settled registers: open ~183, close **~645** (the end-of-interpolation read can show 700 before the linkage relaxes) |
| Gripper completion | **timed** (5 s) + one settled `GetBusServoState` read at +1.2 s; the register is evidence, not proof of force |
| Pose completion | **actual bus registers** within ±12 pulses for all five joints; read at `duration + 0.5 s`; deadline `duration + 0.5 + 3.0 s` |
| Position source | the continuous stream is the vendor command cache (`truth=commanded`); the actual is a one-shot `measured_on_demand` read at completion |
| Feedback age | ≤ 500 ms |

The velocity cap is applied to the actual measured baseline, not to the
preview's `from_rad`: `execute` revalidates and re-runs the FK gate against the
current state.

## Presets

`configs/takeover.yaml` carries five poses. Four ran on the hardware; the
fifth is a limit-bound variant of the calibrated view.

| id | joints (rad) | TCP (nominal FK) | table clearance | evidence |
|---|---|---|---|---|
| `init_upright` | 0, 0, 0, 0, 0 | (0.079, 0.000, 0.418) | 496 mm | 2026-11-19 survey posture |
| `approach_low` | 0, −0.6618, −1.80117, 0, 0 | (0.280, 0.000, −0.015) | 65 mm | `FAST_MOTION.md` approach pose |
| `grasp_low` | 0, −0.6618, −1.80117, −0.6704, 0 | (0.196, 0.000, −0.045) | 35 mm | standing low-grasp procedure |
| `lift_150` | 0.050, 0.205, −1.420, −1.260, 0 | (0.228, 0.008, 0.109) | 188 mm | pass-2 lift (+109.3 mm) |
| `view_table` | 0, 0.2574, −1.18773, −1.5917, −0.01676 | (0.206, 0.000, 0.128) | 207 mm | **not** the calibrated view: see below |

`view_table` is a **near view**. The calibrated table-down view commands
`joint4 = −1.70`, which stalls on the low stop at register 94 (about −1.70 rad
by the vendor map, ~−1.62 in the calibration envelope) — i.e. 26 pulses below
the adopted bound 120. The preset clamps `joint4` to −1.5917, 7 pulses (1.7°)
above the measured view, so it is useful for looking at the table but is not
the hand–eye calibration pose.

The live no-motion run left the arm at the **calibrated view** (measured
joint1…joint5 = 500/561/783/94/495). A preview from there to `grasp_low` was
valid with a 34.8 mm table clearance over 21 samples.

## API

| Method | Path | Behaviour |
|---|---|---|
| `GET` | `/api/motion/presets` | contract + presets + current measured pose/TCP; `available=false` outside pose scope |
| `POST` | `/api/motion/preview` | same endpoint as G6; five targets = pose, one `r_joint` = gripper; adds `pose` (TCP, clearance, samples, violations) and a `START_ECHO` warning when the start is the command cache |
| `POST` | `/api/motion/execute` | revalidates and re-runs the FK gate against the current command-cache start, then dispatches once; the gateway bus-verifies the actual registers before `Completed` |
| `POST` | `/api/motion/stop` | hold at the last measured pulses; **not** a brake or e-stop |

New error codes: `POSE_JOINT_SET`, `VELOCITY_EXCEEDED`, `GRIPPER_RANGE`,
`WORKSPACE`, `TABLE_CLEARANCE`, `LOW_POSE_YAW`, `FK_UNAVAILABLE`.
Gateway error: `MOTION_VELOCITY_EXCEEDED`.

`/api/state` labels the continuous joint stream `truth=commanded` (the vendor
cache); a fresh bus read is `truth=measured_on_demand` for ~500 ms after a
move. Every `Completed` event carries `actual` (the bus read), `deviations_pulses`
and `verification="bus"` (or `"timed"` for the gripper).

## Start

Two processes, both explicit:

```bash
# 1. generate the contract on the laptop
cd armpi-debug-studio
backend/.venv/bin/python tools/export_gateway_motion_config.py \
  --mode pose --out /tmp/studio_takeover_limits.json --overwrite
# copy the package + JSON to a separate deploy dir on the robot, then start a
# second gateway on port 8766 with
#   --enable-motion --motion-config .../takeover_limits.json
# See ../ros2_gateway/deploy/takeover.md for the exact commands.

# 2. backend (laptop, repo root)
export ARMPI_TAKEOVER_ACK=I_AM_AT_THE_ROBOT_WITH_A_CLEAR_WORKSPACE
tools/studio/start_studio.sh --takeover
```

`--takeover` selects the 8766 gateway and `ARMPI_MOTION_SCOPE=pose`; it refuses
to start without the acknowledgement phrase.

## Verified on hardware (2026-11-21)

Raw reports: [`evidence/studio_g7/20261121/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_g7/20261121/README.md).
The first run used the original cache-based completion; the **command-cache
discovery** below led to the bus-verified path, which was then re-run.

| Step | Command duration | Actual bus registers | Δ vs target (pulse) | Dispatch → Completed |
|---|---:|---|---:|---:|
| `view_table` | 1.5 s | 502 / 556 / 792 / 122 / 497 | +2 / −5 / +8 / +2 / +1 | 2.10 s |
| `grasp_low` | 2.5 s | **502 / 339 / 928 / 336 / 499** | +2 / −3 / −2 / −4 / −1 | 3.06 s |
| `view_table` return | 2.5 s | 502 / 555 / 792 / 122 / 497 | +2 / −6 / +8 / +2 / +1 | 3.14 s |

The arm reached the low grasp pose within **4 pulses (0.017 rad)**. A separate
gripper re-run with the 1.2 s timed settle recorded **184** (open, command 170)
and **645** (close, command 700); the first verified sequence read at +0.5 s and
saw the transient (183 / 700), which is why the timed settle is 1.2 s. No
completion was `Unknown`.

**What the first run exposed.** The arm moved correctly, but every `Completed`
appeared ~0.12 s after dispatch regardless of the 1.5/2.5 s interpolation. The
vendor `/controller_manager/servo_states` topic is only the cache written by
`ServoManager.set_position`; the real registers come from
`GetBusServoState`, and they differed by up to 8 pulses (gripper: cache 700 vs
settled 645). G6's cache-based completion and G5's "measured feedback" label
are affected; see the evidence README.

## First real pose motion (operator checklist)

1. Support the arm or clear the workspace; the arm falls on power loss and has
   no e-stop or brake.
2. Start the pose gateway on 8766 **with the vendor zsh environment**
   (`source ~/.zshrc`; CycloneDDS) so `GetBusServoState` is reachable. Confirm
   `/health` reports `motion_mode=pose`, `measured_ready=true`, and the log
   says `verification=True bus=/ros_robot_controller/bus_servo/get_state`;
   `/motion/status` has `velocity<=0.5`, the gripper block and
   `verification=true`.
3. Start the backend with `tools/studio/start_studio.sh --takeover`; confirm
   `/api/diagnostics` is ready, the `pose_takeover` check is visible, and
   `/api/state` labels the continuous position `commanded`.
4. Acquire the lease. Load a preset and read the **whole** preview table plus
   the `Target TCP` / `Table clearance` / `Path samples` rows. The preview warns
   `START_ECHO`: the start is the command cache, up to ~8 pulses from actual.
5. Execute the **smallest** meaningful move first (e.g. `view_table` →
   `grasp_low` is already 2.1 s at 0.5 rad/s; consider `init_upright` →
   `approach_low` only after a dry preview). Keep the gateway console visible.
6. Wait for `Dispatched → Observed → Completed`; `Observed` now appears after
   the interpolation + settle because it is a **bus read**, not a cache sample.
   Any `Unknown` latches further motion until the backend is restarted after
   inspection.
7. Stop is a hold request; use power/support if anything is wrong.

## Limitations

- **No self-collision model.** The gate is TCP-only and uses the nominal URDF;
  the measured stops are pose-dependent. A passing preview is not a collision
  proof.
- **Nominal FK error.** The low-configuration model is ~8 mm short in x and
  8.9 mm low in z; the table clearance is a nominal-FK number.
- **Table plane scope.** The plane was measured at one base pose. Any mecanum
  base move invalidates it.
- **Low-pose yaw is measured evidence, not geometry.** Below TCP z 0.09 m the
  gate allows only ±0.20 rad of joint1; the operator-measured safe yaw height
  is the 18 cm pose.
- **Gripper force is not measured.** Completion is timed (5 s) plus one settled
  `GetBusServoState` read; the register shows motion/stall, not grip force.
  The observed settled close is ~645; the end-of-interpolation cache can read
  700, which is why the read waits 1.2 s.
- **No e-stop, brake or independent encoder.** Stop is a hold request.
- An `Unknown` outcome latches until a backend restart after inspection.
- **Position truth is split.** The continuous stream is the vendor command
  cache (`truth=commanded`); the actual registers are a one-shot bus read at
  completion. The **start** of a move is therefore the cache (up to ~8 pulses
  / 0.03 rad from actual), and the preview warns `START_ECHO`. G5's
  `feedback` uses the same cache; its "tracking" metrics are plan-vs-command,
  not servo tracking.
- **Bus read guard.** A joint whose last pulse is below 25 counts is not read
  (a negative register crashes the vendor node); an arm command that cannot be
  read becomes `Unknown` rather than a false `Completed`.
- The gateway motion JSON must be regenerated from `takeover.yaml` whenever
  the mapping or the contract changes; it now carries `verification=true`.
- **The pose gateway is camera-free** in the verified deployment (`--no-aux-camera
  --camera-topic /armpi_pose_camera_off`); the 8765 read-only instance keeps
  serving both cameras. Since 2026-11-21 the Studio supports a separate
  **camera gateway**: `ARMPI_CAMERA_GATEWAY_URL` routes `/camera/*` and
  `/aux-camera/*` to 8765 while motion stays on 8766. `tools/studio/start_studio.sh
  --takeover` sets it automatically (`CAMERA_GATEWAY_PORT=8765`), so a
  takeover-mode Dashboard shows the main and aux camera feeds without making
  the motion gateway decode frames. Omitting the camera-off flag on 8766 is
  still possible but no longer required and costs one more JPEG encoder on
  the Pi.
