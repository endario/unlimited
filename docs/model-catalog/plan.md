# Model catalog: plan

Design: [design.md](design.md). One PR per step; each merges and ships before the next starts.
All four have shipped; what follows each step is in [design.md](design.md#usage-balancing).

## 1. unlimited (this repo)

- `src/unlimited/catalog.toml`: schema 1; providers codex, claude, glm, grok, deepseek with today's
  models (from `tier_model`), meta (Muse Spark 1.3 Contributor, standard), stealth (no tier model);
  one promotion, Space Bunny, standard, until 2026-09-30 (moved to Command Code in #84); `banned = []`.
- `src/unlimited/catalog.py`: `load(path=None) -> Catalog`, merge, validation errors as
  `CatalogError`; `Catalog.candidates(tier, now)`, `Catalog.model(provider, tier)`,
  `Catalog.provider_of(model)`.
- CLI: `unlimited models [--tier T] [--provider P] [--json]`.
- Tests: shipped file loads from the package; key-wise provider merge; promotions taken whole;
  banned union; missing or different schema raises; unparsable local raises; expiry through the
  end of the UTC day; candidate order; banned omitted everywhere.
- README: the widened role, the file and its override.
- Release. Shipped in #80.

## 2. agent-runner: `tier_model` reads the catalog

`tier_model` asks `unlimited models --provider P --tier T`; a failure fails the run.
Shipped in ren-diao/claude#156.

## 3. agent-runner: harness-keyed providers, promotions, meta and stealth

Shipped in ren-diao/claude#158; `tie_preference` in #159.

## 4. 2mw2lt: `PROFILE` models, `OPENCODE_MODEL`, `_MODEL_OF` precedence, meta and stealth reviewers

Shipped in endario/2mw2lt#2343.
