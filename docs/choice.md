# Choosing a model for a task

unlimited answers one question for any caller: of the candidates the caller allows, which is the
best to use now for a task? It never knows what the task is. The caller describes it only through
generic parameters (how long it may take, how much to explore, what time is worth against quota)
and may attach its own label and metadata, which unlimited records and never reads.

Three parts, each usable on its own:

1. **Verdict** — can an account take a unit of work of a given length on a given model?
2. **Attempt log** — every use of a model, as asked and as it came out.
3. **Choice** — the candidate with the lowest expected cost, learned from the log.

## 1. Verdict

`unlimited verdict --work SECONDS [--model-scope M] [--max-age S] --json`, or
`unlimited.verdict.verdict(reading, model_scope=, now=, work=, max_age=, starts=None)`, over one
schema-1 reading:

- `unread` (no reading, not ok, stale, an expected window missing or malformed),
- `excluded` (the vendor stopped the account, a window is used up, or one runs out before the work
  would finish; with the window and when it lifts), or
- `ranked`, with a `tier` (0: no window projected past its limit; 1: one is, but after the work) and
  a `score` (tier 0: quota projected unused at reset per fraction of the window left; tier 1: hours
  until the first window runs out).

Which limits apply: those with role `session`, `weekly`, `month` or `extra` always; `weekly_model`
only when its scope is `model_scope`; a limit with no role key always (missing data is never read as
room). The scored window is a plan's monthly bucket where it enforces one, else its longest window up
to a week. A limit reported `held` is the vendor's stop, except `overage`, where the account still
runs. Ordering, tie rules and fallback are the caller's.

## 2. Attempt log

`$XDG_STATE_HOME/unlimited/decisions.jsonl` (default `~/.local/state/unlimited/`), one JSON object
per line, appended under an exclusive lock, each with a `type`:

| type | written by | fields |
|---|---|---|
| `start` | `unlimited attempt start` | `attempt` (id), `at`, `provider`, `model`, `offering` (the route's id, when not `model`), `effort`, `account`, `decision` (the choice it carries out, if any), `deadline` (seconds), `task`, `meta` |
| `end` | `unlimited attempt end ID` | `attempt`, `at`, `outcome` (`ok`, `timeout`, `error`, `unavailable`), `tokens` (`in`, `out`, `cache`), `meta` |
| `decision` | `unlimited choose` | `decision` (id), `at`, `request` (everything asked, below), `seed`, `candidates` (each scored, with its odds), `pick` |

`task` and `meta` are the caller's: a label and string key/value pairs, recorded for later analysis,
never read. No prompt or content is recorded unless a caller puts it in `meta`.

Reading rules: a line that does not parse is skipped and counted; a second `end` for one attempt is
ignored; a `start` with no `end` whose deadline has passed is a `timeout` (a caller killed mid-use
still counts); statistics are per route (provider and offering id), so `unavailable` counts
against the route that could not be reached and no other. Records older than 7 days are dropped once
the file passes 1 MB.

## 3. Choice

`unlimited choose --tier T --candidates P,... --deadline S [--quota P=ρ,...] [--exclude ID,...]
[--temperature M] [--quota-weight M] [--task LABEL] [--meta K=V]... --json`. `--exclude` rules out
routes the caller cannot use (a vendor whose account is exhausted, say).

The candidates are each named provider's live promotions at the tier, then its model at the tier,
as the catalog has them (switched-off and banned models excluded). For each, over the log's
attempts with weight `w = 2^(−age / 12 h)`:

**Failure rate**, a Beta posterior with a prior of a 10% rate worth five attempts:
`p = (0.5 + Σw·fail) / (5 + Σw·fail + Σw·ok)`. A model that failed twice in the last hour sits near
0.4, and decays back towards the prior on its own.

**Time to succeed**, log-normal, shrunk towards `log(5 min)` with three attempts' weight:
`μ = (3·log 300 + Σw·log t) / (3 + Σw)`, `T_ok = exp(μ + σ²/2)`, `σ²` pooled over every model.
**Time to fail**: the decayed mean of observed failures, with the call's `--deadline` worth one
(a hang costs the deadline).

**Quota price** of the caller's `ρ` for the candidate: `--quota ID=ρ` for a route (the projected
use at reset of the account the caller would launch it on; give every route on one account the same
value), or `--quota PROVIDER=ρ` for a provider's routes on its usual vendor:
`π(ρ) = exp(5·(ρ − 1))` — 0.03 at 0.3, 1 at the limit, 2.7 at 1.2. A live promotion costs nothing;
a candidate with no `ρ` is priced at the limit.

**Expected cost**, in minutes:

    E = (1 − p)·T_ok + p·(T_fail + T_next) + quota_weight·π − preference

`T_next` is the median `T_ok` of the other candidates (a failure costs the next attempt too), so `E`
depends on the set; the log records the whole set. `preference` is up to 1 minute for providers in
the catalog's `tie_preference`, the first worth most. `--quota-weight` defaults to 20 minutes.

**Pick.** At `--temperature 0` (the default) the lowest `E`. Above it, a sample with
`P ∝ exp(−E / temperature)`: a candidate that many minutes worse is e times less likely. Every
candidate's probability is logged, with the random seed, so another policy can be evaluated on the
same log later.

A decayed average would pick much the same with less machinery; the Beta prior is kept because it
says how far a few attempts should move a model, and because the logged `p` stays a probability.

## Cards

`unlimited cards` shows, per route (a provider's model), the vendor's published figures from the
catalog's `[[cards]]` beside the observed ones above. Card figures are not yet part of `E`: a
vendor's tokens per second is generation speed, while a use's wall clock includes whatever the
caller does between turns. Once the log records tokens for a route, the two can be compared, and a
card can seed `T_ok` for a route with no history.

## Not yet

- **Presets**: named, data-driven parameter sets a caller can ask for instead of passing each
  parameter (#102).
- **Task difficulty and model ability**: pricing a candidate by the chance it succeeds at a task of
  a given difficulty, with the cards' intelligence index as the ability prior.
- **Learning from outcomes beyond success and time**: needs a caller-reported quality signal, which
  `end --meta` can already carry.
- **Per-account and per-plan statistics**: attempts record the account; the statistics are per
  route today.
