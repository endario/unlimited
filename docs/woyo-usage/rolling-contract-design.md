# Rolling local-budget account progress

## Goal and scope

Automatic account-selection consumers must be able to read the same rolling weekly USD progress as the native account display. Keep provider `limits` and provider-verdict semantics unchanged. A self-declared budget is not a vendor allowance, invoice, reset schedule, or provider refusal.

This design replaces the proposed local-limit and mixed-verdict approaches. First deliver an additive, typed budget-progress side field. Consumer adoption is a dependent stage in the owning repositories; do not claim compatibility complete before their wire and evaluator tests pass.

## Existing facts and ownership

`usage.periods[].estimated_cost` already contains USD, budget USD, pricing status, and unpriced-request count. The measured period supplies its rolling duration, timestamps, and coverage. Profile lookup/repricing stays on the existing read path in `local_budgets.attach`; the private event ledger remains in `usage.py`. The pure verdict function does not open profiles, collect events, price models, or read clocks.

Token-only pacing remains display-only. The requested USD progress is a cross-model comparable market-value measure. No new token-budget selection policy is introduced.

## Additive contract

Introduce a pure `budget_progress(reading)` normalizer over the already-decorated reading, colocated with the existing local-budget code. It performs no I/O. `local_budgets.attach` adds its output as reading `budget` after provider-cache writing; CLI verdict-row composition forwards that object inside the provider result. The pure provider `verdict()` function remains unchanged.

```json
{
  "state": "unread",
  "reason": "no-window",
  "budget": {
    "source": "local-market-value",
    "window_type": "rolling",
    "window_minutes": 10080,
    "state": "partial",
    "used": 0.0015686397,
    "usd": 0.31372794,
    "budget_usd": 200.0,
    "coverage": "partial",
    "unpriced_requests": 0
  }
}
```

The outer verdict is the existing provider result, unchanged. Existing consumers ignore `budget`; no ranked verdict loses its required at-reset fields. No generated entries enter `limits`, `credits`, reset projection/history, or provider cache.

Budget states:

- `ok`: valid positive finite USD budget, fully priced observations, observed coverage, and fraction below one.
- `exceeded`: a valid known lower-bound fraction at or above one, including partial history. This reports crossing the declared budget, not provider unavailability.
- `partial`: incomplete coverage or unpriced/unknown usage below the budget; retain the nullable lower-bound fraction and diagnostic counts.
- `invalid` / `conflict`: corresponding configuration or malformed-budget facts, without raw configuration values.
- No configured USD budget: omit the side field, preserving existing output.

Do not add a rolling score, provider exclusion, reset/expiry date, probability, or forecast in this first slice. The same declared threshold can be consumed by multiple callers without each reconstructing pricing/completeness logic.

## Presentation

Keep the current weekly percentage and total-token row, using existing shared card/figure rendering. Label it `Rolling weekly`. Partial progress remains a lower bound in the tooltip. Provider best-account recommendations do not acquire a local-budget candidate. Raw tokens remain the fallback when no usable budget denominator exists.

## Dependent consumer adoption

Intermediate account wires must retain allowlisted measured usage/cost fields or this normalized budget-progress object. The provider evaluation remains authoritative for provider refusal/staleness and unchanged for resetting limits. Budget adoption is explicit in consumer code, not a reinterpretation of provider quota fields:

- `exceeded` can defer new work against the owner's declared budget; reevaluate fresh advice on the existing read cadence, not by a fabricated reset or permanent-ban expiry.
- `partial`, `invalid`, and `conflict` remain visibly unknown/unreliable. Preserve the caller's already-defined unknown-usage eligibility; do not silently reinterpret them as spare capacity.
- Any future current-headroom ranking must be a separately verified consumer change. Do not compare a lower bound with a predicted at-reset percentage by substituting one field for the other.

Consumers currently using identity-only placeholders must forward real readings before budget progress can appear. A new machine-native source adapter is deferred unless it is the active path. Shared pricing/ledger ownership is unchanged; no consumer-local replicas.

## Verification and delivery

- Pure helper: complete, partial, unpriced, unknown, invalid/conflicting, zero and boundary/over-budget inputs; finite numbers and no raw-value leakage.
- Provider verdict regression: removing only the additive budget field yields the same result as before, including off/refused/stale/excluded/ranked cases.
- Cache-hit hot repricing and budget edits; unchanged collection timestamps; no budget side field in provider cache or projection history.
- CLI/provider output remains backward-compatible; native label and measured pixel alignment retain the compact layout.
- Install the local preview and inspect real read/verdict output.
- Consumer-owner wire and evaluator fixtures before claiming the account is integrated there.

Rolling forecasting and combined ranking are deferred. This decomposition adds useful structured progress now without inventing a reset-shaped budget verdict or forcing a cross-repository flag day.
