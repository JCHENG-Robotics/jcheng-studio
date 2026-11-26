# G6 — Supervised motion write path

Status: **implemented and verified without moving the arm (2026-11-18)**.
The default gateway and backend remain read-only. The write path exists only
behind an explicit, fail-closed operator acknowledgement.

> G7 pose takeover (2026-11-21) supersedes the G6 *scope* for real work: one
> whole five-joint pose or one gripper command, a 0.5 rad/s velocity cap and an
> FK workspace/table gate. G6 remains the default `step` scope and the
> commissioning path; see [`g7-pose-takeover.md`](g7-pose-takeover.md).

## Why this milestone

G1–G5 built a read-only console plus offline analysis. The one PRD P0 gap was
safe motion from the Studio itself: every earlier real motion came from the
external MoveIt tool. G6 adds a single-joint, supervised path that the Studio
can validate end-to-end, while keeping the then-unverified mapping state
visible and keeping the physical e-stop gap explicit.

## Defense in depth

A command must pass five independent layers. No layer trusts the previous one.

| Layer | Enforces | Where |
|---|---|---|
| 1. Backend startup | `ARMPI_HARDWARE_MOTION=1` requires a gateway URL, a gateway token, and — while limits are unverified — the exact `ARMPI_SUPERVISED_MOTION_ACK=I_HAVE_SUPPORT_AND_A_CLEAR_WORKSPACE` phrase. Missing values fail startup. | `backend/app/config.py` |
| 2. Backend validation | one joint per command in supervised mode; pulse-aligned step cap (23 pulses = **0.096342 rad**), duration ≥ 1.0 s, adopted EEPROM limits, measured position as the step baseline. | `backend/app/safety/validation.py`, `backend/app/domain/motion.py` |
| 3. Backend safety gate | lease, feedback freshness, no active fault, interlock, gateway motion capability, and **measured** feedback for every joint. `mapping_verified` is non-blocking only with the acknowledgement. | `backend/app/safety/supervisor.py` |
| 4. Gateway validation | independent JSON limit file; fresh measured pulses (≤500 ms); step ≤23 pulses; single joint; duration range; rad↔pulse agreement; one active command; busy rejection. | `ros2_gateway/armpi_debug_gateway/motion.py` |
| 5. Vendor driver | publishes one `ServosPosition` (`position_unit=pulse`, `duration`) to `servo_controller`; the vendor controller clamps 0..1000 and updates its cached positions. No SDK call, no serial access. | `ros2_gateway/armpi_debug_gateway/node.py` |

The gateway is a separate process from the backend. A backend bug cannot make
the gateway publish an out-of-limit pulse, a step above 23 pulses, a
stale-feedback command or a second command while one is executing.

## Effective supervised limits

| Item | Value |
|---|---|
| Joints per command | exactly 1 |
| Max step | `floor(0.10 / radians_per_tick) * radians_per_tick` = 23 pulses = 0.096342 rad (≈5.5°) |
| Duration | 1.0 s … 10.0 s |
| Position limits | `configs/joint_mapping.yaml` (EEPROM-bounded; joint2 low is the measured mechanical stop) |
| Feedback age | ≤ 500 ms |
| Completion | measured pulse within ±3 pulses for 3 consecutive samples |
| Completion deadline | `duration + 2.0 s` |

The backend cap is aligned down to a whole pulse so a command accepted by
preview cannot need one extra pulse at the gateway. Preview and execute use the
same effective limits.

## API contract

The gateway adds three endpoints. They are disabled unless the gateway was
started with `--enable-motion` and a validated `--motion-config` **and** a token.

| Method | Path | Behaviour |
|---|---|---|
| `GET` | `/motion/status` | always available; `state=disabled` on a read-only instance |
| `POST` | `/motion/dispatch` | validate and publish one `ServosPosition`; `403 MOTION_DISABLED`, `409 MOTION_BUSY`/`MOTION_STALE_FEEDBACK`/`MOTION_STEP_EXCEEDED`, `400 MOTION_OUT_OF_LIMIT`/`MOTION_BAD_REQUEST` |
| `POST` | `/motion/stop` | publish a hold at the last measured pulses (0.1 s); reported as `StopDispatched`, **not** a brake or e-stop |

Dispatch body:

```json
{
  "command_id": "cmd_...",
  "client": "armpi-debug-studio-backend",
  "supervised": true,
  "duration_s": 1.5,
  "joints": [
    {
      "joint": "joint1",
      "servo_id": 6,
      "target_pulse": 513,
      "target_rad": 0.05418879
    }
  ]
}
```

The backend still uses the existing `lease → preview → execute` flow. The
preview token is bound to the requested targets, not to a live `from_rad`.
`execute` revalidates against the current measured position, then calls the
gateway. A 4xx gateway rejection is `Failed`; a network/timeout result is
`Unknown` and never retried.

### Dense profile (G8 fast task path)

When the pose contract exports `safety.profile`, the same endpoint also accepts
a dense S-curve:

```json
{
  "command_id": "cmd_...",
  "duration_s": 0.71,
  "joints": [ /* the five endpoint targets */ ],
  "fast": true,
  "profile": {
    "points": [
      {"duration_s": 0.05, "joints": [ /* five pulse targets */ ]},
      "..."
    ]
  }
}
```

The guard checks every segment (pulse limits, per-segment velocity, discrete
acceleration, the first segment against the measured command cache, and that
the last point equals `joints`) and then publishes one `ServosPosition` per
segment at the segment cadence. A `fast: true` gripper-only command uses the
profile block's `gripper_min_duration_s`/`gripper_speed_raw_per_s` and the
`gripper_settle_s` post-command wait. Without the block these requests are
`MOTION_PROFILE_DISABLED`/`MOTION_FAST_DISABLED`; the slow single-command path
is unchanged.

## Completion and unknown outcomes

> **Corrected 2026-11-21.** The original text below said `/controller_manager/servo_states`
> is measured feedback. It is **not**: the vendor `ServoManager.set_position`
> writes the commanded pulse into the cache that both `/joint_states` and
> `/servo_states` publish. A cache-based completion appears ~0.1 s after
> dispatch regardless of the interpolation. The gateway now bus-reads the
> actual registers with `GetBusServoState` after `duration + 0.5 s` and accepts
> a step command only within ±8 pulses; the backend rejects a `completed`
> status without `verification="bus"` as `Unknown`. The step gateway must be
> started with the vendor zsh environment (CycloneDDS) so the service is
> reachable. See [`g7-pose-takeover.md`](g7-pose-takeover.md) and
> [`evidence/studio_g7/20261121/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_g7/20261121/README.md).

Completion is derived from a **`GetBusServoState` bus read** after the
interpolation, not from `/controller_manager/servo_states` (the vendor command
cache). On completion the adapter emits `Observed` and `Completed` audit events
with the actual pulses. A `completed` status without a bus verification is
reported as `Unknown`, never `Completed`.

If the gateway cannot confirm the outcome (status unreachable, `unknown`
state, a failed bus read, or deadline exceeded), the adapter emits `Unknown`,
sets a **latched** `MOTION_OUTCOME_UNKNOWN` fault and refuses further motion
until the backend is restarted after inspection. A normal stop does not clear
the latch.

## Verified on hardware (2026-11-18, no motion)

Raw files: [`evidence/studio_g6/20261118/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_g6/20261118/README.md).

```text
read-only gateway 8765 redeploy
  /health         status=ok, motion_enabled=false, measured_ready=true
  /state          measured block present; truth=commanded (echo) + measured
  /motion/status  state=disabled
  POST dispatch   403 MOTION_DISABLED

supervised gateway instance 8766 (read-only service untouched)
  /health         status=ok, motion_enabled=true, motion_state=idle, measured_ready=true
  /motion/status  state=idle, step<=0.1 rad, duration>=1.0 s, single_joint=true
  out-of-limit    400 MOTION_OUT_OF_LIMIT (pulse 950 outside [70,930])
  step probe      409 MOTION_STEP_EXCEEDED (429 > 23 pulses)
  shutdown        clean SIGTERM; only 8765 remained

supervised backend (motion enabled, acknowledgement present)
  /api/health     hardware_motion=true, supervised_motion=true, max_step=0.096342, min_duration=1.0
  /api/state      5/5 joints truth=measured, source=/controller_manager/servo_states, age 15 ms
  /api/diagnostics safety.ready=true, blockers=[], 14 checks visible
  preview joint1  +0.05 rad / 1.5 s -> valid, token issued
  preview 2 joints                -> SUPERVISED_SINGLE_JOINT
  preview +0.15 rad               -> STEP_EXCEEDED
  measured before vs after        max delta 0.000000 rad (no command dispatched)
```

No valid dispatch was sent. The arm did not move during this verification.

## First real supervised motion (operator checklist)

1. Support the arm or clear the workspace; the arm falls on power loss and has
   no e-stop or brake.
2. Start the supervised gateway instance (see
   [`ros2_gateway/deploy/supervised-motion.md`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/armpi-debug-studio/ros2_gateway/deploy/supervised-motion.md)).
3. Generate the motion limits JSON from the current mapping; do not hand-edit
   pulse bounds.
4. Start the backend with `tools/start-supervised-backend.sh`; confirm
   `/api/diagnostics` has `safety.ready=true`, no blockers, and
   `mapping_verified` is non-blocking with the acknowledgement.
5. Preview one joint with a step ≤ 0.05 rad and duration ≥ 1.5 s. Read the
   `from_rad` / `target_raw` / limit rows before executing.
6. Execute once, watch the arm, and keep the gateway console visible. Stop is a
   hold command, not an emergency stop; use power/support if anything is wrong.
7. After the run, check `/api/events` for `Dispatched` → `Observed` →
   `Completed` (or `Unknown`) and the measured evidence.

## Limitations

- `mapping_verified` became **true** on 2026-11-19 after the formal stop
  survey (all five arm joints); the supervised path remains a restricted
  commissioning mode. The mechanical stops are self-collision-limited and
  pose-dependent, which `docs/phase2/JOINT_LIMITS.md` records.
- No physical e-stop, brake or independent encoder. Stop is a hold request.
- Single-joint only; no trajectory streaming from the Studio, no MoveIt action
  dispatch, no multi-joint coordinated commands.
- Completion depends on the vendor controller's `servo_states` reporting path.
- An `Unknown` outcome latches until a backend restart after inspection; there
  is no automatic retry and no automatic recovery.
- The gateway motion config is generated from `joint_mapping.yaml`; if that file
  changes, regenerate and redeploy the JSON before enabling motion.
