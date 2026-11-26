# License review — jcheng-studio

> Status: **pending** as of 2026-11-26.
> No `LICENSE` file exists. This repository follows the organization rule that
> no license is added before the review is signed off
> (`.github/docs/license-review.md`).

## 1. Current facts

| Item | Fact |
|---|---|
| `LICENSE` file | none |
| Code in v0.1.0 | documentation, tests and a shell gate; no runtime code |
| Runtime dependencies | none in v0.1.0 |
| Test dependencies | `pytest` (MIT), `PyYAML` (MIT) — CI only |
| Existing Studio code | in the frozen `jcheng-manipulation` baseline (MIT); not copied here |
| Vendor code or weights | none copied |
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
3. How a port of existing Studio code from the frozen baseline is recorded
   (authorship, copyright header, diff record) and whether that port needs a
   `THIRD_PARTY_NOTICES.md`.
4. How session-record handling rules are surfaced without implying they are
   covered by the repository license.

## 3. Third-party inventory (to verify at M1/M2)

| Dependency | Use | Known/probable license | Risk |
|---|---|---|---|
| Python standard library | code | PSF | Low |
| pytest | tests only | MIT | Low |
| PyYAML | manifest parsing | MIT | Low |
| Web framework (TBD) | M1 backend | TBD | Decide before adoption |
| HTTP/WS client (TBD) | M1 gateway client | TBD | Decide before adoption |
| Frontend toolchain (TBD) | M2 UI | TBD | Decide before adoption |
| Ported Studio code | M1, from the frozen baseline | Baseline is MIT; verify each file | Record the port and its notices |

## 4. Open questions

- [ ] Confirm the copyright holder and year.
- [ ] Confirm the intended license for application repositories.
- [ ] Decide the record for porting code from the frozen baseline.
- [ ] Decide the notice requirement for the web and frontend stack.
- [ ] Record the ecosystem-wide decision in `.github/docs/license-review.md`.

## 5. Related

- [`.github/docs/license-review.md`](https://github.com/JCHENG-Robotics/.github/blob/main/docs/license-review.md)
- [Acceptance report](ACCEPTANCE.md)
- [README](README.md)
