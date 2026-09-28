# Unlimited for the menu bar

One tile per account, showing its monthly window where the plan enforces one, else its weekly
window, drawn from `unlimited read --json`. A switched-off account (`unlimited off`, whole
vendor or one account) is ghosted — its exact colours at half transparency — and never the
one to use next; Settings can flip those switches per account. The app reads no credential, calls
no vendor, and writes only through the CLI (`unlimited off`/`on`). It needs `unlimited` 0.0.23 or
later in `~/.local/bin`, `/opt/homebrew/bin` or `/usr/local/bin` (0.1.1 or later for the
switching; an older CLI simply loses the greying).

```
make test     # Swift Testing, under Command Line Tools
make app      # .build/Unlimited.app, ad-hoc signed
open .build/Unlimited.app
```
