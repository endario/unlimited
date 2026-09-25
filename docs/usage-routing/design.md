# Usage routing

Issue #92. Where the next unit of work should run, owned by unlimited and used by the agent runner
(`agent-runner/balance.py`) and 2mw2lt (`steering/spending.py`, doc 117). Built in three phases;
each ships and is judged before the next starts.

1. **Account verdicts.** Whether an account can take a unit of work, and how well spent its quota
   would be. Deterministic. Replaces the two consumers' duplicated logic.
2. **Model choice.** Which model, priced by the accounts that can run it, for a task of continuous
   difficulty. Deterministic, no learned state.
3. **Learning.** Record decisions and outcomes; evaluate offline; only then let a learned model
   steer.

The model catalog (`catalog.toml`) keeps saying what can run; readings keep reporting account
facts; consumers keep their hard constraints (host, maker independence, complement-of, machine
availability, failover).

## Phase 1: account verdicts

### Why first

Both consumers already decide, per account, whether it can take work and which account's quota is
best spent, and they disagree: on the same OpenCode Go reading the runner scores the month and 2mw2lt
the week; the runner prefers by name within a tie, 2mw2lt by the caller's key. Every new vendor
(Command Code) or rule (the monthly bucket) has had to be made twice. Phases 2 and 3 need this
verdict as their input anyway.

### Contract (verdict only)

Phase 1 shares the **per-window evaluation**, not the ordering. Each consumer keeps its own
ordering, tie rule, fallback and account-level display: the runner's tie is an absolute score gap
and 2mw2lt's a relative one, and choosing one for both is a separate decision.

The unit judged is a **candidate: a model on an account for a duration**. A pure module,
`unlimited.verdict`, over schema-1 readings:

    verdict(reading, *, model_scope, now, work, starts=None, max_age) -> Verdict

Which limits apply:

- A limit with `role` `session`, `weekly` or `month` applies to all work on the account.
- A limit with `role` `weekly_model` applies only when its `scope` matches `model_scope` (an
  exhausted Opus week does not exclude Sonnet work).
- A limit with `role` `extra` applies (its `scope` names a pool, not a model: `gpt-reserve`, `key`).
- A limit with `role` `null` (Anthropic's severity entries, which repeat other windows) does not.
- A reading from before roles existed, or one whose wire dropped them, applies every limit: an
  absent role is never read as "no model-specific limit".

`work` is the expected duration; `starts` defaults to `now`; a reading older than `max_age` is
`unread`.

A `Verdict` is facts plus a classification, all of which a consumer may use or ignore:

- `state`: `unread` (with the reason: none, not ok, stale, an expected window missing or malformed;
  a malformed window the vendor is not expected to report is skipped and named, as doc 117 does),
  `excluded` (the binding window and when it lifts: the vendor stopped the account, a window is
  used up, or one runs out before `starts + work`), or `ranked`.
- For `ranked`: `tier` (0: no window projected past its limit; 1: one is, but after the work), the
  scored window, its projected use at reset, and its runway. The `score` is doc 117's: in tier 0
  the expiring-quota score `(1 − projected_at_reset) / fraction_of_window_left`, in tier 1 the hours
  until the first window runs out.
- Every window that bound it, by name.

`ranked` is advisory: it describes one snapshot and reserves nothing. A consumer launches the
account the verdict names, and keeps that identity through launch and retry.

The vendor-stop rule: a limit unlimited reports as `held` is the vendor's own word, since unlimited
draws no thresholds of its own. The one exception is Neuralwatt's `overage`, where the account still
runs and bills. `unlimited.verdict.stopped(limit)` says which, for any reading, cached ones included.

A CLI, `unlimited verdict --model-scope S --work SECONDS --max-age SECONDS --json`, prints one
verdict per account of the vendors asked, for the runner.

Rules carried from doc 117 (`spending.verdict`), where they are already tested:

- Every applicable live window constrains feasibility, whatever its length.
- A window that resets before the work starts constrains nothing.
- Only a vendor stop excludes by itself; a threshold hold does not.
- unlimited's projection is used where it has one; otherwise the pace so far, floored while a
  window has only just opened.

The one intended change to that evaluation: **the scored window is the plan's monthly bucket where
it enforces one, else its longest window up to a week** (owner, 2026-09-25; the runner's rule since #160). The
week still excludes the account when it would run out during the work.

### What each consumer's switch changes

Each consumer adopts the verdict in its own change, and lists every selection that changes against
its own fixtures, pinned at the commit it switches from.

- 2mw2lt keeps `order` and its tie keys; the scored window moves to the month; model-scoped limits
  apply only to their model. Its wire (`accounts.wire`) must carry `role`, `scope` and `stopped`
  first. Its published account rows stay an account-level summary until their model-specific
  meaning is settled.
- The runner keeps its ranking, its absolute tie gap and "best account stands for the provider", and
  its fallback when nothing is ranked (tier order, as today; stale, malformed and refused readings
  all allow it, and the launched account is the tier order's). What it takes from the verdict:
  exclusion by runs-out-during-work instead of the 90% cut, only vendor stops excluding, and
  model-scoped limits. Whether it also takes doc 117's score in place of its projected-use score is
  a separate change, with its own list.

### Proof

First fixture: one account with an exhausted Opus week, a usable Sonnet path, and an `extra` scoped
limit. Then each consumer's fixtures replayed as above. Each consumer keeps its own evaluator until
its switch has shipped and held.

### Concurrency

Two callers choosing at once see the same reading; nothing is reserved. A reset during the work is
covered by the runs-out check against `starts + work`.

### Versions

The module ships additively in an unlimited release; the runner checks `unlimited --version` before
calling the CLI and keeps `balance.py`'s own evaluation when it is older; 2mw2lt pins the release it
imports.

## Phase 2: model choice (research, not committed)

Each allow-listed model has an ability `θ_m` (prior: the Artificial Analysis Intelligence Index,
z-scored), a blended token price in dollars for our traffic (cache hit rate and output ratio measured
from our runs), and a speed (prior: Artificial Analysis output speed; corrected from our own run
times, since our endpoints are not the ones measured). A task has a continuous difficulty `b_t`, an
expected size, a required confidence per round kind, and a latency weight. Success is modelled as
`p = σ(a·(θ_m − b_t))`.

An account's price multiplier is the sum, over every window the work draws on, of that window's
expiring-quota price `λ_w(ρ_w)`: near zero when the quota would expire unused, rising steeply
towards the pay-as-you-go price as it nears its cap. A model's cost is its dollar price times the
multiplier of the best Phase-1-eligible account that runs it. A promotion is priced at zero but
still bound by its capacity. The choice is the cheapest model whose `p` meets the required
confidence; expected retries are charged only where a failed task is retried.

## Phase 3: learning (research, not committed)

Log every decision with a defined field list and retention rule (no task or repository content
without a decision to include it), and the probability with which each choice was made, so that
another policy can be evaluated counterfactually. A deterministic policy's log says nothing about the
choices it never made; evaluation needs exploration with known probabilities. Outcome signals: whether the author acted on a finding,
whether the next round reversed it, whether the run completed. A judge model grading sampled reviews
is opt-in, and only a model already permitted to review the repository may see it.

## Open questions

1. Phase 2: the calibration constants (IRT slope, index-to-θ mapping, the λ curve's shape, required
   confidence per round kind), set by hand from a worked example first.
