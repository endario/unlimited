# Phase 2A: choice among a tier's candidates, learned from outcomes

Issue #92, after [Phase 1](design.md). Phase 2 as sketched there (continuous task difficulty,
ability from an intelligence index, dollar prices) needs calibration data we do not have. 2A is the
part that data can already support, and it logs what 2B needs.

## What 2A decides

The runner's routed rounds today: the catalog's tier lists acceptable models; a live promotion is
tried first; otherwise `balance.py` ranks the tier's providers by projected quota use (the Phase 1
verdict excludes), with `tie_preference` taking an approximate tie. A provider that hangs is
retried on every round: Space Bunny, promoted, held rounds for their full deadline (2026-09-25),
and 2mw2lt#2391 asks for a backoff.

2A keeps the tier as the quality bar: every candidate at a tier is acceptable for that round, as
today. It replaces the ranking among them with an expected cost that includes what we observe:

- **how often a model fails** (a timeout, an error, an unusable answer) on this machine, recently;
- **how long it takes** when it succeeds, and when it fails;
- **what its quota costs**, from the Phase 1 verdict.

Quality within a tier is not modelled (2B).

## Model

For candidate `c` (a provider's model at the tier, on its best account, as today):

**Reliability.** Each attempt outcome is `ok` or `fail`. With decayed counts
`s_c = Σ w_i·[ok]`, `f_c = Σ w_i·[fail]`, `w_i = 2^(−age_i / H)`, half-life `H = 12 h`, and a
prior Beta(`a0 = 0.5`, `b0 = 4.5`) (a 10% failure rate worth 5 attempts):

    p_c = (a0 + f_c) / (a0 + b0 + f_c + s_c)

A model that failed twice in the last hour has `p ≈ 0.39`; after a quiet day the decay returns it
towards the prior on its own. This is the backoff, without a separate ban.

**Duration.** Per (provider, model), log-seconds (effort and kind are logged for 2B, not split
on yet: a day's attempts would spread too thin), of successful attempts, shrunk
towards a fixed prior with `n0 = 3` pseudo-attempts:

    μ_c = (n0·μ0 + Σ w_i·log t_i) / (n0 + Σ w_i),   T_ok(c) = exp(μ_c + σ²/2)

`σ²` pooled across all candidates; `μ0 = log(5 min)` until there is history. A failed attempt
costs `T_fail(c)`: its observed time to fail, decayed-mean, with this call's deadline as its prior
(a hang costs whatever the round's deadline is, so the prior is per call, not per candidate).

A decayed EWMA of failures and durations would route much the same with less machinery. The Beta
prior is kept because it says how much a few attempts should move a model (five attempts' worth),
where an EWMA's rate is an arbitrary constant, and because 2B's evaluation reads `p` as a
probability.

**Quota price.** From the verdict's projected use at reset `ρ` (its `at_reset` high end):

    π(ρ) = exp(κ·(ρ − 1)),  κ = 5

`ρ = 0.3 → 0.03`, `ρ = 0.8 → 0.37`, `ρ = 1 → 1`, `ρ = 1.2 → 2.7`. A live promotion has `π = 0`.
An `excluded` verdict removes the candidate, as today; an `unread` one gets `π = 1`.

**Expected cost** in minutes of wall clock, with the quota price at `Q = 20` minutes per unit:

    E_c = (1 − p_c)·T_ok(c) + p_c·(T_fail(c) + T_next) + Q·π(ρ_c)

`T_next` is the median `T_ok` of the other candidates: a failure costs the next attempt too. So a
candidate's `E` depends on the set it is in; the log records the whole set.

**Choice.** Final rounds take the lowest `E`. Finding rounds sample with
`P(c) ∝ exp(−E_c / τ)`, `τ = 2 min`, and every probability is logged, so that 2B can evaluate
another policy on these logs. `tie_preference` breaks a tie within 1 minute of expected cost
before sampling (an approximate tie, as today, in 2A's units).

## Worked example (2026-09-25 evening, standard tier, finding round)

| candidate | ρ | π | p | T_ok | T_fail | E |
|---|---|---|---|---|---|---|
| stealth (promotion) | – | 0 | 0.45 (4 failed, 1 ok) | 4 | 30 (deadline) | 2.2 + 0.45·(30 + 3.6) = 17.3 |
| glm flash | 0.93 | 0.70 | 0.1 | 3.6 | 5 | 3.2 + 0.1·(5 + 4) + 14 = 18.2 |
| deepseek flash | 0.4 (month) | 0.05 | 0.1 | 3.5 | 5 | 3.2 + 0.9 + 1 = 5.1 |
| codex luna | 1.04 | 1.22 | 0.1 | 5 | 5 | 4.5 + 0.1·(5 + 3.6) + 24.4 = 29.8 |

Deepseek takes it; Space Bunny would take it back once it stops hanging for a few hours.

## Logging

One append-only JSON-lines file per machine, `$XDG_STATE_HOME/unlimited/decisions.jsonl`, written
only by unlimited. This is unlimited's first state of its own, so its contract:

- Each record is one line, appended under an exclusive `flock`. A reader skips a line that does not
  parse (a torn last line, a hand edit) and says how many it skipped.
- Each attempt has an id; a second `end` for it is ignored.
- An attempt start with no end, older than its deadline, is read as a `timeout` (at read time: a
  runner killed mid-run still counts).
- Records older than 7 days (14 half-lives: weight below 10⁻⁴) are dropped when the file is
  compacted, which `choose` does under the lock once the file passes 1 MB.

Records:

- a **decision**: id, time, round kind, tier, host, every candidate with `ρ`, `p`, `T_ok`,
  `T_fail`, `E`, its probability, its exclusion reason if any, and the one chosen;
- an **attempt start**: decision id, provider, model, effort, account id;
- an **attempt end**: outcome (`ok`, `timeout`, `error`, `unavailable`), seconds, tokens where
  the harness reports them.

No repository, diff or prompt content. No seeding from the runner's artifacts: that format is the
runner's, not a contract, and the priors stand in until a day of attempts has accumulated.

## Interface

- `unlimited choose --tier T --kind finding|final --candidates P,... [--scope P=M] [--deadline S]
  --json` → the decision (above) with its id. Models, usage vendors, promotions and
  `tie_preference` come from the catalog; the caller names only the providers its own constraints
  allow (host, maker independence, complement-of).
- `unlimited attempt start|end ...` records attempts against it.
- `unlimited outcomes [--json]` prints the per-candidate `p`, `T_ok`, `T_fail` it would use now.

The runner's `balance_candidates` calls `choose`, and falls back to `balance.py` on **any** failure
of it (an older unlimited, a crash, an unwritable log), so 2A never makes routing less available
than today. It records each attempt around the adviser run, hops included. The promotion is a
candidate instead of being tried first. 2mw2lt adopts later; until then the two order differently,
as Phase 1 already accepts.

## Delivery

1. Attempts and the log: `unlimited attempt start|end`, `unlimited outcomes`; the runner records
   attempts around `balance.py`'s existing choice. No routing change.
2. `unlimited choose` and the runner's switch to it.

Both ship the same day; the first starts the history the second reads.

## Proof

- Replaying the seeded history: the decisions `choose` would have made on the recorded rounds,
  against what `balance.py` made.
- Unit tests of each formula at its named points (the example table's rows).
- Live: a day of runner rounds on this machine and M1, then `unlimited outcomes`.

## Medium calls I made (for the critic)

1. Promotions lose "tried first" and become `π = 0` candidates. The owner's rule for promotions is
   "no special handling"; a free model that hangs should lose.
2. Exploration only in finding rounds; final rounds stay deterministic.
3. Per-machine logs; no sync in 2A.
4. The constants (`H`, prior, `κ`, `Q`, `τ`) are set by hand from the example above.
