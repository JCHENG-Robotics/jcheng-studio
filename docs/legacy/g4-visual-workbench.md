# G4 Visual Workbench (read-only)

> Status: **implemented and mock/offline-validated (2026-11-18)**. The Workbench
> shows a URDF forward-kinematics model, camera metadata and calibration/TF
> warnings. It can use an optional read-only `/tf` snapshot from the gateway;
> without one it falls back to URDF FK. It does not move the arm or sit in the
> safety/control chain. Mock camera frames are synthetic.

## Scope

- Parse the audited project URDF/xacro (including the gripper include) and build
  its joint tree.
- Compute link frames from the current joint-state map with forward kinematics.
- Prefer a read-only TF edge snapshot from the gateway (`/tf` + `/tf_static`
  cached on the robot); fall back to URDF FK for frames not in the live graph.
- Show joint positions against URDF/mapping limits, marking provisional limits,
  missing state and out-of-limit values.
- Show the camera snapshot with its frame names, resolution, encoding, FPS, age
  and `truth`, plus an explicit warning that PnP must use
  `camera_optical_calibrated`, not the body frame or the nominal URDF frame.
- Show the validated hand-eye extrinsic and its version/source.
- Save metadata-only camera references for calibration/traceability; image
  bytes are never stored by the Studio.
- Report missing, cyclic or degraded model inputs instead of drawing fake
  frames.

## Inputs

| Source | Role |
|---|---|
| `ros2_ws/src/armpi_ultra_description/urdf/armpi_ultra.xacro` | URDF links/joints/limits; includes `gripper.urdf.xacro` |
| `configs/visual_model.yaml` | URDF audit status and the measured `T_link4_camera` hand-eye |
| `/api/state` | current joint positions and their truth (`simulated` / `commanded` / `measured`) |
| gateway `/tf` + `/tf_static` | optional live TF edges (parent/child, translation, quaternion, stamp, age, static flag) |
| `/api/peripherals` + snapshot | camera availability, resolution, encoding, FPS, frame age, image |
| mapping (`configs/joint_mapping.yaml`) | provisional software limits for arm joints |

xacro properties/macros are not evaluated. The project URDF keeps joint
origins, axes and limits as literal XML, so a plain XML parse is sufficient;
mesh paths with `$(find ...)` are ignored. The SHA-256 in the response covers
all parsed URDF files (main + include).

## Frame model and truth

- `base_link` is the root. Fixed and revolute joints are composed from URDF
  origins and axis-angle rotations; prismatic joints use axis translation.
- Mimic joints follow their source joint (`multiplier`/`offset`). If the source
  joint has no state, the mimic position is reported as missing rather than
  silently assumed to be zero.
- The measured camera frame is composed as
  `T_base_link4 @ T_link4_camera`, loaded from `configs/visual_model.yaml`.
  Its source is `measured_hand_eye`, not `urdf_fk`.
- Nominal URDF frames (`camera_link`, `camera_optical_frame`) are marked
  `nominal: true` and must not be used for PnP.
- No live tf2 is queried directly by the backend. When the gateway provides
  `/tf`, the response says `source="tf2_topics"`, `live=true` and
  `truth="measured_tf"`; otherwise it says `source="urdf_fk"`, `live=false`.
  Dynamic TF may be derived from commanded joint states; the UI warns about
  this and `joint_truth` remains a separate field.

TF status values:

| Status | Meaning |
|---|---|
| `live` | gateway `/tf` is fresh (newest dynamic edge ≤500 ms) and the graph composes |
| `live_static_only` | only `/tf_static` is available; dynamic frames fall back to URDF FK |
| `model_only` | no gateway TF; all frames are URDF FK and state is fresh |
| `degraded` | live TF is stale or partial, required arm state is missing, or model input is incomplete |
| `invalid` | URDF/camera model cannot be loaded; no frames are returned |

Missing, cyclic, duplicate-parent and unreachable links are reported as
warnings; a cyclic or parentless URDF returns `invalid` instead of crashing.

## Camera frames

| Frame | Role | Use |
|---|---|---|
| `rgb_camera_link` | live image/CameraInfo body frame | metadata only |
| `camera_optical_calibrated` | measured OpenCV optical frame (x right, y down, z forward) | PnP target poses |
| `camera_optical_frame` | nominal URDF/REP-103 model frame | **not** hardware |

The calibration block includes parent link, translation, RPY,
`calibration_version` and source. In mock mode the camera is
`truth=simulated`; in hardware mode the same fields come from the read-only
gateway. The snapshot overlay only labels metadata; it does not claim a
validated detection.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/visual` | URDF/FK frames, joint limits, camera + calibration + TF warnings |
| GET | `/api/visual/camera-references` | saved metadata references |
| POST | `/api/visual/camera-references` | save a reference (note ≤500 chars) |

A reference stores `created_at`, mode/source label, camera truth, topic,
resolution, encoding, FPS, frame timestamp, sample age, calibration version and
source, TF status/source and the note. It does **not** copy the JPEG/SVG. The
save path rejects an unavailable camera and appends a `CameraReferenceSaved`
audit event. There is no motion/lease endpoint under `/api/visual`.

## Safety isolation

- The endpoint is read-only: it never acquires a lease, publishes ROS, opens a
  serial port, or dispatches motion.
- The page renders from JSON only; a rendering failure cannot block the safety
  gate or alter a command.
- Camera references are metadata bookkeeping, not a calibration write and not a
  control input.
- The URDF meshes and inertial values remain model estimates. Joint limits are
  verified for the operating envelope since 2026-11-19 (`verified: true`);
  earlier G4 captures that show provisional limits are historical.

## Verification (2026-11-18)

```text
backend pytest        74 passed at G4 delivery (8 visual tests)
gateway pytest        12 passed at G4 delivery (includes the /tf endpoint)
Playwright mock E2E   10 passed (Visual Workbench spec)
frontend build        tsc -b && vite build OK
hardware Playwright   3 passed (2026-11-18, later G5 run)
live TF               11 dynamic + 8 static edges, status=live
base_link -> link5    gateway composition vs tf2_echo: < 0.4 mm
```

Checks include:

- parsing the real URDF and gripper include;
- `camera_forward` FK places `camera_connet_link` at x≈0.154 m, z≈0.282 m,
  matching the SRDF reference note;
- camera calibrated frame exists, parent `link4`, source `measured_hand_eye`;
- nominal camera frame is marked nominal;
- missing joints and cyclic URDFs are reported, not guessed;
- `/api/visual` returns `model_only`, `live=false`, 640×400 simulated camera
  metadata and the provisional-URDF warning;
- saving a camera reference works, is listed, and appends only
  `CameraReferenceSaved` (no `Requested`/`Dispatched`);
- a missing URDF returns `tf.status=invalid` with a warning instead of a 500;
- Playwright checks the model SVG, camera overlay/frame names, 11-joint table,
  save-reference flow and absence of motion audit stages.

Evidence: [`evidence/studio_g4/20261118/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_g4/20261118/README.md).

## Live robot verification (2026-11-18)

The optional gateway `/tf` path was redeployed and checked against the robot:

- `/tf` returned 11 dynamic edges and 8 static edges; the freshest dynamic edge
  was 8–50 ms old and the gateway reported `status=ok`.
- The backend composed `base_link → link5` at
  `[0.16416, 0.00031, 0.23982] m` with quaternion
  `[-0.00009, 0.70682, 0.00555, 0.70737]`.
- `tf2_echo base_link link5` in the vendor container reported
  `[0.164, 0.000, 0.240] m` and `[-0.000, 0.707, 0.006, 0.707]`; the
  differences are below 0.4 mm and 0.001 in quaternion components, consistent
  with different sampling instants and `tf2_echo`'s three-decimal display.
- `/api/visual` returned `tf.status=live`, `source=tf2_topics`,
  `truth=measured_tf`, `live=true`, and the camera body/optical frames
  `rgb_camera_link` / `camera_optical_calibrated`.
- The first hardware attempt exposed two contamination/staleness issues:
  orphaned laptop mock nodes published a second `base_link →
  camera_optical_calibrated` edge, and the reported maximum TF age included
  latched static edges. The stale nodes were stopped; `max_tf_age_ms` now uses
  the dynamic-edge maximum, while static ages are reported separately by the
  gateway.

Evidence: [`evidence/studio_g5/20261118/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_g5/20261118/README.md)
(`gateway_tf.json`, `tf_link5_composed.json`, `tf2_echo.txt`,
`hardware_visual.json`, `hardware_visual.png`).

## Limitations

1. Live TF was verified on the robot on 2026-11-18, but it remains optional:
   without gateway `/tf`, frames fall back to URDF FK and the current joint
   state. In hardware mode that state is the vendor command echo, and the
   vendor's dynamic TF may also derive from commanded states rather than
   independent encoders.
2. At the 2026-11-18 G4 build the URDF was provisional and `verified=false` was
   returned. Joint limits are now verified for the operating envelope
   (2026-11-19); meshes and inertial values remain model estimates.
3. The view is an SVG isometric projection of link frames/axes, not a mesh or
   depth render. It is intended for structural checks, not collision or
   calibration truth.
4. The mock camera is a generated SVG. A hardware-mode camera check now passes
   against the live gateway, but it still depends on the vendor `aurora` node
   and the `rgb_camera_link` body frame; PnP must use the calibrated optical
   frame.
5. Camera references store metadata only; they cannot reproduce the exact image
   later unless an external system saved it.
6. The Workbench does not show a live target-detection overlay. G2/G3 analysis
   and fiducial results are separate; no estimate is presented as ground truth.
7. URDF `r_joint` limits are shown from XML while the arm's mapping excludes the
   gripper; the measured `-0.8378…0.0` range is documented separately in
   `docs/hardware/measured_joint_limits.md`.
