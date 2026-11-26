# G3 Experiment Lab (offline records)

> Status: **implemented and offline-validated (2026-11-18)**. The Studio can
> create immutable experiment definitions, record append-only paired trials,
> validate failure codes and optional artifact references, and analyze complete
> pairs. No hardware experiment or trial was recorded. The bundled demo is
> explicitly simulated.

## Scope

This closes the Studio-facing part of PRD G3:

- experiment definition: purpose, origin, hardware/calibration/mapping versions,
  stop rules, planned pairs, seed and fixtures;
- `experiment_id` → `pair_id` → `trial_id` traceability with method, order,
  fixture, success, continuous error and failure code;
- failure codes covering communication, timeout, joint limit, planning,
  trajectory execution, vision, calibration, grasp failure, safety veto and
  unknown;
- optional `trajectory_id` link to a G2 dataset and artifact references
  (`rosbag2`, log, image, CSV, trajectory, other);
- CSV + metadata JSON export, with the same paired statistics as the Phase 3
  `armpi_analysis.paired_trials` tooling;
- a page under `Experiment Lab`, with a deterministic simulated demo.

It does **not** execute a trial, move the robot, or read a rosbag2 file. A
`rosbag2` artifact is stored as a path/URI reference only.

## Experiment definition

`POST /api/experiments` accepts:

| Field | Rule |
|---|---|
| `name` | required, non-empty, ≤200 chars |
| `purpose` | required, ≤2000 chars |
| `data_origin` | `hardware` or `simulated` |
| `operator`, `robot_id` | optional strings |
| `hardware_config`, `calibration_version`, `mapping_version` | optional strings for condition traceability |
| `stop_rules` | optional protocol text |
| `planned_pairs` | integer 0–200; 0 means manual pair IDs, no plan |
| `seed` | integer 0–2³¹−1 for pair and method-order randomization |
| `fixtures` | empty, one entry (repeated), or one per planned pair |
| `bootstrap_samples` | integer 100–50,000 (default 10,000) |
| `confidence` | 0.5–0.999 (default 0.95) |
| `notes` | optional |

The definition is immutable once stored. A plan with `planned_pairs > 0`
assigns `p001…` and randomizes both pair order and the method order inside each
pair. The plan is returned with the definition, and trial validation enforces
the planned pair/method, `trial_order` and fixture.

`data_origin` determines the trial `truth`: `simulated` experiments produce
`truth=simulated` trials; `hardware` experiments produce `truth=measured`
trials. The API does not accept a per-trial truth override, so a simulated
trial cannot be labelled measured.

## Trial record

`POST /api/experiments/{id}/trials` accepts:

| Field | Rule |
|---|---|
| `pair_id` | non-empty, ≤64; one baseline + one intervention per pair |
| `trial_order` | 1 or 2, unique within the pair; must match the plan |
| `method` | `baseline` or `intervention` |
| `fixture` | non-empty; must match the plan when one exists |
| `grasp_success` | boolean |
| `continuous_error_mm` | finite, ≥0; required when success is true |
| `rotation_error_deg` | optional finite, ≥0 |
| `failure_code` | required on failure; forbidden on success |
| `failure_detail` | optional text on failure |
| `duration_s` | optional finite, ≥0 |
| `measurement_source` | `vision`, `caliper`, `fixture`, `operator`, `simulated`, `unknown` |
| `trajectory_id` | optional; must reference an existing G2 dataset |
| `artifacts` | up to 20 `{kind, path, note}` references |
| `operator_notes` | optional |

Duplicate `pair/method` and `pair/trial_order` combinations are rejected
(unique constraints plus a pre-check with a readable error). Trials are
append-only; there is no edit or delete endpoint. A mistaken trial therefore
requires a new experiment definition rather than silent mutation.

### Failure codes

| Code | Meaning |
|---|---|
| `COMMUNICATION` | link/serial/gateway failure |
| `TIMEOUT` | timeout |
| `JOINT_LIMIT` | joint limit / range violation |
| `PLANNING_FAILED` | MoveIt/planner failure |
| `TRAJECTORY_FAILED` | trajectory execution failure |
| `VISION_FAILED` | vision/localization failure |
| `CALIBRATION_INVALID` | calibration invalid or missing |
| `GRASP_FAILED` | grasp/miss/slip |
| `SAFETY_VETO` | safety gate rejected the action |
| `UNKNOWN` | unclassified |

A failed trial may have a null continuous error (for example, a missed grasp
with no displacement measurement); complete pairs with a missing error are
excluded from the continuous analysis and counted in a warning.

## Analysis (`analysis_version: g3-1`)

For each complete pair (`baseline` and `intervention` both present), with
`d_i = error_intervention,i − error_baseline,i`:

- **continuous outcome**: mean/SD of `d`, mean improvement
  `−mean(d)`, seeded bootstrap interval for `mean(d)`, Cohen's `d_z`, and the
  exact two-sided sign test (wins = `d < 0`);
- **binary outcome**: per-method success rates, discordant counts
  (`baseline_only`, `intervention_only`) and the exact McNemar test;
- **order effect**: paired differences split by which method ran first
  (descriptive only);
- **failure codes** per method;
- **warnings** for simulated data, fewer than 8 complete pairs, fewer than 10
  discordant pairs (binary endpoint underpowered), and complete pairs excluded
  from the continuous analysis.

The formulas mirror `ros2_ws/src/armpi_analysis/armpi_analysis/paired_trials.py`
so the Studio and ROS tooling agree for the same records. Analysis is
recomputed deterministically from the stored trials, seed, bootstrap count and
confidence; `analysis_version` is stored on the experiment and returned in the
report and exports.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/experiments` | experiment summaries + failure-code/method maps |
| POST | `/api/experiments` | create an immutable definition |
| POST | `/api/experiments/demo` | deterministic simulated 16-pair demo (idempotent) |
| GET | `/api/experiments/{id}` | definition + plan + trials + analysis |
| POST | `/api/experiments/{id}/trials` | append one trial |
| GET | `/api/experiments/{id}/analysis` | recompute the paired report |
| GET | `/api/experiments/{id}/export.csv` | trial rows for script recomputation |
| GET | `/api/experiments/{id}/export.json` | definition + trials + analysis |

Every create/record path appends an audit event (`ExperimentCreated`,
`TrialRecorded`, or `ExperimentSeeded` for the demo). There is no experiment
endpoint that acquires a lease, dispatches motion, publishes ROS, or touches the
gateway.

## Verification (2026-11-18, laptop, mock only)

```text
backend pytest        65 passed  (56 existing + 9 experiment)
gateway pytest        12 passed
Playwright mock E2E    9 passed  (8 existing + 1 Experiment Lab)
frontend build        tsc -b && vite build OK
```

The simulated demo result is reported honestly:

```text
16 complete pairs; 12 pairs have both continuous errors
mean improvement 1.161 mm; 95% bootstrap CI [-1.43, -0.89] mm
sign test 12 wins / 0 losses / 0 ties, p = 4.88e-4
success 68.8% -> 87.5%; McNemar p = 0.375 (underpowered warning)
```

The binary endpoint is deliberately underpowered at this size; the UI and
report warn against claiming a significant success-rate improvement. Evidence:
[`evidence/studio_g3/20261118/`](https://github.com/JerichoCheng/jcheng-manipulation/blob/main/evidence/studio_g3/20261118/README.md).

## Limitations

1. No real hardware trial exists; the demo and all checked-in trial data are
   simulated.
2. Failure codes are coarse primary categories; secondary causes belong in
   `failure_detail` and artifact references.
3. `rosbag2`/log/image artifacts are references only. The Studio does not copy,
   read or validate the files, so a missing path is only detectable by the
   operator or an external script.
4. A fixture is assumed geometrically identical across its pairs; this is not
   verified by the Studio.
5. There is no trial edit/void workflow. Append-only records protect integrity,
   but a mistake requires a new experiment.
6. Analysis uses a seeded bootstrap and exact tests; it does not correct for
   multiple endpoints or multiple experiments.
7. Imported trials live in the same local, unauthenticated SQLite database as
   the event log and G2 datasets. Do not expose the backend without a separate
   security review.
