# Legacy Studio records

These documents were written for the frozen `jcheng-manipulation` Studio
(`armpi-debug-studio/`, v1.0.0 baseline). They are kept here as design and
evidence records, not as claims of this repository.

- **Source:** `JerichoCheng/jcheng-manipulation`, HEAD `a08b8b6`, working tree
  on 2026-11-26. `g8-task-takeover.md` includes uncommitted edits from that
  working tree.
- **Verification here:** the console code is ported and its offline tests pass;
  no hardware session was run in `jcheng-studio`. The test counts in these
  documents describe the baseline, not the port.
- **Links:** links to `ros2_gateway/`, `ros2_ws/`, `docs/phase2/` and
  `evidence/` point at the baseline repository. Those assets were deliberately
  not copied; see [`../MIGRATION.md`](../MIGRATION.md).

## Index

| Record | Content |
|---|---|
| [`STUDIO-README.md`](STUDIO-README.md) | Baseline Studio overview, layout and status notes |
| [`e7-gateway-verification.md`](e7-gateway-verification.md) | Read-only gateway verification (2026-11-17/18) |
| [`g1-verification.md`](g1-verification.md) | G1 mock slice verification |
| [`g2-trajectory-inspector.md`](g2-trajectory-inspector.md) | Offline trajectory inspector |
| [`g3-experiment-lab.md`](g3-experiment-lab.md) | Offline experiment lab |
| [`g4-visual-workbench.md`](g4-visual-workbench.md) | URDF/TF visual workbench |
| [`g5-trajectory-recorder.md`](g5-trajectory-recorder.md) | Gateway trajectory recorder |
| [`g6-motion-write-path.md`](g6-motion-write-path.md) | Supervised single-joint write path |
| [`g7-pose-takeover.md`](g7-pose-takeover.md) | Five-joint pose and gripper takeover |
| [`g8-task-takeover.md`](g8-task-takeover.md) | Camera-to-task takeover and object forms |

The product requirements are at [`../PRD.md`](../PRD.md).
