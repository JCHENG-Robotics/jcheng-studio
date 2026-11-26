# M0 evidence — repository scaffold

> Status: **in progress** as of 2026-11-26.
> Scope: the document set, admission manifest, repository gate and CI for
> `jcheng-studio` v0.1.0. No backend, no UI, no gateway call, no operator
> session and no hardware session.

## 1. Environment

| Item | Value |
|---|---|
| Host | operator laptop, Linux x86_64, Python 3.12.3, pytest 7.4.4 |
| Repository | `JCHENG-Robotics/jcheng-studio`, `main` |
| Robot | not used |
| Motion | none; no command was sent |
| DDS | not joined |

## 2. Commands and results

| Command | Result | Log |
|---|---|---|
| `python3 -m pytest -q tests` | pending | — |
| `bash scripts/check_repo_gate.sh` | pending | — |
| Manifest schema validation against `capability-manifest.v1.schema.json` | pending | — |
| CI `studio-gate` | pending | — |

## 3. Claims supported

- The admission manifest parses, matches `VERSION` and declares
  `hardware_access: none` with an empty runtime surface.
- The static scan finds no hardware, DDS/ROS, vendor-topic or motion token in
  code, configuration or scripts.
- No model weight, session database, operator env file or recording is tracked.
- The documentation set required by the organization standard exists and its
  relative links resolve.
- The same gate passes in CI on `main`.

## 4. Not covered

- Gateway client, console UI and operator sessions (M1–M3).
- Any hardware or motion behavior (M4).
- CI success until the first run is recorded above.

## 5. Related

- [Acceptance report](../../ACCEPTANCE.md)
- [Roadmap](../../ROADMAP.md)
- [Architecture](../../docs/ARCHITECTURE.md)
