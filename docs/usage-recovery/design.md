# Usage recovery in the menu bar and CLI

## Intent

Show `?` instead of a quota-style warning when usage is unavailable, explain the reading failure in both the macOS popover and human-readable CLI status, and let a person sign in to the selected Claude account from its popover.

## Design

`UsageIssue` translates the reading's existing status/reason into plain language. The popover displays it in the same Box as usage cards. Current healthy readings have no issue; stale readings explain the missing fresh data. Backoff keeps the last recorded percentages and names the retry delay, not a specific unrecorded failure.

Failure titles, explanations and authentication classification have one owner: the builtin `src/unlimited/usage_errors.json` registry. Python's formatter loads it with the same `importlib.resources` pattern as the catalog. Exact reason matches take precedence over longest-prefix matches; unknown reasons use a readable fallback. Registry entries can interpolate the fixed diagnostic reason via `{reason}`. The app keeps its presentation-specific freshness policy and vendor-specific login capability, rather than moving those into the shared messages.

CLI status prints an unavailable marker, readable title and explanation, followed by a retry delay when one is active. Retained readings describe a paused refresh rather than asserting the cause was throttling. JSON status/why/retry fields and cache behavior do not change; human prose remains a presentation layer, not part of the factual reading protocol.

Claude authentication failures offer Sign in when `Launch.resolve` finds an executable account wrapper. Login opens a new iTerm window and runs that wrapper with `auth login --claudeai`. The browser still requires the person to select the correct account. Missing wrappers and terminal automation failures are explained. No login runs without an explicit click.

The wrapper runs inside a login shell because the menu-bar process does not supply its PATH. Before login, `/usr/bin/env -u` clears `CLAUDE_CONFIG_DIR` and the authentication overrides listed by the Anthropic adapter; the wrapper then selects its account configuration. The app does not read or embed tokens. Session and login commands share shell quoting followed by AppleScript string escaping; the existing tmux session command also quotes its inner shell invocation.

Popover-local error state follows the Settings window's `@StateObject` pattern: Command Line Tools cannot expand the installed SDK's `@State` macro.

## Boundaries

No Python schema/cache or polling changes. Retained successful readings can lose the original transient-failure cause; freshness/backoff copy is an intentionally lossy explanation. Arbitrary aliases without an established wrapper are not guessed into configuration paths. The existing whole-CLI-failure retained-strip behavior remains separate from this per-account recovery flow.

## Verification

Swift and Python regression tests cover readable reason mapping, unknown reasons, retry delays, unavailable symbols, freshness, backoff, recovery, selected-wrapper arguments, inherited override removal and composed command escaping. Visual checks render the native views without changing the real usage cache. Terminal checks use a harmless fake wrapper, never an actual login. A wheel-installed Python package and the signed app both need their own registry copy; Swift decoding tests also exercise the installed resource.
