# Woyo rolling logged usage

**Progress:** A is implemented and locally verified; native display and personal limits remain separate deliveries. A has not been released yet.

## Goal and scope

Show locally accumulated gateway-log tokens over rolling seven and thirty days in the CLI and macOS app. Collection is **app/CLI reads only**: no daemon or new collection command. No fabricated quota, reset, availability promise or selection-policy change.

The owner also requested optional `.env` thresholds **purely for self pacing**. These are personal display settings, not a caller's task budgets or launch policy. Deliver them separately after measured usage, without an artificial week-long waiting requirement.

## Source evidence

The endpoint is `GET $FY_API_BASE_URL/api/log/token`. Plain HTTP exposes the bearer key and allows response tampering; this transport remains explicitly owner-authorized.

A controlled request verified stable `request_id` but changing numeric `id`. The tagged upstream's `assignDisplayLogIds` assigns page positions to numeric IDs. The key endpoint is a bounded recent tail without key-authorized pagination. Upstream `MaxRecentItems` defaults to 1000, not a verified deployment limit. Sources: [controller/log.go](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.37/controller/log.go), [model/log.go](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.37/model/log.go), [constants](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.37/common/constants.go).

The checked producer inserts consumption logs rather than updating them. Use insert-once accumulation, not correction tracking. Request IDs are not a unique database index: error and consumption rows may share one ID. Errors may anchor continuity but do not compete with consumption. Repeated consumption identities within one page are ambiguous; skip those new events, retain any previously recorded event, and mark uncertainty.

[service/text_quota.go](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.37/service/text_quota.go) sets `usage_semantic: anthropic` on the Anthropic logging branch and uses text metadata otherwise. Normalize as Anthropic when `usage_semantic == "anthropic"` or the older `claude == true` marker is present; otherwise OpenAI. Anthropic input is prompt + cache read + cache creation; OpenAI input is prompt alone. Live GLM counts confirmed separately reported cached input. Count consumption (`type: 2`) only. [Replay fixture](log-replay.json) has synthetic identities/timestamps and checked consumption counts; its collision row is a regression scenario.

User-scoped time-range APIs require a separate dashboard credential and user ID, which were not supplied; they can also include other keys' use. Do not add another credential surface or change scope to account-wide usage. Use the supplied inference key.

## A: tracker and CLI

### Source boundary

Reuse `Credential`, `EnvKeys`, transport and vendor caching. Register vendor `woyo`, displayed as Woyo (native strip code WY). This tracker does not add model offerings. Discover `FY_API_KEY` from the environment and `.config/*.env`; matching the credential variable rather than a launcher filename keeps discovery independent of its callers. Only matching files contribute names. No caller project configuration is read.

`read(cred, now, get)` stays stateless: it returns a successful reading for a valid log sample, including empty samples without quota, plus a private allowlisted snapshot. Hash request IDs in that snapshot. Retain only identity, time and numerical accounting fields, never model names, prompt/response text, IP, username, credentials or full metadata objects.

One optional adapter hook in `cache.through` imports successful snapshots, strips private fields before cache/output, and attaches current aggregates for every returned reading. Run it inside the existing vendor lock before cache writing. Other adapters are unchanged. Direct adapter calls are parsing/source operations, not durable collection; public reads use the cache transaction.

Keep the existing failure policy: transient faults retain the prior reading's `taken_at`; throttles/server faults also retain a backoff deadline; auth refusal replaces the reading. Durable totals survive all of them and display beside stale/refusal/backoff status. Do not add a vendor-specific second failure ledger.

### Durable state

One concrete store, no extensible import framework. Reuse `state.flock` and `state.write_json`, extracting the existing state-directory convention from `outcomes` for its second caller. Store private, versioned JSON under `XDG_STATE_HOME/unlimited/usage`, outside the disposable cache and caller-attempt ledger. Never sum these events with caller-reported attempt tokens. Lock order is cache then usage; the usage lock covers read/merge/prune/write, not HTTP.

The event map contains consumption records only, keyed by vendor/account/hashed request ID with first-write-wins. Error identities enter a separate page-anchor set, never the event map, so they cannot suppress consumption. Retain the thirty-day event horizon plus bounded last-page anchors. Unsupported versions/corrupt state remain untouched and produce unknown totals with a fixed diagnostic, never silently reset zero.

A synthetic JSON write/read/aggregate/atomic-rewrite probe with 50,000 events produced 10,668,056 bytes and took 0.0895 seconds locally. Keep JSON initially; repeat with the implemented representation before release. This is not a throughput guarantee.

### Coverage

Maintain merged monitored intervals separately from event counts:

| Capture | Transition |
|---|---|
| First success, including empty | Establish the observation point. Earlier backfill remains partial history. |
| Overlap with last successful page identities | Bridge the interval between successful captures under bounded-tail retention assumptions. |
| Two successive genuinely empty pages | Bridge the interval under the same assumptions. Empty means zero **logged** usage, not zero actual use. |
| Disjoint pages or empty/nonempty transition | Leave the interval unmonitored; mark `tail-gap`. Do not infer deployment capacity from sample size. |
| Failed poll | Preserve prior successful anchors/events. A later overlapping success may bridge the missed poll. |
| Malformed row or ambiguous consumption identity | Retain prior valid facts; record uncertainty at a parseable event timestamp, otherwise across the capture's thirty-day horizon. No identity issue-clearing state machine. |
| Cache hit | Reaggregate/expire, but extend no monitored interval. The suffix from last collection to `as_of` remains unobserved. |

Compute each window over `[now - duration, now)` in UTC. No successful sample means `unknown`; uncovered intervals/intersecting uncertainty mean `partial`; covered intervals through the end mean `observed`. Old gaps expire by window intersection. Use the closed period reason set `horizon-edge`, `tail-gap`, and `unparsed`; state failures use the separate diagnostic. Overlap is collection evidence, not an authoritative billing watermark. Logging can be disabled upstream; labels always say logged/observed tokens. Detecting log silence by comparing another activity source is deferred; the supplied log alone cannot establish actual use.

### Public schema

Keep schema 1 and add optional `usage`, separate from `limits`/`credits`:

- `source`: `gateway-log`; `as_of`; nullable `collected_at`; optional fixed `diagnostic`.
- `periods`: `window_minutes` (10080 or 43200), `starts_at`, `ends_at`, nullable `input_tokens`, `output_tokens`, `total_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `coverage` (`unknown`, `partial`, `observed`), and reason codes.

No successful sample gives null totals; successful empty gives observed zero with partial coverage until monitoring covers its horizon. Missing cache counts mean zero under the checked producer's convention; malformed counts are not imputed. CLI shows both rolling periods, token units, input/output detail, collection age and coverage, including retained facts on refused readings. Verdict/choice/projection ignore measured usage.

## B: macOS display

Separate macOS-only delivery after A. Decode the additive object; older readings still work. Keep the CLI boundary and current refresh/wake timer. No native credentials or vendor HTTP.

The strip shows compact seven-day tokens with units/period, not an ambiguous percentage. Settings and popover show both periods and provenance/coverage. Reuse account containers and horizontal geometry. Keep token figures separate from quota cards, health and best-account selection. Render retained totals independently of latest fetch status/quota freshness, with stale/refusal status visible. Measured accounts are not automatically hidden as routine quota accounts. No chart or new palette.

Verify rendered ink/baselines, clipping and click geometry for strip, Settings and popover, including long values and mixed account types.

## C: local self-pacing display

The requested optional settings are `UNLIMITED_WEEKLY_TOKEN_LIMIT` and `UNLIMITED_MONTHLY_TOKEN_LIMIT`: rolling seven-/thirty-day input-plus-output budgets including cached input once. Display these as rolling 7d/30d, not calendar months. No actual values have been chosen; do not install example limits.

Resolve each period independently. Empty/unset contributes no budget; explicit values must be positive integers. Same-key files with agreeing values share a limit; conflicting or invalid explicit values yield a local diagnostic and no denominator. Never print raw invalid values. Re-read settings after cache reads, like account names. File-discovered credentials use their current files; environment-only credentials may use environment settings.

Periods may gain optional `local_budget`: `status` (`ok`, `invalid`, `conflict`), nullable `token_limit`, profile labels (no full paths/raw values). CLI/native progress is explicitly local and qualifies partial history. Never feed budgets into limits, credits, resets, forecasts, verdicts, choice or incentives.

The gateway key is one pool, but local observation histories are not synchronized. Machines may display different totals against the same limit. A partial total is not proof the budget has room.

## Acceptance and delivery

A: parser semantics, error/consumption collisions, duplicate snapshots, concurrency, UTC expiry, overlap/gaps/empty samples, malformed data, damaged state, cache deletion, refusal/backoff, privacy and permissions. Full Python suite plus real read/dedup/restart checks.

B: old/new decoding, retained totals, neutral token health, no quota selection changes, both periods, rendered measurements and full native suites.

C: cache-hit edits, duplicate-file agreement/conflict, invalid values without leakage, no selection effects, CLI/native budget display.

Ship each Python-changing delivery through the project release process; install the CLI/app. No collector is installed. Other vendors' token sources, account funding/rate limits and transport improvements remain outside this work.
