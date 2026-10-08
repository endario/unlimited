# Unified account controls

The owner wants visibility, incentive and never-offer controls in each macOS Settings account row, with an installed preview before PR review. They selected a conditional duration picker, compact existing-policy entries instead of the old target composer, equally spaced compact buttons, and hover help instead of the Accounts footnote.

## Account rows

- Eye changes Preferences.hidden through StripModel.arrange. Tracking, availability and saved policy are independent of visibility; hidden accounts remain in Settings.
- Multiplier opens an account-scoped editor. A numeric button label names only a stored canonical account/vendor override; otherwise show the sliders icon. Existing route indicators continue showing inherited/mixed policy without inventing one account-wide factor.
- Ban uses Tile.offerRows switch matching and StripModel.offer. New bans select the canonical account ID; undo preserves the matched switch's spelling. A covering vendor ban needs confirmation before allowing all its accounts, including identity-free readings; overlapping account bans may remain after vendor undo. Banned rows fade to 0.5 opacity but remain editable.
- Up/down retain Preferences.step's visible-account ordering and skip hidden rows. Hidden rows' arrows are disabled; boundary presses do not move a row.

The trailing controls occupy equal 26×26-point slots with 4-point gaps. Hover help explains each action; visibility/ban also expose accessible current state. Use native centering, no pixel offsets, and verify rendered ink rather than relying on layout boxes.

## Editor and other policies

One steering editor serves account popovers and existing broader-policy entries. It contains a multiplier picker, Until reset, and a duration picker when reset is off; context menus expose custom entry. Apply is an explicit activation; opening/dismissing writes nothing. 1x is a neutral override, while Clear override removes only that scope, leaving inherited policy intact.

Drafts load an exact live target/account group. Canonical account identity is used for account-row steering, separately from the matched switch spelling used for ban undo. Alias/orphan/broader groups remain in Other policies rather than appearing as canonical row overrides. Unconfigured editors start fresh; reset groups retain reset mode, including alias-selected groups edited by their stored selector; timed groups use a parser-compatible remaining duration. An explicit Apply reactivates the chosen duration and is last-writer-wins.

Other policies is only an existing-policy list with compact Edit/Clear popovers and unmatched-switch Allow actions. New vendor/model/route policies are CLI-only, as selected by the owner. No empty target composer remains. The owner subsequently clarified that steering should be per-account and authorized clearing the existing shared boosts rather than copying them into account overrides. With no broader policies left, this section disappears.

Account-scoped steering depends on callers supplying account bindings to choose/rank; setting a policy in this app does not retrofit caller configuration.

CLI capability probes, errors, command serialization and publication fencing remain authoritative. Disable only unavailable/pending policy controls; keep local visibility/reordering usable. No Python, CLI or persistence-schema changes.

## Acceptance

Run the full Swift suite and signed app build. Cover fresh/scoped/reset/timed/custom/neutral editor state, hidden-account refresh/relaunch, policy argv/readback/refusals, and a larger ordered list with hidden and banned entries.

Drive a separately identified fixture app for policy mutations, reorder boundaries/middle/lower rows, hidden-row skipping and order persistence. Replay timed commands through the real CLI with isolated HOME/config/cache. Then back up and replace the installed app, exercise real visibility with immediate restoration, inspect screenshots and measure button centers. Leave Settings open; stop before PR review.
