# sounding

Read AI-subscription quota usage, per window, as facts — and where each window is heading.

```
uv tool install sounding                # or: pipx install sounding
```

```
sounding                                # usage per account, for people
sounding read [--vendor V]... [--max-age SECONDS] --json
sounding capture claude-statusline      # in a Claude Code statusline script
```

Each reading gives each limit's window length, the fraction used so far, and its reset time. It
also gives whether the vendor says the limit is held, and why, and, where the vendor names it,
the account's plan. sounding reports what the vendor says, and projects it forward: it never
picks an account or draws a threshold. Choosing what to do with a reading is up to the consumer.

## For people

![sounding status: one block per account, a bar per usage window, and a forecast line under each](docs/status.svg)

## For agents

`sounding read --json` gives the same readings, one per account, for load-balancing and pacing
work across subscriptions (one limit shown):

```json
[
  {
    "schema": 1,
    "vendor": "anthropic",
    "account": "work",
    "taken_at": "2026-09-21T14:00:00+00:00",
    "source": "api",
    "plan": "default_claude_max_20x",
    "status": "ok",
    "why": null,
    "retry_until": null,
    "limits": [
      {
        "name": "seven_day",
        "window_minutes": 10080,
        "used_at_least": 0.41,
        "resets_at": "2026-09-25T11:00:00+00:00",
        "held": false,
        "held_why": null,
        "severity": null,
        "active": null,
        "kind": null,
        "projection": {
          "at_reset": [0.873, 0.9436],
          "exhausts_at": null,
          "run_out": 0.164,
          "samples": 2,
          "past_windows": 5,
          "since": "2026-09-20T14:00:00+00:00"
        }
      }
    ]
  }
]
```

Read `projection.at_reset` against `used_at_least` rather than either alone, and
`past_windows`/`samples` for how much the projection rests on.

## Sources

| Vendor | Local, no network | Network |
|---|---|---|
| Anthropic (Claude Code, claude.ai sign-in) | the `rate_limits` Claude Code hands its statusline, via `capture` | `oauth/usage` on Claude Code's own token |
| OpenAI (Codex, ChatGPT sign-in) | `rate_limits` in Codex's session logs | `wham/usage` on Codex's own token |
| Z.ai (GLM coding plan) | — | `quota/limit` on the plan's API key |
| OpenCode Go | — | `zen/go/v1/usage` on the Go API key opencode keeps |
| xAI Grok (SuperGrok, Grok CLI sign-in) | — | the Grok CLI's billing proxy on its own token |

The network endpoints are not officially documented and may change without notice. sounding
reads each harness's credential where the harness keeps it. It never refreshes a token or
writes a credential, and no token appears in its output, cache or errors.

## Projection

Each limit also carries a `projection`: where the window is heading, from the readings sounding
has taken. Within the window, two paces are extended to the reset — the average since it opened,
and a recency-weighted pace with a half-life of a fourteenth of the window, which rises with a
burst and falls in a quiet spell. Past windows of the same limit, kept for
eight windows, add the shape of use (quiet nights, busy Mondays): each one's use from this point
to its end, shifted to today's. They take over from the paces between the third and eighth window.

`at_reset` is `[low, high]`, unclamped, so `1.07` means use would pass the limit. `exhausts_at`
is when the high end reaches it, if before the reset. `run_out` is how likely use passes it, from the past windows that did, or `null`
without them. `samples`, `past_windows` and `since` say what it rests on. A projection is only as good as how often something reads: a statusline
`capture` feeds it continuously.

## Cache

There is one cache file and one `flock` per vendor under `$XDG_CACHE_HOME/sounding`, mode
`0600`. Concurrent callers share one upstream request. A 429's `Retry-After` holds off every
caller until it passes, capped at 24 hours; a 429 or 5xx holds off at least five minutes, and the
account's last good reading stands in the meantime. On macOS the keychain is readable only from the
user's GUI session or launchd, not over plain ssh.

## Status

Pre-release. The schema (`"schema": 1`) may still change.

## 2mw2lt

sounding is part of [2mw2lt](https://2mw2lt.com) — *Too Much Work, Too Little Time* — a steering
partner that coordinates work across AI workers and trusted people. 2mw2lt reads its accounts
through sounding.
