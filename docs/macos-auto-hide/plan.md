# Auto-hide normal accounts Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for coupled app changes; the kit eligibility/preferences slice can run independently.

**Goal:** Default-enabled presentation collapse, hover restoration, and a delayed gentle fade back to compact form.

**Architecture:** Tile owns visual eligibility; Preferences persists the setting. StripModel derives the displayed list without changing manual visibility. AppDelegate coordinates shared layout, native pointer monitors and a cancellable HoverDelay deadline.

**Tech Stack:** Swift 6, SwiftUI, AppKit, Swift Testing, macOS 14+.

**Spec:** design.md

## Global Constraints

- macOS presentation only; no CLI/schema/policy changes.
- Default enabled, including older preference payloads.
- Manual eye-hidden accounts stay hidden on hover.
- Five-second exit grace period; no delay slider or polling.
- Slow ease-in/ease-out fade, disabled by Reduce Motion.
- Build/install/show before PR review; no click-only intermediate release.

## Review Focus

- Status-item repositioning must not create a callback resize loop or a stale retained click target.
- Old payloads must retain labels, hidden set and order.
- Click captures the displayed account before expansion changes positions.
- Open popover and reentry cancel pending collapse; outside movement does not prolong the deadline.
- Summary, top-edge hover and drag-release exit remain usable; disabled auto-hide removes pointer monitors.

### Task 1: Eligibility and compatible setting

Files: macos/Sources/UnlimitedKit/{Tile,Preferences}.swift; macos/Tests/UnlimitedKitTests/AutoHideTests.swift.

Interfaces: `Tile.isRoutine: Bool`; `Preferences.autoHideNormal: Bool` default true.

- [x] Write eligibility and old/partial preference payload tests; establish missing-member red.
- [x] Implement visual predicate from Tile fields, not Reading/forecast calculations.
- [x] Explicitly decode all preference fields with compatible defaults.
- [x] Verify combined suite, preserving existing Preferences.apply semantics.

### Task 2: Presentation and timed exit

Files: macos/Sources/Unlimited/{StripModel,HoverDelay}.swift; macos/Tests/UnlimitedTests/{StripModelTests,HoverDelayTests}.swift.

Interfaces: `StripModel.hoverExpanded`, `fadingNormal`, `displayedTiles`, `collapsedNormal`, `displayedAccount(at:)`; `HoverDelay.update(inside:open:now:)`, `collapse(now:)`, `reset()`.

- [x] Write filtered/manual-list, hover/open pin, refresh, manual-hidden, summary, failure and setting-persistence tests; establish red.
- [x] Implement computed presentation and distinct summary geometry, without preference or account identity entries.
- [x] After owner requested delayed collapse, write grace-period/reentry/popover deadline tests; establish red.
- [x] Replace retained-region state with a deadline: entry/open cancels it, first exit sets it, expiry begins fade.
- [x] Verify suite.

### Task 3: Native UI and installed preview

Files: macos/Sources/Unlimited/{main,StripView,SettingsView}.swift; macos/README.md.

- [x] Render displayedTiles, native summary accessibility/help, and General toggle.
- [x] Use shared StripLayout for fitting, click capture and anchors; keep popover lookup on manual tiles.
- [x] Integrate local/global movement/drag/release monitors over the status-item window; remove them when disabled.
- [x] Cancel pending tasks on reentry, popover open, wake/screen changes and termination.
- [x] Implement slower fade-in/out without animated status-item width; honor Reduce Motion.
- [x] Finish native top-edge/timing/fade checks on final sources, rerun `make -C macos test` and `make -C macos app`, verify signature/resources.
- [x] Install/show final build and refresh rendered measurements.
- [ ] Commit only feature scope, self code-review then independent PR review; no release before visual acceptance.

Multi-display testing is unavailable with the currently attached hardware. Private execution evidence records commands, outcomes and rendered measurements.
