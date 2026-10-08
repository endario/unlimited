# Auto-hide normal accounts Implementation Plan

**Spec:** design.md

### Task 1: Eligibility and compatible setting

- [x] Write eligibility and old/partial preference payload tests; establish missing-member red.

### Task 2: Presentation and timed exit

- [x] After owner requested delayed collapse, write grace-period/reentry/popover deadline tests; establish red.
- [x] Verify suite.

### Task 3: Native UI and installed preview

- [x] Finish native top-edge/timing/fade checks on final sources, rerun `make -C macos test` and `make -C macos app`, verify signature/resources.
- [x] Install/show final build and refresh rendered measurements.
- [x] Commit only feature scope, self code-review then independent PR review; no release before visual acceptance.
