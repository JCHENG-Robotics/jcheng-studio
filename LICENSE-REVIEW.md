# License review — jcheng-studio

> Status: **pending** as of 2026-11-26.
> No `LICENSE` file exists. This repository follows the organization rule that
> no license is added before the review is signed off
> (`.github/docs/license-review.md`).

## 1. Current facts

| Item | Fact |
|---|---|
| `LICENSE` file | none |
| Code in v0.5.0 | ported backend, UI, configs, tools, tests, the read-only `/v1` REST/WS clients, the M2a session store and a shell gate |
| Runtime dependencies | FastAPI, uvicorn, pydantic, PyYAML, numpy, websockets; React, React Router, Zod, Zustand, Vite |
| Test dependencies | `pytest`, `httpx`, `playwright` |
| Existing Studio code | ported from the frozen `jcheng-manipulation` baseline (MIT) at `a08b8b6`; port record in [`docs/MIGRATION.md`](docs/MIGRATION.md) |
| Vendor code or weights | none copied; the vendor-derived URDF/xacro and meshes were deliberately excluded. The test fixture is a minimal project-authored kinematic file with no vendor mesh data. |
| Session records | outside the repository; each carries its own handling rule |

## 2. Decision recorded on 2026-11-26

The repository is created **without** a `LICENSE` file until the ecosystem
review closes. Adding a license now would pre-empt the organization decision
and would suggest the eventual license covers session records and exported
evidence, which it will not.

The review must decide before the ecosystem reaches v1.0:

1. The copyright holder and year for new repositories.
2. Whether MIT is the intended license for application repositories, matching
   the frozen `jcheng-manipulation` baseline.
3. How the port of existing Studio code is recorded (authorship, copyright
   header, diff record) and whether that port needs a `THIRD_PARTY_NOTICES.md`.
   The baseline files carry no per-file copyright header; the port record is
   [`docs/MIGRATION.md`](docs/MIGRATION.md).
4. How session-record handling rules are surfaced without implying they are
   covered by the repository license.

## 3. Third-party inventory (to verify at M1/M2)

| Dependency | Use | Known/probable license | Risk |
|---|---|---|---|
| Python standard library | code | PSF | Low |
| FastAPI | backend HTTP API | MIT | Low |
| uvicorn | backend process | BSD-3-Clause | Low |
| pydantic | backend models | MIT | Low |
| PyYAML | manifest and config parsing | MIT | Low |
| numpy | pose/task math | BSD-3-Clause | Low |
| websockets | read-only `/v1/ws/state` client | BSD-3-Clause | Low |
| jcheng_core (ported) | statistics and point-cloud primitives | Project code, baseline MIT | Low |
| pytest / httpx | tests only | MIT / BSD-3-Clause | Low |
| React, React DOM, React Router | UI | MIT | Low |
| Zod, Zustand | UI validation and state | MIT | Low |
| Vite, TypeScript, Playwright, Vitest | build and test toolchain | MIT / Apache-2.0 | Low |
| Ported Studio code | console and configs, from the frozen baseline | Baseline is MIT; verify each file | Record the port and its notices |

No vendor SDK, firmware, driver, mesh or model weight is copied. The
vendor-derived URDF/xacro is read at runtime only when the operator sets
`ARMPI_URDF_PATH`; it is not part of this repository.

## 4. Open questions

- [ ] Confirm the copyright holder and year.
- [ ] Confirm the intended license for application repositories.
- [x] Record the port of the frozen baseline code in `docs/MIGRATION.md`; the
      authorship/notice decision remains open.
- [ ] Decide the notice requirement for the web and frontend stack.
- [ ] Record the ecosystem-wide decision in `.github/docs/license-review.md`.

## 5. Related

- [`.github/docs/license-review.md`](https://github.com/JCHENG-Robotics/.github/blob/main/docs/license-review.md)
- [Acceptance report](ACCEPTANCE.md)
- [Migration record](docs/MIGRATION.md)
- [README](README.md)
