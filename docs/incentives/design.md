# Manual steering

## Intent

An operator can encourage or discourage a vendor, model, route or account without disabling it. Source: https://github.com/endario/unlimited/issues/156.

The owner explicitly requests multipliers on both sides of one (`10x` and `0.1x`), automatic expiry at the account's longest reset unless `--for` supplies a duration, macOS controls, and green upward/red downward triangles next to the account name, with three at `10x`. These are requirements, not speculative extensions. A persistent additive preference or explicit-duration-only implementation would not satisfy them. Multipliers scale with route time while an additive number of minutes does not.

## Behavior

A positive finite multiplier divides expected time cost, not quota cost. `10x` encourages; `0.1x` discourages; `1x` is neutral. Exhaustion and off switches retain precedence. `incentive TARGET off` removes steering, not the target.

Without `--for`, capture the latest future quota reset for each affected account when the setting is applied. Do not extend expiry when a window rolls over. An explicit duration overrides reset-derived expiry. Require an explicit duration if a needed account has no usable reset. Validate the whole operation before writing.

Self-decided calls for critic confirmation: account-specific reset expiries for broad targets; fixed expiry timestamps; refusing implicit indefinite steering; non-compounding overlap resolved by specificity rather than multiplied together.

## Shared backend contract

Extract target validation and route aliases from the existing switch implementation, and share locked atomic JSON persistence between switches and a separate `incentives.json`. Preserve the switches format. Incentive entries hold `target`, optional explicit account selector, `multiplier`, activation timestamp, and either a common `until` for `--for` or frozen account bindings (`vendor`, canonical account ID, aliases and `until`) for reset expiry. A default-reset group affects only the accounts found at activation. Validate every affected account before writing; the latest reset must be established from quota windows rather than auxiliary/tool counters.

Keep `rank()` pure with explicit `incentives` and `accounts` inputs; `accounts` maps offering ID to the account the caller will launch. CLI `choose --account ID=ACCOUNT` supplies that binding and `choose` loads local incentives. It does not select an account. A matching account-scoped or reset-bound policy requires account context. Without it, keep the route's existing ranking and expose `incentives_unresolved` in the decision and a CLI warning, rather than guessing or breaking an existing caller. Common-duration whole-route policies do not require account context. No local-policy read is added to pure rank. A replay supplies the logged policy, bindings, decision time and seed, not today's local policy.

Each state file has its own lock and atomic replacement through the shared persistence primitive; there is no operation that updates both switches and incentives and no cross-file atomicity promise. Expired groups/bindings are filtered for reads and pruned on the next mutation. Catalog route-target aliases are reused, including provider:model pairs; account aliases normalize to the resolved canonical account ID. Overlapping different targets never multiply. Within the same specificity, most recently activated wins deterministically.

Resolve overlapping policies by explicit account override, offering ID/pair, underlying model/pair, provider, then vendor. Canonical aliases naming the same scope replace rather than compound; `1x` is a meaningful neutral override and removal restores inherited steering. Apply the factor to time cost in both deterministic scoring and Thompson draws through the same arithmetic helper. Keep observed evidence, quota and preference unchanged, and log input policy/bindings plus effective factors.

Overlay live policy metadata onto readings after caching, never into vendor history. Expose each applicable setting with its affected routes and expiry, plus resolved route factors. Account-wide controls must not misrepresent a route-only setting as a global account value. For the label, summarize active non-neutral effective route factors by direction: show the strongest upward and/or downward badge, with full scope/multiplier/expiry details in help text. Opposing route settings show both directions rather than a fabricated net factor.

## macOS

Settings offers multiplier controls for accounts and whole vendors, including neutral/removal and encouragement/discouragement presets, with a custom multiplier and duration. Until reset is the default. Writes go through the CLI; failures remain visible; the refreshed CLI reading is authoritative.

The account label in the menu-bar tile and popover carries green upward triangles or red downward triangles. For strength `max(multiplier, 1 / multiplier)`: below 5 one triangle, from 5 to below 10 two, at least 10 three. Neutral shows none. This makes `10x` and `0.1x` symmetric. Exact multiplier and expiry are available as help text and in Settings; direction is conveyed by shape as well as color.

Use one shared indicator view. Widen only steered cells enough to fit the marks. Rendering, click hit testing and popover anchoring must consume the same cell geometry. Preserve the indicator when labels are customized or automatically numbered. Expired settings cease to show without requiring another vendor fetch.

## Critic disposition

Round 1 (Meta) proposed dropping multipliers, reset expiry, account scope and macOS. Rejected: these are the owner's explicit requirements, not optional scope. Accepted the compatibility concern: unbound account steering is reported rather than making existing callers fail. Separate state files require independent per-file transactions, not a cross-file transaction. Keep resets out of verdict/scoring internals: activation reads existing role-tagged readings through the CLI orchestration layer. Raw retry durations stay unmodified; the chosen route's multiplier discounts its whole expected-time component, including its fallback estimate, exactly as the requested formula specifies.

Round 2 (Meta) again proposed replacing the requested behavior with additive minutes and dropping default reset expiry. That does not meet the brief. Accepted decomposition by dependency: implement and verify the shared backend/JSON contract first, then the macOS consumer in a separate implementation slice. Both slices are part of this task; no reduced feature or feature flag. New rank keyword inputs are optional and neutral by default. Readers see either the old or new complete JSON after atomic replacement; no partial pruning is published. Matching/specificity is one shared resolver used by rank and reading annotations. Timestamp ties use persisted list order as a final deterministic tie-breaker. Pure replay uses the original decision time and frozen input, not the current time. No changes to cache, projection or verdict scoring are needed.

## Frozen contracts

The expiry resolver takes the maximum future `resets_at` across usage-window roles `session`, `weekly`, `weekly_model`, `month` and `extra` with a positive window length, excluding auxiliary/tool counters (`TIME_LIMIT`) and severity duplicates (role null). It does not select the longest duration or use verdict's scored window. A relevant unknown/past reset that prevents establishing the latest upcoming reset requires `--for`; an unopened zero-usage window does not prevent resolving an existing longer window. At least one future reset is required. `t_next` remains the median of raw observed/prior `t_ok` values, independent of multipliers and scoring order.

Read JSON stays schema 1. Add one namespaced optional field:

```json
{"steering": {
  "settings": [{"target": "openai", "account": null, "multiplier": 10, "until": "..."}],
  "routes": [{"id": "gpt-6-luna", "target": "openai", "account": null, "multiplier": 10, "until": "..."}]
}}
```

`settings` lists live settings applicable to this account, with its concrete expiry; `routes` lists effective winners per applicable offering, including neutral overrides. Both lists come from the shared resolver. Absent steering decodes as empty. Terminal labels use the same strongest-up/strongest-down summary as the widget. The menu-bar underline continues to describe quota room, not time-cost steering; its help must say so.

Global `incentive --json` lists stored activation groups (`target`, optional `account`, `multiplier`, `activated_at`, common `until` or `bindings` with account-specific `vendor`, `account`, `names`, `until`). The CLI and pure API accept explicit offering-to-account identity bindings; account ID and local alias matching uses the same binding snapshots and identity helper.

Final critic (DeepSeek): BUILD, backend contract before macOS. Accepted its pins on raw fallback time, additive schema-1 overlay, role-based reset derivation, and unchanged quota underline. The new resolver and storage primitive get tests before their CLI consumers.

## Verification

Regression tests cover multipliers on either side of one, unchanged quota penalties, exhausted routes, invalid factors, overlapping targets, independent account expiries, explicit duration, missing reset, persistence and removal, pure rank replay, JSON and terminal display.

Swift tests cover decoding older readings, indicator thresholds and expiry, relabeling, sibling isolation, CLI argument construction, and variable-width hit testing/anchoring. Build the real macOS executable. Render actual SwiftUI cells and controls in light/dark appearances; measure rendered label/indicator ink and check clipping. Exercise CLI set/read/choose/remove using an isolated state directory and fixture readings.

## Scope

Keep caller policies and scenarios out of the API. No changes to caller repositories or remote configuration propagation. No account chooser is introduced implicitly; account steering must use the existing account/projection contract or an explicit generic input seam.
