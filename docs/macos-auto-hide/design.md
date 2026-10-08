# Auto-hide normal menu-bar accounts

The owner requested a default-enabled global setting, hover restoration, and later a five-second exit grace period with slower fades. This is macOS presentation only: tracking, policies, ordering and manual eye preferences are unchanged.

## Presentation

Tile.isRoutine consumes the existing visual classification, not a new forecast: readable percentage, white health, no dimming/ban/non-neutral steering, and no unread or non-normal alternate window. The best-account underline alone does not exempt a tile.

StripModel.tiles remains the manually visible list. displayedTiles filters routine accounts only when the setting is enabled, hover expansion is inactive, the popover is closed and no read problem is reported. A read failure keeps recently cached normal percentages visible. Settings always lists all accounts; hover never restores manual eye-hidden accounts.

If every manual-visible account is routine, a distinct presentation-only summary Tile supplies ellipsis geometry. It never passes through Preferences.apply or becomes account selection. Native status-button help/accessibility text explains the summary, whose activation opens the first manually visible account in saved order.

## Hover and animation

Local/global AppKit pointer monitors use the status-item window's current screen frame, including its top-edge hit area. Drag and release events also update containment. Monitors are removed when auto-hide is disabled.

A real inactive-status-item probe reproduced oscillation with tracking-area callbacks: expanding moved the item away from a stationary pointer, provoking more callbacks. Event-driven checks avoid that callback loop. The requested exit delay now bridges item repositioning without retaining a stale, non-clickable rectangle.

HoverDelay owns the deadline. Exit starts a fixed grace period; further outside movement does not extend it. Reentry or an open popover cancels the pending collapse. After the grace period, normal tiles fade before the status item becomes compact. Expansion fades those tiles in; exceptional accounts are not faded out. Reduce Motion disables the effect. Width itself is not animated because macOS controls status-item placement.

Rendering, click capture and popover anchors share StripLayout over displayedTiles. Capture the clicked account before pinning the full layout. Popover selection resolves against the manual list. Closing starts a fresh grace period if the pointer is outside. Wake/screen changes cancel transient hover state and reevaluate containment.

Persist only Preferences.autoHideNormal. Explicit decoding defaults missing fields without losing old labels, hidden set or order. Hover/fade state does not save preferences or call the CLI.

## Acceptance

Automated coverage includes visual eligibility, old/partial payload preservation, setting persistence, manual-hidden exclusion, read-failure expansion, refresh during expansion, summary selection, grace-period timing, reentry cancellation and popover pinning.

Native fixture/installed checks cover inactive enter/exit, both item edges, top-edge hover, repeated resizing, mixed/all-normal layouts, selected-account popovers, moving into an open popover, accessibility activation, toggle-off, drag/release exit and timed fade frames. Rendered screenshot measurements belong to the private execution evidence. Multi-display testing requires hardware not attached to this machine.
