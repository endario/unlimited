# Manual Steering Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development. Track each deliverable with the checkboxes below.

**Goal:** Implement #156 and account-label steering indicators in the macOS app.

**Architecture:** A shared resolver owns scope matching, reset snapshots and effective factors. The CLI persists local policy and overlays it on readings; pure ranking takes explicit inputs. The macOS consumer uses the namespaced schema-1 overlay and writes through the CLI.

**Tech Stack:** Python standard library/unittest; Swift 6, SwiftUI/AppKit, Swift Testing.

**Spec:** docs/incentives/design.md

## Global constraints

- Generic target/policy names; no caller scenarios or configuration.
- Positive finite multipliers, both sides of one; quota and observed evidence unchanged.
- Default latest upcoming account reset, fixed at activation; explicit duration overrides it.
- Additive schema-1 JSON; old CLI readings remain decodable.
- Shared persistence and targeting; no fork of the switches mechanism.
- Account context is explicit; existing unbound callers receive unresolved diagnostics, not guessed steering or a new hard failure.

## Review focus

- An account alias and canonical ID must match the same policy.
- A reset-based broad target expires independently for its activation-time accounts.
- Neutral overrides suppress inherited badges without deleting the inherited setting.
- A delayed pre-mutation reading must not restore a cleared indicator.
- Variable-width cells must use the same geometry for rendering, clicks and popover anchors.

## Task 1: Backend policy, persistence and CLI

**Files:** new src/unlimited/incentives.py and shared state persistence helper; catalog.py, cli.py, choice.py, show.py; tests/test_incentives.py plus existing switch/choice/help coverage.

**Interfaces:** `rank(..., incentives=None, accounts=None)` stays pure. `choose` loads local policy unless supplied. `accounts` binds offering IDs to explicit identities. `incentive TARGET FACTOR [--account NAME] [--for DURATION] [--json]`, omitted target lists, `FACTOR=off` clears exact scope.

- [ ] Write and run failing unittest cases for activation expiry, explicit duration bypass, positive/negative direction, neutral override, alias matching, independent reset expiries, invalid state and removal.
- [ ] Extract locked atomic JSON writing and target aliases; run existing off/on/list tests unchanged.
- [ ] Implement one resolver for live policy, precedence and provenance. Reject zero/negative/nonfinite/bool factors and nonfinite derived costs. Prune expired entries on mutation.
- [ ] Integrate one time-cost division helper into deterministic score and Thompson draws; keep raw t_next. Test the unchanged quota, preference, durations and seed replay.
- [ ] Add CLI set/list/clear and explicit choose account bindings. Missing context is emitted in incentives_unresolved and stderr; excluded/off candidates remain unavailable.
- [ ] Overlay steering after cache reads under schema 1:

```json
{"steering":{"settings":[{"target":"openai","account":null,"multiplier":10,"until":"2026-10-09T00:00:00+00:00"}],"routes":[{"id":"gpt-6-luna","target":"openai","account":null,"multiplier":10,"until":"2026-10-09T00:00:00+00:00"}]}}
```

- [ ] Add terminal up/down glyph summary using the resolved route factors and the reciprocal thresholds in the design.
- [ ] Run `PYTHONPATH=src uv run python -m unittest discover -s tests -v` and an isolated CLI set/read/choose/clear round trip with fixture readings. No live operator policy is changed by tests.

## Task 2: macOS controls and indicators

**Files:** new shared steering model/layout and indicator/editor view where appropriate; Reading.swift, Runner.swift, Tile.swift, StripModel.swift, SettingsView.swift, StripView.swift, PopoverView.swift, main.swift; UnlimitedKitTests steering/layout tests.

**Consumes:** Task 1's frozen read overlay and CLI contract. Global list entries hold target/account/multiplier/activated_at/common until or reset bindings; per-account settings have concrete until.

- [ ] Add failing Swift tests for old/new decoding, expired route factors, neutral overrides, reciprocal thresholds and relabeled tiles.
- [ ] Decode steering optionally; add Runner list/set/clear methods and argument-construction tests. Do not write policy files or credentials from the app.
- [ ] Add settings controls for vendor/account scopes and custom model/route target; multiplier presets and custom value, default Until reset or explicit duration, Apply and Clear. Show inherited/current values, expiry, pending state and actionable CLI errors.
- [ ] Extend sequencing to prevent stale policy readings from winning after edits. Redraw at the nearest expiry and reconcile after wake; test pure sequencing/expiry seams where possible.
- [ ] Render shared small green up/red down triangles alongside account names. Strength below 5: one; 5..<10: two; >=10: three; 1: none. Mixed route directions display both with scope-aware help. Preserve quota underline and health display.
- [ ] Add one variable-width geometry contract; use it in TileView, hit testing and popover anchoring. Test boundary clicks after steered cells.
- [ ] Run `make -C macos test` and `make -C macos app`. Render actual SwiftUI cells/controls in light/dark appearances and measure ink extents, triangle count and clipping; retain representative screenshots.

## Task 3: Integrate, document, review and release

**Files:** README.md, docs/choice.md, docs/catalog.md, macos/README.md, pyproject.toml; refresh this topic's artifacts to final behavior.

- [ ] Verify both slices together and refresh CLI examples/API documentation. Record any unresolved route/account limitations plainly.
- [ ] Re-run Python and Swift suites, build the app and package, and exercise the real CLI through a fixture binary used by the UI.
- [ ] Self pre-pass with code-review; verify findings before changes. Open draft PR and run independent-review, fixing verified findings until the gate permits merge.
- [ ] Merge per the repository SOP; ship a Python version bump/release, confirm PyPI and update the local unlimited install. Do not invent remote machine access.

## Baseline

The Python unittest suite and macOS Swift Testing suite passed before implementation. `pytest` is not a project dependency; use unittest as CI does.
