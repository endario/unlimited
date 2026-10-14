# Rolling logged usage implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans task-by-task. Checkboxes track completion.

**Goal:** Deliver rolling logged token totals, native display, then optional personal pacing limits.

**Architecture:** A stateless Woyo parser supplies a private normalized snapshot. The existing vendor cache transaction imports it into a separate durable JSON store and attaches rolling facts. Native rendering and personal limits consume the additive contract in later deliveries.

**Tech stack:** Python stdlib/unittest; Swift/SwiftUI/AppKit/Swift Testing. No dependencies or daemon.

**Spec:** [design.md](design.md).

## Global constraints

- Use this isolated worktree; do not change the shared checkout.
- Collection is app/CLI reads only. Keep schema 1, existing quota failure/backoff policy and selection behavior.
- Vendor `woyo`, human name Woyo, native code WY. No model offerings or caller policies.
- HTTP is owner-authorized; never print credentials, raw logs or invalid configuration values.
- Durable private state is outside the fetch cache. Count consumption only; error identities are a separate anchor set.
- UTC windows are `[now - duration, now)` for 10080/43200 minutes.
- No actual personal limits have been selected.

## Verification focus

- A consumption/error identity collision must retain the consumption regardless of row order (Task 2).
- Deleting the cache or refusing authentication must retain durable events (Tasks 1–2).
- Cached aggregation must expire events without extending capture coverage (Task 1).
- A token tile must not acquire a percent sign, quota warning, best-account mark or automatic hiding (Task 3).
- An invalid local value that contains secret-like text must never reach output or disk (Task 4).

## File boundaries

A store: create `src/unlimited/usage.py`, `tests/test_usage.py`; modify `state.py` and `outcomes.py` only for shared state-directory extraction.

A source/CLI: create `src/unlimited/adapters/woyo.py`, `tests/test_woyo.py`; modify `adapters/__init__.py`, `cache.py`, `show.py`, and relevant CLI/help/render tests and README.

B native: `Reading.swift`, new `LoggedUsage.swift`, `Tile.swift`, `Steering.swift`, `StripView.swift`, `AccountRow.swift`, `PopoverView.swift`, and native tests. Reuse account containers and strip geometry.

C limits: new `src/unlimited/local_budgets.py` and `tests/test_local_budgets.py`, cache return decoration, CLI/native display and their tests.

## Task 1: durable observations (delivery A)

**Produces:** `state.default_dir() -> Path`; `usage.attach(r: dict, now: datetime) -> dict`; `usage.path(vendor: str, account: str) -> Path`.

**Private snapshot contract:** reading key `_usage_snapshot`, with `events` (list), `anchors` (hashed-ID list), `empty` (actual source page emptiness), and `unparsed` (list of uncertainty intervals). Each event has `id`, `at` (aware ISO), and nonnegative integer `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`. An uncertainty interval has `starts_at`, `ends_at`. The parser never supplies raw IDs.

**Public contract:** `usage` contains `source: gateway-log`, `as_of`, nullable `collected_at`, optional `diagnostic`, and `periods`. Each period has the window/dates, nullable token counts (including derived total), `coverage`, and `reasons`. Reasons are `horizon-edge`, `tail-gap`, `unparsed`.

- [ ] Write a consumer-facing failing test before adding the module. Example fixture/expectation:

```python
from datetime import datetime, timedelta, timezone
from unlimited import usage
from unlimited.schema import reading, iso

now = datetime(2026, 10, 14, tzinfo=timezone.utc)
event = dict(id="hashed-a", at=iso(now - timedelta(hours=1)),
             input_tokens=2851, output_tokens=85,
             cache_read_tokens=1088, cache_creation_tokens=0)
snapshot = dict(events=[event], anchors=["hashed-a"], empty=False, unparsed=[])
r = dict(reading("woyo", "fixture", now, "ok"), _usage_snapshot=snapshot)
first = usage.attach(r, now)
second = usage.attach(r, now)
assert [p["total_tokens"] for p in second["usage"]["periods"]] == [2936, 2936]
assert "_usage_snapshot" not in first
assert first["usage"]["periods"][0]["coverage"] == "partial"
```

- [ ] Run `PYTHONPATH=src:tests python3 -m unittest tests/test_usage.py` using the repository's importable test command (`python3 -m unittest discover -s tests -p test_usage.py` if needed); record the expected missing-feature failure.
- [ ] Add the concrete store, private versioned JSON and shared state directory. Use `flock(path)` around load/import/prune/write. Whitelist event fields. First-write-wins, separate anchors, merged monitored intervals and uncertainty intervals. Reject corrupt/unsupported state without replacing it.

```python
# Shared path convention, preserving outcomes' existing location:
def default_dir():
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "unlimited"
# outcomes.path(): return default_dir() / "decisions.jsonl"
```

- [ ] Add failing cases, then minimal fixes: repeated/reordered snapshots; first-write-wins; overlapping/disjoint/empty pages; failed poll then overlap; UTC lower/upper boundaries; cache-only expiry; expired gaps; ambiguous/malformed row uncertainty; unread/corrupt/future-version state; private file/lock permissions; concurrent imports with disjoint events.
- [ ] Assert unknown totals before any capture, zero logged totals only after a valid empty sample, and no coverage extension on cache hits. Assert literal token totals, not values calculated by the implementation's helpers.
- [ ] Run the focused tests and full Python suite. Do not commit shared files while the source task is editing them; final A commit follows integration.

## Task 2: stateless source and CLI (delivery A)

**Consumes:** Task 1's snapshot contract and `usage.attach(r, now)`.

**Produces:** `woyo.VENDOR`, `woyo.URL`, `woyo.KEYS`, `discover`, `names`, `read(cred, now, get)`, and `attach = usage.attach`. Register the module in `REGISTRY`.

- [ ] Add a failing parser/source test with a specific transport double:

```python
from unlimited.adapters import woyo
from unlimited.credential import Credential
from unlimited.transport import Answer

def get(url, headers, now):
    assert url == woyo.URL
    assert headers == {"Authorization": "Bearer fixture-key"}
    return Answer(replay, 200, None)

r = woyo.read(Credential("fixture", {"key": "fixture-key"}), now, get)
assert r["status"] == "ok"
assert r["limits"] == []
assert sorted(e["input_tokens"] for e in r["_usage_snapshot"]["events"]) == [23, 2851]
assert sorted(e["output_tokens"] for e in r["_usage_snapshot"]["events"]) == [8, 85]
```

Load `docs/woyo-usage/log-replay.json` as `replay`; reverse its rows to prove error/consumption order independence. Source calls must not create durable state.

- [ ] Verify the failure; implement the pure allowlisted parser. Anthropic marker/older true flag adds cache once, otherwise OpenAI prompt already includes it. Validate integer counts and timestamps; malformed metadata/rows become uncertainty, not zero. Consumption duplicates within a page are skipped as ambiguous, while error identities remain anchors.
- [ ] Add the optional cache hook before projection/cache writing, still under the vendor lock:

```python
attach = getattr(adapter, "attach", None)
if attach is not None:
    out = [attach(r, clock()) for r in out]
```

Ensure private snapshots are stripped before serialization. Cached readings lack snapshots and only reaggregate. Preserve the existing cache refusal/backoff rules.
- [ ] Add failing integration cases for environment/profile discovery, same-key dedup, cache deletion/restart, cache-hit expiry without HTTP, authentication refusal retaining usage, transient/backoff behavior, concurrent coalesced fetches, secret/raw-body leakage and unchanged verdict/projection results.
- [ ] Add failing text-render tests for observed rolling 7d/30d token units, input/output detail, partial/unknown/zero distinctions, collection age and retained totals alongside refusal. Implement a shared measured-usage renderer called on successful and refused readings; do not substitute a quota bar/reset.
- [ ] Run `PYTHONPATH=src:tests python3 -m unittest discover -s tests`. Run real read-only Woyo reads, repeat/restart/cache-clear checks using private credentials without printing raw responses. Repeat the persistence volume probe against actual state shape.
- [ ] Update README and design status, then commit/publish/review/merge A and release/install Python per project instructions. Keep B/C out of A's diff. Attribution must use the current session's reported runtime/model.

## Task 3: measured native display (delivery B)

**Consumes:** A's `usage` schema. **Produces:** optional `Reading.usage`, neutral logged-token tile value and reusable measured-period view.

- [ ] Add failing Swift decoding/tile tests using literal A-shaped JSON: total 2936 for 7d and 30d, `coverage: partial`, no quota, and both successful/refused top-level statuses. Older JSON without `usage` must still decode.

```swift
let readings = try Reading.decode(Data(fixture.utf8))
let tiles = Tile.strip(readings, now: now)
#expect(readings[0].usage?.periods[0].totalTokens == 2936)
#expect(!tiles[0].best)
#expect(tiles[0].health == .normal)
#expect(!tiles[0].isRoutine)
```

- [ ] Run `make -C macos test` and verify the new tests fail, then add DTO decoding and token presentation. Keep counts separate from `Limit`; do not append `%` to token values or let them enter quota picks/health. Retained facts display even when latest reading is refused/stale.
- [ ] Add failing layout/render tests for mixed percent/token tiles, long token values, both rolling labels, auto-hide, and strip click/popover anchoring. Extend existing `StripLayout.width(of:)`, not a second geometry implementation.
- [ ] Add a reusable logged-period detail view used by account Settings and popover. Keep measured sections separate from quota bars; show collection age, observed-source and coverage explanations.
- [ ] Render AppKit bitmaps/screenshots for strip, Settings and popover. Compute ink extents/baselines, assert no clipping and report measurements. Use baseline alignment for mixed fonts; do not assert layout coordinates as visual proof.
- [ ] Run full native suites/build, update docs, review/merge this macOS-only delivery, and install the new app. No Python release solely for B.

## Task 4: personal pacing limits (delivery C)

**Consumes:** measured periods and the source's `EnvKeys` metadata. **Produces:** `local_budgets.attach(r: dict, keys: EnvKeys) -> dict`; optional period `local_budget` (`status`, nullable `token_limit`, `profiles`).

- [ ] Write failing real-file tests under isolated HOME/state/config roots: unset/empty limits omitted; agreeing same-key files resolve; weekly/monthly resolve independently; conflicts/invalid explicit values have no denominator; invalid secret-like text never appears in output; environment-only credentials can use environment values while file-discovered credentials use live files.

```python
f.write_text("FY_API_KEY=fixture-key\nUNLIMITED_WEEKLY_TOKEN_LIMIT=10000\n")
r = local_budgets.attach(measured, keys)
assert r["usage"]["periods"][0]["local_budget"]["token_limit"] == 10000
f.write_text("FY_API_KEY=fixture-key\nUNLIMITED_WEEKLY_TOKEN_LIMIT=private-invalid-value\n")
r = local_budgets.attach(measured, keys)
assert r["usage"]["periods"][0]["local_budget"]["status"] == "invalid"
assert "private-invalid-value" not in json.dumps(r)
```

- [ ] Verify failure and implement positive-integer parsing/resolution without echoing raw values. Overlay after cache reads/names, not in durable usage or cached provider data. Add cache-hit edit tests proving no extra network call and unchanged provider facts.
- [ ] Add failing CLI/native tests for explicitly local limit/progress, partial-history qualifications, conflicts/invalid diagnostics, values above the limit and absent budgets. Implement textual progress in the measured-period views, no provider warning/selection effect.
- [ ] Assert quota verdicts, choice/projection and native best-account/health results are identical with and without personal limits.
- [ ] Run both full suites and visual measurement checks. Update docs, review/merge/release/install. Do not write limits to the user's profile without actual selected values.

## Execution choice

Tasks 1 and 2 have a fixed primitive snapshot boundary and disjoint files, so implement them in parallel and verify A together. Keep cache/source/CLI edits with Task 2. B/C follow A's contract in their own deliveries. The orchestrator owns integration, actual gateway checks, review and releases.
