# Unified Account Controls Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. Owner preview precedes PR review.

**Goal:** Put visibility, account incentives and never-offer controls in one row.
**Architecture:** Preferences owns visibility/order; existing CLI commands own policy. A shared steering editor serves account popovers and compact existing-policy entries.
**Tech Stack:** SwiftUI, Swift Testing, macOS accessibility automation.
**Spec:** [design.md](design.md).

## Constraints

No Python, persistence-schema or CLI-contract change. Keep canonical steering identity separate from ban undo spelling. Preserve inherited policy rather than fabricating an account factor. Opening/dismissing writes nothing; unavailable/pending policy actions cannot publish optimistic state. New broader policy creation is CLI-only, as selected by the owner.

## Task 1: Scoped editor

- [x] Test exact live scope, fresh drafts, reset/timed mode, expired groups, custom factors and neutral overrides.
- [x] Implement SteeringDraft, observable editor state and shared SteeringEditor.
- [x] Provide multiplier/reset controls, conditional duration picker, Apply/Clear and custom-value context menus.

## Task 2: Account rows

- [x] Reuse Preferences.hidden, Tile.offerRows and StripModel.steer/offer.
- [x] Add eye, multiplier, ban and reorder actions; retain vendor-unban confirmation and usable faded rows.
- [x] Add hidden refresh/relaunch, policy argv/readback/refusal and long-list ordering regressions.
- [x] Replace the old target composer with compact existing-policy entries.
- [x] Use equal compact trailing slots and hover help; remove the Accounts footnote.

## Task 3: Verification and preview

- [x] Run the Swift suite and signed app build.
- [x] Drive fixture hide/show, ban/unban, Apply/Clear, duration, untouched dismissal and fresh-account drafts.
- [x] Check vendor undo retains overlapping account bans.
- [x] Replay timed policy argv through the real CLI under isolated HOME/config/cache.
- [x] Drive a larger GUI list: boundary presses, middle/lower moves, hidden-row skipping, banned-row moves and relaunch persistence.
- [x] Measure rendered button ink spacing and alignment, including numeric and faded rows.
- [x] Back up the installed bundle, install the build, verify real hide/show and restore the owner's preferences.
- [x] Following explicit owner authorization, back up and clear shared vendor boosts without copying them into account overrides; verify bans unchanged and neutral UI has no extra policy section.
- [x] Finish the startup warning guard's delayed-read GUI check and install its rebuilt bundle.
- [x] Preserve the refreshed installed screenshot and leave Settings open for owner preview.
