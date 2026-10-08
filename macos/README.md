# Unlimited for the menu bar

One tile per account, showing its monthly window where the plan enforces one, else its weekly
window, drawn from `unlimited read --json`. A switched-off account (`unlimited off`, whole
vendor or one account) is ghosted — its exact colours at half transparency — and never the
one to use next; Settings can flip those switches per account. The app reads no credential, calls
no vendor, and writes only through the CLI (`unlimited off`/`on`/`incentive`). It needs `unlimited` 0.0.23 or
later in `~/.local/bin`, `/opt/homebrew/bin` or `/usr/local/bin` (0.1.1 or later for the
switching; an older CLI simply loses the greying).

Unavailable or stale usage displays `?`; the popover explains the reported failure. For Claude
sign-in failures, **Sign in** opens iTerm and runs the account's executable wrapper from
`~/.local/bin` with `auth login --claudeai`. Choose the correct account in the browser, then
refresh. Without a matching wrapper, sign in through that account's Claude Code setup.

Settings keeps each account's controls together: the eye shows or hides its menu-bar tile;
ban prevents offering it and fades the row. Neither stops tracking. The multiplier button opens
an account override editor with a multiplier, Until reset, and a duration picker when reset is
off. Apply writes the override; Clear override removes that scope without clearing inherited
policies. Right-click a picker for custom values. Other policies lists existing vendor/model/route
policies and switches for accounts not read here; broader policies can be created through the CLI.
Account/reset steering requires caller account bindings (`choose --account OFFERING=ACCOUNT`, or
`rank(accounts=...)`).

Auto-hide normal accounts is enabled by default in General. White, readable accounts without
warnings, bans or steering disappear from the compact strip; hover restores them. After leaving,
the strip holds for five seconds before fading back. An open popover keeps it expanded. The eye's
manual hiding still applies, tracking is unchanged, and Reduce Motion disables the fade. When all
visible accounts are normal, an ellipsis remains available to hover or activate.

With unlimited 0.1.10, Settings can encourage or discourage vendors, accounts, models and routes.
Until reset is the default; an explicit duration overrides it. A green upward or red downward
vertical fast-forward glyph beside the account name has single, double or triple stacked heads:
`10x` and `0.1x` use triple heads; `1x` has no glyph. Help gives the route, multiplier and expiry.
Clear removes the selected scope, so an inherited setting may apply again. Steering is local to
this machine, and the quota-room underline is independent of it.

```
make test     # Swift Testing, under Command Line Tools
python3 Tests/verify-resources.py --build-system native
python3 Tests/verify-resources.py --build-system swiftbuild  # Swift toolchains with swiftbuild
make app      # .build/Unlimited.app, ad-hoc signed
open .build/Unlimited.app
```
