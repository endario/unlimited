# Unlimited for the menu bar

One tile per account, showing its weekly window, drawn from `unlimited read --json`. The app reads
no credential and calls no vendor. It needs `unlimited` 0.0.21 or later in `~/.local/bin`,
`/opt/homebrew/bin` or `/usr/local/bin`.

```
make test     # Swift Testing, under Command Line Tools
make app      # .build/Unlimited.app, ad-hoc signed
open .build/Unlimited.app
```
