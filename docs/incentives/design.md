# Manual steering

Source: https://github.com/endario/unlimited/issues/156

## Behavior

An operator can encourage or discourage a vendor, model, offering or account with a positive finite multiplier. Divide the route's whole expected-time component by that factor; leave quota price, observations and caller preference unchanged. Apply the same arithmetic to deterministic costs and Thompson draws. Keep the fallback estimate based on raw durations.

Without `--for`, capture each affected account's latest upcoming usage-window reset. An explicit duration overrides reset expiry. Do not renew frozen timestamps as windows roll over. Refuse incomplete reset activation before writing; an unopened zero-usage window does not invalidate a known future window. Exclude auxiliary/tool counters and severity duplicates.

Overlapping scopes override rather than compound: explicit account, offering, model, provider, vendor. Offering IDs take precedence when a name also names a model. A neutral `1x` override suppresses inherited steering; clearing it restores inheritance. Account IDs are authoritative when supplied, so a reused wrapper alias cannot transfer a frozen account reset.

## Interfaces

`incentive TARGET FACTOR [--account NAME] [--for DURATION]`, with `off` to clear and omitted target to list. Persist activation groups in `incentives.json` beside the catalog, using the shared locked atomic writer. Each group has `target`, optional `account`, `multiplier`, `activated_at`, and common `until` or per-account `bindings` (`vendor`, `account`, `names`, `until`). Prune expired bindings on reads and subsequent mutations.

Pure `rank` takes optional `incentives` and `accounts` inputs. `choose` loads local policy unless supplied explicitly. Account bindings map offering IDs to an account ID/name or `{account, names}`; they describe what the caller will launch, not an account to select. Report missing context as `incentives_unresolved` without changing existing unbound ranking. Replay uses the logged policy, bindings, original decision time and seed.

Overlay policy after caching, under optional schema-1 `steering`:

```json
{"steering": {
  "settings": [{"target": "openai", "account": null, "multiplier": 10, "until": "..."}],
  "routes": [{"id": "gpt-6.1-sol", "target": "openai", "account": null, "multiplier": 10, "until": "..."}]
}}
```

`settings` includes active overridden scopes; `routes` contains effective winners, including neutral overrides. Usage facts remain readable when routing metadata fails; stderr and `steering.error` expose that failure. Ranking still refuses invalid routing configuration.

## macOS

Settings edits vendor/account scopes and custom model/route targets through the CLI. Until reset is the default; an explicit duration overrides it. Drafts remain local until Apply. Show active scopes, exact expiry, Clear, pending state and actionable errors. A successful save followed by failed refresh must be reported as saved, not as a failed mutation.

Next to the account name, show one compact vertical fast-forward glyph per direction, with stacked filled heads inside a single icon. Green up encourages, red down discourages. Reciprocal strength below 5 uses one head, 5 to below 10 two, at least 10 three; `1x` has none. All strengths use a fixed footprint. Mixed route directions retain both glyphs; help names the route, factor and expiry. The quota-room underline remains independent.

Rendering, hit testing and popover anchoring use shared variable-width geometry. Retain the old fixed-width hit-testing contract for existing callers. Preserve marks across relabeling. Fence asynchronous reads against mutations, disable competing write controls, and reconcile policy at expiry without delaying expired-mark removal.

## Scope and verification

No caller-specific policy or remote configuration propagation. Account selection and per-account learning remain outside this change. Validate persistence, alias/id round trips, independent expiries, scope precedence, quota/evidence preservation, sampling and replay with unittest; validate decoding, glyph strength, geometry and publication ordering with Swift Testing. Exercise the actual CLI with isolated state and verify rendered SwiftUI glyphs in both appearances.
