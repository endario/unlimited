# Usage Recovery Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for inline, sequential execution. Track the test/build results below.

**Goal:** Explain unavailable usage and offer account-specific interactive Claude login.

**Architecture:** UnlimitedKit derives presentation from existing readings and builds escaped account-wrapper commands. The app renders the issue and routes explicit clicks through its existing iTerm bridge.

**Tech Stack:** Swift 6, SwiftUI/AppKit, Swift Testing; macOS 14 minimum.

**Spec:** [design.md](design.md)

## Constraints

No cache/schema, credential or unrelated incentive changes. No automatic login. Reuse `Launch.resolve`, `Box` and `Card.until`. Keep backoff percentages. The CLI extension is covered by [cli-plan.md](cli-plan.md).

## Review focus

- Hostile wrapper paths preserve argument boundaries across shell and AppleScript layers.
- Inherited authentication overrides do not change the selected wrapper's account.
- Non-Claude expired credentials and permission/network failures never launch Claude login.
- Missing wrappers and terminal automation failures are explained.
- A recovered reading removes its issue; backoff does not manufacture a cause.

## Task: escaped terminal recovery

Files: `macos/Sources/UnlimitedKit/Launch.swift`, `macos/Sources/UnlimitedKit/Runner.swift`, `macos/Sources/Unlimited/Launching.swift`, `macos/Tests/UnlimitedKitTests/LaunchTests.swift`.

- [x] Add red tests for `Launch.loginCommand`, `Launch.terminalCommand`, `Launch.terminalScript(command:)`, and shell/AppleScript quoting. Exercise real shell argument decoding and AppleScript string decoding, with spaces, apostrophes, quotes, backslashes, CR/LF and shell metacharacters.
- [x] Run `make -C macos test`; confirm missing members fail before implementation.
- [x] Implement single-quoted POSIX arguments (`'` becomes `'\''`), then escape backslashes, double quotes and CR/LF in AppleScript strings. Existing tmux launch quotes both its inner command and its outer argument. Login uses `/usr/bin/env -u` for the exact override set in the design and the resolved wrapper followed by `auth login --claudeai`.
- [x] Have `openTerminal(command: String? = nil) async -> String?` use the shared generated script and return a readable failure. Run `/usr/bin/osascript` off the UI actor through the existing bounded `Runner.run` plumbing; both UI callers surface failures. Prevent overlapping terminal launches and scope completion errors to the selected account.
- [x] Run tests; later execute the real terminal bridge against a temporary harmless wrapper and check its arguments and isolated environment.

## Task: unavailable usage presentation

Files: `macos/Sources/UnlimitedKit/UsageIssue.swift` (new), `macos/Sources/UnlimitedKit/Tile.swift`, `macos/Sources/Unlimited/PopoverView.swift`, `macos/Sources/Unlimited/StripModel.swift`, `macos/Tests/UnlimitedKitTests/UsageIssueTests.swift` (new), `macos/Tests/UnlimitedKitTests/StripTests.swift`.

Interface: `UsageIssue` has `title: String`, `message: String`, `offersLogin: Bool`; `Reading.issue(now: Date) -> UsageIssue?` derives it without side effects.

- [x] Add red tests for `Tile.Value.unread.text == "?"`, stale/unknown/question mark semantics, known value/waiting/no-window preservation, auth eligibility by vendor/reason, generic fallback, freshness and retained backoff values, and healthy recovery.
- [x] Run tests to confirm the new requirements fail.
- [x] Implement grouped readable failure messages. Future retry deadlines are shown using `Card.until`; status `ok` retains honest freshness/backoff wording without reusing a failure cause. Unknown producer reasons remain readable and available in help.
- [x] Render `UsageIssueView` in the existing Box, between header and cards. Sign in requires both `offersLogin` and a resolved launch. Missing launch gets a caption. Use popover-local launch-error state, cleared on account change. Sign in invokes `openTerminal(command: launch.loginCommand)` and keeps refresh available. The existing terminal button closes only after a successful launch.
- [x] Update the symbol-related model comment, root README glyph description, and macOS README login dependency; do not rewrite unrelated docs.

## Verification

- [x] `make -C macos test` and `make -C macos app`.
- [x] Temporary native renderer: actual failure panel/strip in light and dark; inspect screenshots, text wrapping, and rendered ink extents at the popover's existing width. Do not mutate the real usage cache or account state.
- [x] Actual generated login command against a fake wrapper through iTerm, never actual login; verify selected wrapper arguments and absent inherited overrides.
- [x] Self code review, then local-only independent review of a snapshot containing this change without the pre-existing incentive work. Verify findings and fix the contained defects.
- [x] Refresh design/plan outcomes and report local build/review status. Leave commit, PR and installation to explicit authorization if not requested.
