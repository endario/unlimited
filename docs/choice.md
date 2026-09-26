# Choosing a model for a task

unlimited answers one question for any caller: of the candidates the caller allows, which is the
best to use now for a task, and in what order to fall back? It never knows what the task is. The
caller describes it through generic parameters and may attach its own label and metadata, which unlimited records and
never reads. Nothing needs tuning: the choice explores by itself as far as the evidence is thin.

Three parts, each usable on its own:

1. **Verdict** — can an account take a unit of work of a given length on a given model?
2. **Attempt log** — every use of a model, as asked and as it came out.
3. **Choice** — the candidates in the order to try, by expected cost learned from the log,
   exploring routes with thin records.

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
| `end` | `unlimited attempt end ID` | `attempt`, `at`, `outcome` (`ok`, `timeout`, `error`, `unavailable`, `abandoned`), `tokens` (`in`, `out`, `cache`), `meta` |
| `decision` | `unlimited choose` | `decision` (id), `at`, `request` (everything asked, below), `policy`, `seed`, `candidates` (each scored, with its odds), `order`, `pick`, `prefer_unmatched`, `attempts_unknown` |

`task` and `meta` are the caller's: a label and string key/value pairs, recorded for later analysis,
never read. No prompt or content is recorded unless a caller puts it in `meta`.

Reading rules: a line that does not parse is skipped and counted; a second `end` for one attempt is
ignored; a `start` with no `end` whose deadline has passed is a `timeout` (a caller killed mid-use
still counts); an attempt its caller ended `abandoned` (it lost the attempt, restarting say)
counts against no route; statistics are per route (provider and offering id), so `unavailable` counts
against the route that could not be reached and no other. Records older than 7 days are dropped once
the file passes 1 MB.

## 3. Choice

`unlimited choose --tier T --candidates NAME,... --deadline S [--quota ID=ρ,...] --json` is what a
caller usually passes. It prints the decision: `candidates` scored, `order` (their indices, the order to
try) and `pick` (the first). Launch `candidates[pick]`; if it cannot run, the next in `order`;
record each use with `attempt start --decision ID` and `attempt end` so the next choice learns.

Rarely needed: `--exclude ID[=REASON],...` (routes the caller cannot use), `--prefer
NAME=MINUTES,...` (lean without ruling out), `--vendors V,...|any` (whose accounts a use may
spend), `--temperature M` and `--quota-weight M` (override how the order is made and what quota is
worth), `--task LABEL` and `--meta K=V` (the caller's own, recorded).

A name is a provider (its live routes at the tier, promotions first), a model (each of its live
routes) or an offering id (that route), as the catalog has them; switched-off and banned routes are
never candidates. `--exclude` rules out routes the caller cannot use; a reason is the caller's,
recorded and never read. Nor is a route on a vendor this machine has no account with:
`--vendors` names the vendors instead, or `any` for all of them, for a caller that launches
elsewhere.

**From Python, over the caller's own history:** `unlimited.choice.rank(cat, tier=, candidates=,
attempts=, quota=, deadline=, now=, ...)` takes the same parameters as keywords and returns the
decision (None when no candidate is live), reading and writing no file; `choose` is `rank` over unlimited's log, with the decision
appended to it.

- `attempts`: dicts as `outcomes.attempts` returns them (`provider`, `model`, `offering`, `effort`,
  `task`, `at` timezone-aware, `outcome` one of `ok`, `timeout`, `error`, `unavailable`, `secs`,
  `tokens`). Leave out an attempt that should not count; anything else is refused. The decision's
  `attempts_unknown` counts those naming no route of the catalog.
- `vendors`: None (every vendor) unless given; `choice.vendors_here(cat)` is this machine's.
- `seed`: replays a decision's order.

For each candidate, over the attempts with weight `w = 2^(−age / 12 h)`:

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
`π(ρ) = debit·exp(5·(ρ − 1))` — 0.03 at 0.3, 1 at the limit, 2.7 at 1.2 for a route of the
catalog's default `debit` of 1. A live promotion costs nothing; a candidate with no `ρ` is priced at
the limit, so between two such routes of one model the one debiting less wins.

**Expected cost**, in minutes:

    E = (1 − p)·T_ok + p·(T_fail + T_next) + quota_weight·π − preference

`T_next` is the median `T_ok` of the other candidates (a failure costs the next attempt too), so `E`
depends on the set; the log records the whole set. `preference` is up to 1 minute for providers in
the catalog's `tie_preference`, the first worth most, plus the caller's `--prefer`: minutes off a
candidate's cost (negative adds them), named as in `--candidates`, each route taking its most
specific name's value (offering id, then model, then provider). It leans without ruling out: a
caller wanting variety handicaps what it has already used, which still wins when the rest are
worse by more. unlimited never learns why. A name the catalog does not know is refused; names
matching no candidate are listed in the decision's `prefer_unmatched`. `--quota-weight` defaults to
20 minutes.

**Order.** The decision's `order` is every candidate, in the order to try; `pick` is its first;
`policy` says how it was made.

- **Default (`thompson`): exploring by itself.** Each candidate's failure rate is drawn from its
  Beta posterior, `p ~ Beta(0.5 + Σw·fail, 4.5 + Σw·ok)`, and its log time to succeed from
  `Normal(μ, σ² / (3 + Σw·ok))`; `T_fail`, `T_next`, the quota price and the preference stay at
  their values. The order is by the drawn cost (`e_drawn`), lowest first; the rest of the order is
  the same draw's ranking. A route with little history draws widely, so it is tried about as often
  as it could be the best; a route that cannot win on quota or preference whatever its speed is
  not tried; as records fill, exploration fades. How widely a thin record draws is set by the
  priors (a 10% failure rate worth five attempts, five minutes worth three), the maintainers'
  constants, not the caller's. `prob` is each candidate's share of first places over the deciding
  draw and 1000 more on the decision's seed.
- **`--temperature 0` (`best`):** by `E` at the means, lowest first, never exploring.
- **`--temperature M` above 0 (`softmax`):** sampled without replacement with `P ∝ exp(−E / M)` at
  each draw: a candidate that many minutes worse is e times less likely.

The seed is logged, so the order replays.

A decayed average would pick much the same with less machinery; the Beta prior is kept because it
says how far a few attempts should move a model, and because the logged `p` stays a probability.

## Cards

`unlimited cards` shows, per route (a provider's model), the vendor's published figures from the
catalog's `[[cards]]` beside the observed ones above. Card figures are not yet part of `E`: a
vendor's tokens per second is generation speed, while a use's wall clock includes whatever the
caller does between turns. Once the log records tokens for a route, the two can be compared, and a
card can seed `T_ok` for a route with no history.

## Not yet

- **Task difficulty and model ability**: pricing a candidate by the chance it succeeds at a task of
  a given difficulty, with the cards' intelligence index as the ability prior.
- **Learning from outcomes beyond success and time**: needs a caller-reported quality signal, which
  `end --meta` can already carry.
- **Per-account and per-plan statistics**: attempts record the account; the statistics are per
  route today.
