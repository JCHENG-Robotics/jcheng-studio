# G8 — Task takeover

Status: **hardware-verified on 2026-11-21** — the 13-step single-point plan
(first hardware run) and the **fast 7-phase path** (dense S-curves, base
approach inside the run; 13.46 s wall, landing 2.27 mm planar). Evidence:
[`evidence/studio_g8/20261121/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_g8/20261121/README.md)
and [`evidence/studio_g8_fast/20261121/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_g8_fast/20261121/README.md).

G8 is the first Studio scope that acts on **what the camera sees** instead of a
pose the operator typed. It is deliberately narrow: one cube on the floor in
front of the robot, the validated deep grasp family, and a put-back to the
grasp pose. It is not a planner. It also drives the chassis toward a cube that
is visible but out of arm reach (G8.1), now **inside the same run**.

## Flow

```text
view_table preset (operator prepares the view)
   -> POST /api/task/plan {"form": "cube_39" | one catalog form}
        cloud capture (gateway subprocess, ~1 s)
        hand-eye T_link4_camera (TSAI, calibrated table-down)
        segment floor cube (RANSAC floor, components, top slab)
        form correction or the cube's (+13.7, +3.9, +8.9) mm
        deep-family fit (j2=-0.6618, j3=-1.80117, j5=0, j1/j4 solved on URDF)
          or a measured catalog j4 held exactly, j1 solved on azimuth
        reach band 0.220-0.255 m, FK xy <= 3 mm (or the catalog j4 residual)
        build 7 phases, each command validated: limits, velocity cap, FK gate
   -> one "run task":
        if APPROACH_REQUIRED: chassis pulse -> settle -> re-plan (max 3)
        for each phase: for each command:
            POST /api/motion/preview -> /api/motion/execute
            pose command = one dense S-curve (profile)
            grip command = one timed raw bus-servo command
        gateway bus-verifies each pose (12 pulses) and timed gripper step
   -> POST /api/task/verify (plan_id) after the lift
```

## The 7 phases (fast path)

The old plan emitted 13 single-command steps. The fast plan groups them and
drops two redundant moves:

| # | Phase | Commands | Why |
|---|---|---|---|
| — | `open_gripper` | one grip, 0.8 s | **conditional**: only when the gripper is not already open (skipped after a release) |
| 1 | `approach` | pose j4 -> 0, 4.0 rad/s | |
| 2 | `grasp` | descend to pre-grasp 0.6 rad/s, final 0.15 rad 0.35 rad/s | two profiles, one operator row |
| 3 | `close` | preclose raw 318 (0.35 s), grip raw 550 (0.5 s) | contact/stall evidence kept |
| 4 | `lift` | grasp pose -> `view_table` at 4.0 rad/s | the separate `lift_150` move is gone; the measured 2026-11-20 fast sequence returns straight to the verify pose |
| 5 | `grip_verify` | marker | cloud at the view, cube box must be empty |
| 6 | `release` | descend 1.4 rad/s, final 0.8 rad/s, open 0.45 s | |
| 7 | `return_view` | grasp pose -> `view_table` 4.0 rad/s | |

With the gripper already open this is **7 rows** and ~5.4 s of commanded
motion for the mock geometry (the old single-point plan was ~23.5 s), plus the
gateway's per-command verification settles. The measured fast-motion envelope
is 4.0 rad/s / 20 rad/s² with 8.94 s grasp + put-back motion
([`docs/phase2/FAST_MOTION.md`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/docs/phase2/FAST_MOTION.md)).

## Fast profile (why and how)

The vendor controller forwards **one bus-servo command per trajectory point**
with that segment's duration, so a single long point is one constant-velocity
move with a hard start/stop (slow *and* shaky). `backend/app/domain/profile.py`
is the pure port of `tools/phase2/motion_profile.py`: a scalar trapezoid with
half-cosine ramps, all joints synchronised, endpoints exact, 50 ms waypoints.

- The **plan** stores the endpoint, the profile parameters and the estimated
  total; each pose command is one `profile` object.
- **Preview** validates the endpoint (limits, FK gate along the straight
  joint-space line) and that the profile total/segments fit the contract.
- **Execute** rebuilds the S-curve from the current pose, converts every
  waypoint to pulses and sends it as one `/api/motion/dispatch` with
  `profile.points`.
- The **gateway** validates every segment again — pulse limits, per-segment
  velocity, discrete acceleration, the start from the measured command cache,
  and that the last point equals the requested endpoint — then publishes one
  `ServosPosition` per segment at the segment cadence (the same path the
  vendor action controller uses). Completion is still one `GetBusServoState`
  read of the endpoint after the total duration + settle.

Contract (`configs/takeover.yaml`, exported into the gateway
`safety.profile`): `vmax 4.0 rad/s`, `amax 20 rad/s²`, `dt 0.05 s`,
`max_total 6 s`, `verify_settle_s 0.35`, gripper `0.35/0.5/0.45/0.8 s`,
`gripper_settle_s 0.6`. The slow single-point pose path keeps its 0.5 rad/s
cap, 1.0 s floor and 100 raw/s gripper validation; the fast values apply only
to a task profile or a `fast` gripper command.

## Hardware result (2026-11-21)

The first fast run started from an `APPROACH_REQUIRED` plan (target radius
0.257 m); the run's own base pulse (13 mm nominal) brought the cube into the
band and the re-plan was valid. `approach`, `descend` and `grasp` completed,
then `close_pre` was **rejected before publication** because the backend
adapter did not forward `fast: true` to the gateway and the slow 1.0 s floor
applied. The arm stayed at the grasp pose; it was returned to the view with the
ordinary 0.5 rad/s path.

After the fix the second run passed: 7 phases, 5.466 s commanded / **13.46 s
wall** (the old 13-step plan was 33.56 s), `close_pre` 309 (contact),
`close_grip` 414, `held: true`, `release_open` 174, and a landing of
**+2.22 mm x / -0.46 mm y / +1.06 mm z** (2.27 mm planar) against the pre-grasp
point. Details:
[`evidence/studio_g8_fast/20261121/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_g8_fast/20261121/README.md).

## Locate and fit

- The registered cloud `/depth_cam/depth_registered/points` is captured by an
  on-demand subprocess (`cloud_capture.py`) so the multi-megabyte
  deserialization never shares the gateway's motion process. Layout: OpenCV
  optical XYZ in the driver's `depth_camera_link` frame.
- The segmenter is a pure-NumPy port of `tools/phase2/object_segment_rgbd.py`:
  RANSAC floor, plane refinement, 5 mm voxel components, region/height/size
  gates, top slab with a 35 mm minimum extent.
- **Min-range clip recovery** (2026-11-21): the Aurora reports no valid depth
  nearer than ~0.25 m at the view pose, so a cube inside that sphere loses the
  near part of its top face; the clipped centroid is biased toward the far
  edge by 8-13 mm. When exactly one side of the top's minimum-area rectangle
  lies on the min-range sphere (range spread <= 1.5 mm, mean within 2.5 mm of
  the cloud minimum) and the opposite edge and both side edges are intact, the
  centre is rebuilt as `far_edge - cube_size/2` along the clipped axis and the
  mid-point along the intact axis. Enabled by `task.locate.cube_size_m`
  (0.0394 m, the measured cube); the plan warns `TOP_CLIPPED_RECOVERED`.
  A top clipped on two sides is still rejected.
- The pose basis for the FK is a **fresh bus actual** from the gateway when it
  is recent enough and within 0.12 rad of the view; otherwise the command
  cache is used and the plan warns `START_ECHO` (up to ~8 pulses, ~8 mm at the
  TCP). The 2026-11-21 run shows the difference: the same cloud moved the
  measured point ~8 mm between a cache and a fresh-actual basis.
- The reach band is **direct grasp only** (0.220-0.255 m target radius). A
  farther cube is driven toward by the chassis approach (G8.1).

## G8.1 chassis approach — inside the run

The view covers about 0.15-0.55 m of floor, while the validated tool-down
grasp pose reaches a 0.220-0.255 m target radius. A cube outside that band
returns `APPROACH_REQUIRED` plus a pulse vector computed from the measured
point and the aim point `(0.230, 0.0)` (the middle of the band, on the
tool-down axis, so the constant correction's `joint1 ~ 0` envelope still
holds). The old separate "底盘接近" button is gone: **the run itself** pulses
`POST /api/chassis/pulse` (duty <= 10, 0.2-2.0 s, ~4.9 mm/s per duty), waits
for the coast, re-plans from a fresh cloud and repeats (at most 3 pulses),
then continues with the arm phases. The UI and `tools/studio/g8_task_run.py`
both implement the same loop.

The base has **no odometry** (four duty motors, write-only): the pulse response
is labelled `verification: open_loop` and the camera re-measurement is the only
confirmation. The gateway caps every pulse; the run caps the loop; the arm
stays at the view during the pulse. The approach is a translation (the mecanum
strafe handles a lateral offset); a base yaw is not implemented, so a cube
outside the lateral window is rejected rather than driven to. The locate
window is **±150 mm in y** (2026-11-21): a valid 39 mm cube at
`(0.426, -0.100, -0.057) m` was previously rejected as `y_out_of_region`
because the old window was ±90 mm; it now returns `APPROACH_REQUIRED` with a
lateral pulse. The top-view gate is **30 mm** (was 35 mm): a rotated cube at
`(0.434, -0.105, -0.042) m` measured a 34.6 mm top extent from depth noise and
was falsely rejected as `partial_top_view`. The locate failure message names
the rejected cluster's gates, so a genuinely bad placement is actionable
instead of just `no usable cube point`.

Historical live evidence (2026-11-21, 13-step plan): cube at `x = 0.357 m` ->
two pulses (2.0 s / 98 mm nominal -> 94 mm actual; 0.68 s / 33 mm -> ~42 mm) ->
recovered point `(0.2214, 0.0024)` -> plan valid -> `close_grip` raw 412,
`held: true`, put-back landing +0.5 mm x / -0.7 mm y / +0.2 mm z. See
[`evidence/studio_g81/20261121/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_g81/20261121/README.md).

Two live defects were fixed in the same run: the adapter's 8 s POST timeout was
below the gateway's pulse handler time (now 20 s for `/chassis/pulse`), and a
top face clipped by the min-range sphere could flag *two* edges as clipped
(the recovery now also requires the cut boundary to be perpendicular to the
camera direction, `recovery_radial_min: 0.85`).

## Object forms — the 2026-11-23 catalog (2026-11-24)

The G8 task plan is no longer cube-only. `task.forms` in
`configs/takeover.yaml` carries the six object forms measured on the real arm
on 2026-11-23 ([`docs/phase2/OBJECT_GRASP_CATALOG.md`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/docs/phase2/OBJECT_GRASP_CATALOG.md),
evidence in [`evidence/onsite_20261123/object_forms/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/onsite_20261123/object_forms/)).
The task-takeover page shows them as selectable cards; `POST /api/task/plan`
accepts `{"form": "<id>"}`; `tools/studio/g8_task_run.py` takes `--form`.
Without a selection the contract's `default_form` is `cube_39`, so the
historical cube command sequence is unchanged.

| Form id | Mode | Grasp parameters |
|---|---|---|
| `cube_39` | `task` | j4 from the deep-family fit; close −0.63; the original G8 object |
| `block_lying` | `task` | j4 **−0.50** measured; close −1.6 → **−0.05**; top 30 mm at the pads' limit |
| `block_upright` | `task` | j4 **−0.39** measured; close −1.6 → −0.05; top 61 mm |
| `cylinder_standing` | `task` | j4 **−0.365** measured; aim **+27 mm** (pads sit ~13 mm behind the TCP); close −1.6 → −0.05 |
| `cylinder_lying` | `refused` | widest ring 14.7 mm is below the pads' 28–30 mm reach; `FORM_REFUSED` |
| `carton_vlt` | `pose` | no depth return in any orientation; `FORM_POSE_ONLY` returns the recorded j1 0.066 / j2 −0.62 / j3 −1.77 / j4 −0.45 / j5 0 pose for pose takeover |
| `sachet` | `refused` | squeezes out of the rigid pads (478 → 642); `FORM_REFUSED` |

**How a form changes the plan.** A measured `grasp_j4` is held exactly, not
re-fitted: re-solving j4 for the TCP xy is what left the pads above the lying
block on 2026-11-23. `joint1` is solved on the target azimuth, and the TCP xy
residual is reported as a warning (`FORM_J4_MEASURED`) rather than rejected —
the contact point is the pads, which sit in front of the nominal TCP. A
fixed-j4 form carries the **measured object position** it was validated at
(`base_point_m`, window ±12 mm): the lying block 0.233 m, the upright block
0.226 m and the standing cylinder 0.214 m. An object outside that window makes
the G8.1 loop pulse the base toward the form's base point instead of the cube's
`(0.230, 0.0)`, and the plan warns `FORM_BASE_POSE`; the `0.220–0.255 m`
direct-grasp band is then replaced by that measured base window (the catalog's
cylinder sits at x ≈ 0.214 m, below the cube band). The `+27 mm` TCP aim
(`correction_mm`) only affects joint1/display, not the fixed j4. The approach,
pre-grasp (`j4 ± 0.15`), lift, release and return phases are otherwise
unchanged; every pose still goes through plan-time validation,
preview/execute and the gateway's bus verification. A deep close (−0.05 rad,
raw ~688) stretches the release-open duration beyond 0.45 s so the commanded
interpolation stays under the 900 raw/s fast-path sanity ceiling.
`close_register` is documentation (482/489/433 are real holds, 640–648 an
empty close), not a completion signal.

**Not yet hardware-run as an integrated task path.** The six form poses were
measured through the pose API on 2026-11-23; this task-plan integration was
validated by unit/mock tests and a frontend build on 2026-11-24. Treat the
first hardware run of a form as a commissioning run, clear the workspace, and
expect `LOCATE_FAILED` on the upright block/standing cylinder when the
min-range sphere leaves too little top face (use pose takeover with the
recorded pose).

## Safety

- Every command is validated twice: at plan time from the planned start, and
  at execute time by the ordinary preview from the **actual** state. A
  rejected preview aborts the run; nothing is retried.
- The FK gate covers the straight joint-space interpolation (21 samples) with
  the workspace box, the measured tilted table plane + 10 mm, and the low-pose
  yaw rule. The S-curve is on the same straight line, so the gate applies.
- The gateway re-checks a profile independently: every segment's pulses,
  velocity, discrete acceleration, the measured start and the endpoint
  equality. A profile cannot hide a fast segment after a slow one.
- The grip verify segments the view after the lift and requires that no cube
  box remains at the expected point; a miss aborts before the put-back.
- The run needs a lease, the fixed acknowledgement, pose scope and the
  verified mapping; the operator is at the robot with a clear workspace.
- Gripper steps are timed (the fingers stall on contact). The slow pose path
  keeps `speed_raw_per_s: 100` and a 1.0 s floor. The fast task path uses the
  measured 0.35/0.5/0.45 s durations and a **sanity ceiling** of 900 raw/s on
  the commanded interpolation rate; the servo physically saturates near
  420-460 raw/s, so this does not claim the fingers move at 900 raw/s.
- A stop during a profile is checked at every segment boundary. The hold
  command wins, but a segment already published can still finish (<= 50 ms),
  so the arm stops within one waypoint of the hold.

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/task/plan` | optional `{form}`; locate + fit + 7 phases (or `APPROACH_REQUIRED` + `approach`); `valid`, `form_id`, `point`, `target`, `grasp_j4`, `preclose_rad`, `grip_rad`, `fit`, `fast`, `warnings`, `plan_id`; unknown form → 422 `UNKNOWN_TASK_FORM`, a refused/pose form → invalid with `FORM_REFUSED`/`FORM_POSE_ONLY` |
| POST | `/api/task/verify` | `{plan_id}`; object box at the view after the lift |
| POST | `/api/chassis/pulse` | one bounded base pulse; lease + arm at the view |
| GET | `/api/motion/presets` | `view_table`, `lift_150`, ... plus `task.fast` and `task.approach` |
| POST | `/api/motion/preview` | `targets` plus optional `profile {vmax_rad_s, amax_rad_s2, dt_s}` / `fast` |
| POST | `/api/motion/execute` | same body + lease/token; a profile dispatches the dense waypoints |

`tools/studio/g8_task_run.py` is the operator runner: plan-only by default;
`--execute --i-have-clear-workspace` runs the base approach and every phase,
requires bus-verified `Completed`, and saves a report. `--form` selects a
catalog form; without it the contract default (the 39.4 mm cube) is used.

## Limitations

- One object, one position, one put-back target (the grasp pose); the landing
  scatter measured after the 13-step run is +3.6 mm x, +0.2 mm y, +0.4 mm z,
  and after the fast run +2.22 mm x, -0.46 mm y, +1.06 mm z (2.27 mm planar).
- The object-form task path is **not hardware-run as an integrated flow** yet:
  the pose values and close registers were measured on 2026-11-23, but the
  form selector, its arm phases and the stretched deep-close release were
  validated in Mock/tests only. The catalog j4 is tied to the base approach's
  aimed position (0.230, 0.0); a target farther from it is the first reason to
  expect a miss.
- The fast path ran on the hardware on 2026-11-21: 7 phases, 5.466 s commanded
  / 13.46 s wall, `close_pre` 309, `close_grip` 414, `held: true`. The first
  attempt exposed a real regression (the adapter did not forward `fast: true`,
  so the gateway applied the 1.0 s slow floor and rejected `close_pre` before
  publication); the fix and both attempts are in the evidence README.
- The chassis approach is open-loop and translation-only; the cube must be
  visible in the cloud region (0.15-0.55 m, |y| <= 0.09) and inside the lateral
  pulse window. No base yaw.
- No collision model: the FK gate is TCP-only and nominal.
- The clip recovery is one-sided and uses the measured cube size; a two-sided
  clip, a different object or a different view pose needs new evidence.
- The measured point's z follows the pose basis; use the fresh bus actual when
  the last command was not bus-read.
