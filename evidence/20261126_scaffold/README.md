# M0 evidence — repository scaffold

> Status: **complete** as of 2026-11-26.
> Scope: the document set, admission manifest, repository gate and CI for
> `jcheng-studio` v0.1.0. No backend, no UI, no gateway call, no operator
> session and no hardware session.

## 1. Environment

| Item | Value |
|---|---|
| Repository | `JCHENG-Robotics/jcheng-studio` (public, created 2026-11-26T01:28:57Z) |
| Initial commit | `c041644` (`chore: initialize jcheng-studio repository`) |
| Operator host | operator laptop, Linux x86_64, Python 3.12.3, pytest 7.4.4 |
| CI host | `ubuntu-latest`, Python 3.12, pytest 8.3.4, PyYAML 6.0.2 |
| Robot | not used |
| Motion | none; no command was sent |
| DDS | not joined |

## 2. Commands and results

| Command | Result | Log |
|---|---|---|
| `python3 -m pytest -q tests` | pass, 13 tests | `gate.txt` |
| `bash scripts/check_repo_gate.sh` | pass | `gate.txt` |
| Manifest schema validation against `capability-manifest.v1.schema.json` | pass | `gate.txt` header; `tests/test_contract.py` |
| CI `studio-gate` on `c041644` | pass ([run 45736077396](https://github.com/JCHENG-Robotics/jcheng-studio/actions/runs/45736077396)) | [`job studio-gate`](https://github.com/JCHENG-Robotics/jcheng-studio/actions/runs/45736077396/job/139629552368) |

## 3. Claims supported

- The admission manifest parses, matches `VERSION`, validates against the v1
  schema and declares `hardware_access: none` with an empty runtime surface.
- The static scan finds no hardware, DDS/ROS, vendor-topic or motion token in
  code, configuration or scripts.
- No model weight, session database, operator env file or recording is tracked.
- The documentation set required by the organization standard exists and its
  relative links resolve.
- The same gate passes in CI on `main`.

## 4. Not covered

- Gateway client, console UI and operator sessions (M1–M3).
- Any hardware or motion behavior (M4).
- Reviewer sign-off; the operator is the only account.

## 5. Related

- [Acceptance report](../../ACCEPTANCE.md)
- [Roadmap](../../ROADMAP.md)
- [Architecture](../../docs/ARCHITECTURE.md)
