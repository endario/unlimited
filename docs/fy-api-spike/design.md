# Woyo usage and route integration

**Superseded proposal; not implemented.** The current tracker design is [Woyo rolling usage](../woyo-usage/design.md). This earlier proposal is not an implementation specification.

## Intent

Expose Woyo gateway usage in unlimited and register its working GPT/GLM offerings without inventing a quota score. Workstation owns the requested `claude-wy` and `code-wy` launchers; unlimited holds no launch configuration. Runtime findings and upstream references are in this directory.

## Evidence

The owner-authorized plain-HTTP gateway identifies as New API. Token usage reports an uncapped key, no expiration or model allowlist, and zero accounting counters despite successful inference. Token-scoped recent logs carry prompt/completion counts, including zero-token failures. The gateway has working Messages routes for `gpt-6.1-sol` and `gpt-6-luna`, and a working Chat Completions route for `glm-5.3`. Flash has no available channel. Model IDs are the operator's advertised identities, not proof of upstream provenance.

## Design

- Add a `woyo` adapter using existing `Credential`, `EnvKeys`, transport and cache. Discover `FY_API_KEY` from the environment and `claude-wy*.env`, honoring `CLAUDE_WY_ENV`. Do not read a caller's project `.env`; the workstation installation places the key in its private env file.
- Query `/api/usage/token/` for quota. Add optional generic `quota` facts to readings: scope (`key`), `unlimited` boolean, accounting `used`, `limit`, `remaining`, `unit` (`provider_quota`), expiration, and model allowlist. These are not model token counts or money. Uncapped zero counters remain raw provider accounting evidence, never a percentage of capacity.
- Query `/api/log/token` for independently timestamped `consumption` facts: observed input/output tokens and record count, coverage `recent`, earliest/latest record timestamps, and source. Sum consumption records only. Do not accumulate snapshots, infer full history or create a reset window. Log failures retain quota and report accounting unavailable rather than turn it into zero.
- Render key quota as uncapped/capped/unknown and recent observed tokens without a fabricated usage bar or projection.
- Preserve the verdict contract. An uncapped reading with no scoreable window remains `unread/no-window`; that means quota ranking is unavailable, not that the model is unusable. Callers select a Woyo route explicitly or let `choose` explore it using observed attempt outcomes. No green-light verdict is derived from read-only token authentication, which is weaker than relay authentication.
- Add distinct `woyo/gpt-6.1-sol`, `woyo/gpt-6-luna`, and `woyo/glm-5.3` offerings using the operator-advertised canonical model mappings. Do not add Flash or set `free` to encode unlimited quota. `/v1/models` is catalog evidence only; discovery follows the existing unproven-route mechanism.
- Leave choice's expected-cost formula unchanged: callers can exclude unusable routes from verdicts and supply route-specific quota inputs. Uncapped-key eligibility is not a zero-price promotion or an account-capacity guarantee. The schema exposes the fact without pretending to know upstream pacing.

## Acceptance

With the local WY credential, usage JSON and text report uncapped key quota and recent observed token counts; the registered offerings appear distinctly from the original vendor routes; quota verdict remains unread/no-window rather than inventing eligibility or a score. Expired/restricted key facts remain explicit but do not promise relay access. Quota read failure is not availability; accounting failure is not zero consumption. Recent coverage, record count and earliest/latest timestamps are mandatory for successful log summaries, and projections ignore these non-windowed facts. Only allowlisted numeric/log metadata is retained: no prompts, response bodies, full raw logs or credentials. Existing adapter, cache, verdict and choice tests continue to pass. Live checks use only read-only endpoints; no load test or sensitive project prompt.

## Scope and adjacent disposition

The gateway's transport remains unencrypted by owner authorization. Upstream rate/account-funding limits are unknown and cannot be solved locally. No arbitrary gateway configuration framework or cross-vendor rank formula is added. A macOS view of the new generic facts needs checking before completion; if the current rendering hides them, that is a separate integration slice rather than an invented usage window.
