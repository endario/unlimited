# Unlimited for the menu bar

One tile per account, showing its monthly window where the plan enforces one, else its weekly
window, drawn from `unlimited read --json`. The app shows usage only: it reads no credential,
calls no vendor and writes nothing through the CLI. It needs `unlimited` 0.0.23 or later in
`~/.local/bin`, `/opt/homebrew/bin` or `/usr/local/bin`.

Unavailable or stale usage displays `?`; the popover explains the reported failure. A window that
has spent its limit displays `✘` instead of its percentage. Where the CLI supplies
locally observed gateway-log usage, the strip shows its rolling weekly figure: estimated cost as a
percentage of a configured USD budget, a configured token-budget percentage, or compact raw tokens.
Settings and the popover retain an exact token total; snapshot and cost details are in tooltips.
These observations remain visible beside a refused or stale latest read and never affect quota selection.
For Claude
sign-in failures, **Sign in** opens iTerm and runs the account's executable wrapper from
`~/.local/bin` with `auth login --claudeai`. Choose the correct account in the browser, then
refresh. Without a matching wrapper, sign in through that account's Claude Code setup.

Settings keeps each account's controls together: the eye shows or hides its menu-bar tile, and
the arrows reorder it. Neither stops tracking.

Auto-hide normal accounts is enabled by default in General. Readable accounts disappear from the
compact strip unless they ask for attention: red, sprint, under-used or dimmed accounts stay, as
do measured token figures and prepaid balances. Amber accounts hide with the normal ones, as do
accounts whose window is spent or whose usage is missing. Hover restores them. After leaving, the strip holds for five seconds before fading back.
An open popover keeps it expanded. The eye's manual hiding still applies, tracking is unchanged,
and Reduce Motion disables the fade. When every account is routine, an ellipsis remains available
to hover or activate.

Native host tests use transparent off-screen AppKit windows and isolated fixture data. The test
command checks xUnit completion as well as exit status: a native tracking loop can end the async
runner before its tests finish.

```
make test     # Swift Testing, under Command Line Tools
python3 Tests/verify-resources.py --build-system native
python3 Tests/verify-resources.py --build-system swiftbuild  # Swift toolchains with swiftbuild
make app      # .build/Unlimited.app, ad-hoc signed
open .build/Unlimited.app
```
