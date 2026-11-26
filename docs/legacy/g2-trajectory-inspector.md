# G2 Trajectory Inspector (offline)

> Status: **implemented and mock/offline-validated (2026-11-18)**; a read-only
> G5 recorder also produced a no-motion capture and one supervised
> `camera_forward` moving capture, both imported successfully. This is not a
> PRD G2 hardware acceptance: the moving capture is a single small motion, and
> replay never touches a robot. All demo data is explicitly simulated.

## Scope

The Inspector closes the offline part of PRD G2:

- planned, commanded and feedback streams are stored separately and labelled;
- per-joint error and sampling-quality metrics are computed by the backend;
- a Replay clock plays recorded samples on the client only;
- CSV/JSON exports let another script recompute the metrics;
- the trajectory API has no motion write path.

It does **not** consume rosbag2. Live capture is provided separately by the
read-only G5 recorder
([`g5-trajectory-recorder.md`](g5-trajectory-recorder.md)), which writes this
dataset contract directly. A dataset can still be imported as JSON through the
UI or `POST /api/trajectories`.

## Dataset contract (`schema_version: 1`)

```json
{
  "schema_version": 1,
  "name": "short traceable name",
  "description": "optional free text",
  "timebase": "shared_trajectory_start",
  "joint_names": ["joint1", "joint2", "joint3", "joint4", "joint5"],
  "planned": {
    "truth": "planned",
    "time_offset_s": 0.0,
    "source": "MoveIt plan / file name",
    "points": [{"t_s": 0.0, "positions": [0.0, 0.0, 0.0, 0.0, 0.0]}]
  },
  "commanded": {
    "truth": "commanded",
    "time_offset_s": 0.0,
    "source": "target sequence actually sent",
    "points": [{"t_s": 0.0, "positions": [0.0, 0.0, 0.0, 0.0, 0.0]}]
  },
  "feedback": {
    "truth": "measured | measured_on_demand | simulated | commanded | unknown",
    "time_offset_s": 0.0,
    "source": "bus-servo get_state / joint_states echo / demo",
    "points": [{"t_s": 0.0, "positions": [0.0, 0.0, 0.0, 0.0, 0.0]}]
  },
  "executor": {"status": "Succeeded", "detail": "optional", "truth": "simulated"},
  "metadata": {"operator": "optional free-form JSON"}
}
```

Rules enforced on import:

| Rule | Limit |
|---|---|
| Joints | 1–8 unique names, ≤64 chars |
| Samples | 2–20,000 per stream, `t_s` finite, ≥0, strictly increasing |
| Positions/velocities | finite, exactly one value per joint |
| Truth | `planned.truth="planned"`, `commanded.truth="commanded"`; feedback as above |
| Timebase | must be `shared_trajectory_start` |

Invalid data returns `400 BAD_REQUEST` with `details.issues[]` carrying a JSON
path and message; nothing is stored.

### Truth rules

- `feedback.truth=measured` / `measured_on_demand`: tracking error is reported.
- `feedback.truth=simulated`: metrics are reported but every response keeps the
  simulated label and the demo notes it is not hardware validation.
- `feedback.truth=commanded`: the vendor `joint_states` command echo. Tracking
  error is **unavailable** and the reason is shown; command fidelity is still
  reported.
- `feedback.truth=unknown`: same as commanded for metric purposes.

Streams are never renamed into each other: the UI, analysis JSON and CSV all
carry `planned`, `commanded`, `feedback` and the feedback truth.

## Metric definitions (`analysis_version: g2-1`)

For a reference stream and a sample stream:

1. Align on the sample stream timestamps inside the overlap
   `[max(starts), min(ends)]`.
2. Linearly interpolate each reference joint at those timestamps.
3. `error = sample_position - reference_interpolated`.

Per joint:

| Metric | Definition |
|---|---|
| `max_abs_error_rad` | `max(abs(error))` |
| `rmse_rad` | `sqrt(mean(error²))` |
| `mean_abs_error_rad` | `mean(abs(error))` |
| `final_error_rad` | signed `error` at the last aligned sample |

Aggregate values pool all joints and samples. `command_fidelity` compares
planned vs commanded; `tracking` compares planned vs feedback and is only
available for non-echo feedback.

Sampling quality per stream:

| Field | Definition |
|---|---|
| `effective_rate_hz` | `(samples - 1) / (end - start)` |
| `max_gap_s` | largest timestamp delta |
| `gaps_over_1_5x_median` | deltas > 1.5 × median delta |
| `dropout_ratio` | `missing / expected`, `expected = round(duration / median_delta) + 1` |
| `interval_jitter_ms` | population standard deviation of deltas |

Cross-device latency is **not** computed. The only timebase evidence accepted
is a declared shared trajectory start; no clock synchronization is inferred.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/trajectories` | summaries (no full datasets) |
| POST | `/api/trajectories` | validate + analyze + store an imported dataset |
| POST | `/api/trajectories/demo` | deterministic simulated demo (idempotent by hash) |
| GET | `/api/trajectories/{id}` | dataset + stored analysis |
| POST | `/api/trajectories/{id}/recompute` | re-run analysis and compare with stored |
| GET | `/api/trajectories/{id}/export.csv?sample=feedback\|commanded` | aligned CSV |
| GET | `/api/trajectories/{id}/export.json` | dataset + analysis + metadata |

Imports are append-only rows keyed by the canonical SHA-256 of the normalized
dataset. Re-importing the same data returns the existing record with
`duplicate: true`; it does not create a second row. Each new import appends one
`TrajectoryImported` audit event. There is no trajectory endpoint that calls an
adapter, publishes ROS, acquires a lease, or writes a motion command.

## Replay safety

- Replay is a client-side clock over `record.dataset`.
- While playing, the global top bar shows `REPLAY`; leaving the page stops it.
- The banner states that no hardware command is sent.
- The Playwright test captures `last_seq` before replay and asserts no new audit
  event appears after play/pause.
- The backend has no `POST /api/trajectories/{id}/replay` motion route; the
  gateway still has no motion publisher and `hardware_motion=false`.

## Verification (2026-11-18, laptop, mock only)

```text
backend pytest            56 passed  (48 existing + 8 trajectory)
frontend build            tsc -b && vite build OK
Playwright mock E2E       8 passed  (7 existing + 1 trajectory)
```

Trajectory backend coverage: exact fixed-dataset metrics, gap/dropout quality,
command-echo tracking refusal, invalid datasets with paths, deterministic demo,
import/list/get/recompute, duplicate handling, CSV recomputation of every RMSE
and max error, export JSON, 404 handling, and no motion event on import.

Evidence bundle: [`evidence/studio_g2/20261118/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_g2/20261118/README.md).

## Limitations

1. One small **moving** hardware trajectory has been captured (supervised
   `camera_forward`, 0.892 s, 2026-11-18), but it is not a grasp, repeatability
   study or accuracy benchmark. Its measured error is dominated by servo pulse
   quantization and a one-period start offset. The bundled demo is synthetic
   and must not be cited as robot evidence.
2. Import is JSON only; rosbag2/MoveIt action-result import is not implemented.
   The G5 recorder writes the JSON contract; a bag is still not read directly.
3. The timebase is a declared shared start; delays across devices are not
   measured and are never reported as latency.
4. Velocity/acceleration curves are not plotted; only positions and position
   error. Velocity fields are preserved if present but not analyzed.
5. The dataset limit is 20,000 samples per stream and the UI downsamples lines
   above ~900 points per series; exports are not downsampled.
6. Replay has no speed selector (fixed 2×) and no synchronized camera view.
7. Imported data is stored in the same unauthenticated local SQLite database as
   the event log. Do not expose the backend without a separate security review.
