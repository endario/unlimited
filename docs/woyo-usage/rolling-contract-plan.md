# Rolling budget progress and Woyo routes implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox syntax. Parent owns integration and CLI; route parsing may run as a disjoint implementation slice.

**Goal:** Publish truthful rolling USD-budget progress without changing provider quota advice, and publish Woyo's vendor model list with the existing routes contract.

**Architecture:** `local_budgets.attach` remains the live profile/cost decorator. A pure normalizer exposes budget progress; CLI composition adds it beside the unchanged provider verdict. Woyo's `models` hook reuses route caching, and the CLI preserves a root vendor filter.

**Tech Stack:** Python stdlib/unittest; SwiftUI/Swift Testing.

**Spec:** [rolling-contract-design.md](rolling-contract-design.md). Route requirements are recorded in issue 219; actual streaming behavior was probed before selecting dispatchable IDs.

## Global Constraints

- Provider `limits` and provider-verdict semantics stay unchanged.
- No generated local quota, reset, score, forecast, collector, protocol framework, or pricing/ledger replica.
- Local pricing/settings stay outside provider cache and are reread on cache hits.
- Preserve raw token totals, partial coverage, sanitized diagnostics and unknown-usage fallback.
- Publish gateway IDs verbatim; do not infer maker identity from aliases.
- Keep changes in the current isolated worktree; parent owns commits and verification.

## Review Focus

- Budget arithmetic must not convert unknown or incomplete data into complete headroom: pin state precedence and nullable fraction.
- A partial lower bound crossing the budget is meaningful, but not provider refusal: test exceeded side data with unchanged provider verdict.
- Invalid/bool/nonfinite fields must not raise or echo raw config: pure-helper table tests.
- Root and subcommand vendor positions must select only the named route source: CLI tests both forms.
- Listed, unverified aliases must not become automatic routes: Woyo model tests check verified served IDs true and unverified IDs false. Intermittent failures remain documented rather than being a permanent capability block.

---

## Task 1: Pure budget progress and additive composition

**Files:** `src/unlimited/local_budgets.py`, `src/unlimited/cli.py`, `tests/test_local_budgets.py`, `tests/test_verdict.py`; README configuration/API notes.

**Interface:** `budget_progress(reading: object) -> dict | None`. Input is the already-decorated schema-1 reading. Output uses the spec's `source`, `window_type`, `window_minutes`, `state`, `used` fraction, USD amounts, coverage and unpriced count. It opens no profiles or ledger.

- [ ] Add a helper test with this concrete input:

```python
r = {"usage": {"source": "gateway-log", "periods": [
    {"window_minutes": 10080, "coverage": "observed", "estimated_cost":
     {"status": "ok", "usd": 50.0, "budget_usd": 200.0, "unpriced_requests": 0}}
]}}
p = local_budgets.budget_progress(r)
self.assertEqual((p["state"], p["used"]), ("ok", 0.25))
```

- [ ] Add table variants: USD 0 → ok/0; 200 and 250 → exceeded/1 and 1.25; coverage partial + USD50 → partial/.25; coverage partial + USD250 → exceeded/1.25; unpriced1 + USD50 → partial; unknown USD → partial/None; configuration invalid/conflict → corresponding sanitized states; no USD budget → None. Assert malformed bool, negative and nonfinite amounts cannot yield a fraction. Patch profile/ledger functions to raise during helper calls, proving no I/O.
- [ ] Run `PYTHONPATH=src:tests python3 -m unittest test_local_budgets` and confirm the new helper test fails before implementation.
- [ ] Implement the helper using existing `schema.number` for finite JSON numbers and the existing period/cost fields. Validate the positive budget and nonnegative USD; never fabricate zero for unknown USD. Use `exceeded` only for a valid lower bound >=1; require observed coverage, status ok, and zero unpriced requests for state ok. Sanitize unknown coverage to unknown and invalid fields to invalid. Return None when no USD budget is configured.
- [ ] In `local_budgets.attach`, build the repriced reading, remove any stale root budget overlay, then attach `budget_progress(out)` if present. Provider cache writing remains before this decoration. Library `read()` therefore exposes the normalized side object.
- [ ] In CLI verdict-row composition, compute the existing provider result and copy the reading's normalized budget into `verdict.budget` when present. Do not modify `verdict.verdict`, `limits`, projection, choice, or incentives.

```python
result = verdict(r, model_scope=a.model_scope, now=now,
                 work=work, max_age=max_age, off=off, policy=policy)
if isinstance(r.get("budget"), dict):
    result = dict(result, budget=r["budget"])
```

- [ ] Add CLI integration tests: with a decorated budget, stripping only `budget` from the resulting verdict equals the existing provider result; without a configured USD budget the output is unchanged. Cover unread, ranked, excluded, stale and off provider results. Add cache-hit removal/repricing tests for the root overlay.
- [ ] Run focused suites then full Python unittest discovery.

## Task 2: Woyo model source and root vendor filter

**Files:** `src/unlimited/adapters/woyo.py`, `tests/test_woyo.py` (disjoint worker); `src/unlimited/cli.py`, `tests/test_sources.py` (parent).

**Interface:** `models(cred: Credential, now: datetime, get) -> dict`, matching existing OpenCode/CommandCode hooks. `MODELS_URL = "$FY_API_BASE_URL/v1/models"`.

- [ ] Add model-source tests for fixture bearer auth, exact URL, sorted/deduplicated advertised IDs, malformed/non-list data, refusal, and dispatchability. Fixture expectation:

```python
[{"id": "glm-5.3", "dispatchable": True},
 {"id": "glm-5.3-flash", "dispatchable": True},
 {"id": "gpt-6-luna", "dispatchable": True}]
```

- [ ] Run `PYTHONPATH=src:tests python3 -m unittest test_woyo` and confirm models tests fail before implementation.
- [ ] Implement the existing authenticated route-reading pattern: call `get(MODELS_URL, bearer_headers, now)`; propagate failed transport with `failed`; parse nonempty string IDs from the vendor data list; return `reading(...)` with sorted routes. Intersect advertised IDs with verified served IDs (`glm-5.3`, `glm-5.3-flash`, `gpt-6-luna`, `gpt-6.1-sol`, `gpt-6-astra`). Leave unverified aliases listed but false. Record the GPT streaming defect without treating accepted intermittent failures as permanent non-dispatchability. No runtime smoke probes on ordinary reads.
- [ ] Add CLI tests for both `--vendor woyo routes` and `routes --vendor woyo`, asserting no other vendor fetch. Patch fake adapters before parser construction so the Woyo hook participates in choices.
- [ ] Give routes' duplicate `--vendor` argument `default=argparse.SUPPRESS`. Do not change root/subcommand precedence elsewhere in this task.
- [ ] Run `test_sources`, `test_woyo`, `test_help` and full Python suite. Verify real vendor-filtered routes after local installation. No catalog offering or discovery-prefix rewrite.

## Task 3: Native wording and integration evidence

**Files:** `macos/Sources/UnlimitedKit/Card.swift`, `macos/Tests/UnlimitedKitTests/EstimatedCostTests.swift`, README/native README and the current design notes.

- [ ] Change the existing USD-budget card title from `Weekly · estimated` to `Rolling weekly`; keep token-budget fallback title and all nil forecast/reset fields.
- [ ] Update its literal title assertion, then run the native kit tests and rendered percentage alignment test with the CommandLineTools Testing plugin path.
- [ ] Build `make -C macos app`; install local CLI with `uv tool install --force .`; replace the owner's installed app only after checking its running PID and current target. Open the app and verify binary hashes match.
- [ ] Live check: read and verdict report the same budget fraction and explicit rolling source; provider verdict remains no-window for WY. Temporarily change profile budget/rates, assert cache-hit repricing, then restore $200 and the configured rates. Do not print secrets.
- [ ] Refresh planning/docs to describe delivered additive progress and defer consumer adoption/forecasting honestly. Preserve generated non-secret stream evidence for issue 219.
- [ ] Run the independent PR gate after integration, then merge/release per project policy. Link issue 219 for closure only when model publication and both vendor-filter forms are verified. Record the upstream GPT streaming defect as deferred rather than claiming it fixed.

## Dependent owner stage

Consumer repositories must preserve the budget-progress side object and explicitly adopt it. Their account wire/evaluator integration is not completed by this PR; do not advertise full compatibility there until their own checks pass. Rolling forecasting and combined ranking remain deferred.
