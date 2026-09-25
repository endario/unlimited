# Model catalog

unlimited already tells its consumers how much of each plan is left. This adds the other half they
need to route work: which model each provider runs at each tier, and which promotional models to try
first. The agent runner (`~/.claude/agent-runner`) and 2mw2lt (`steering/`) both depend on unlimited
already (the runner through `unlimited read --json`, 2mw2lt as a library); neither depends on the
other, and this keeps it that way.

## What it replaces

- `tier_model` and the `opencode-go/deepseek-*` guard in `agent-runner.sh`.
- `PROFILE`'s model names in `steering/gates.py`, and `OPENCODE_PROVIDER`/`OPENCODE_MODEL` in
  `steering/harness.py`.

Each consumer keeps its own policy: the runner's round orders (finding/final) and efforts, 2mw2lt's
tie rotation and efforts. The catalog says what can run; consumers say when.

## Shape

A TOML file. The repo ships `src/unlimited/catalog.toml`; a machine's own
`$XDG_CONFIG_HOME/unlimited/catalog.toml` (default `~/.config/unlimited/catalog.toml`) overrides it,
so an edit takes effect at the next read with no release.

```toml
schema = 1

[providers.deepseek]          # the maker: a reviewer never shares one with the author
harness = "opencode"          # the launcher id the consumer already resolves (the runner's
                              # resolve_agent_binary names); an account wrapper stays the consumer's
usage = "opencode"            # the unlimited vendor whose limits it spends
standard = "opencode-go/deepseek-v4.1-flash"

[providers.meta]
harness = "opencode"
usage = "opencode"
standard = "opencode-go/muse-spark-1.3-contributor"

[providers.stealth]           # unnamed preview models, whoever makes them
harness = "opencode"
usage = "commandcode"

[providers.codex]
harness = "codex"
usage = "openai"
standard = "gpt-6-luna"
heavy = "gpt-6-sol"

banned = []                   # model ids never offered, wherever they are listed

[[promotions]]
provider = "stealth"
model = "commandcode/stealth/space-bunny-alpha"
tiers = ["standard"]
until = 2026-09-30            # optional; absent means until removed
```

A provider with no model at a tier cannot run at it (Grok at heavy, stealth outside a promotion).

A consumer validates a run against the catalog: the model it launches under a provider must be one
the catalog lists for that provider. That replaces the runner's `opencode-go/deepseek-*` guard with
the same check made from data.

### Override rule

The local file is merged over the shipped one: a provider's keys replace the shipped provider's keys
one by one, and a new provider is added. `promotions`, being a hand-curated list, is taken whole from
the local file when it has the key at all, so removing a shipped promotion is deleting it locally.
A local file that does not parse is an error the consumer sees, never a silent fall-back to the
shipped copy: a curated list that quietly stops applying is the failure this exists to avoid.

A local file must state `schema`, and one that differs from the shipped one is an error, for the
same reason. `banned` is the union of both files.

## Loading

Read at call time, never at import: 2mw2lt's daemon imports `gates` without reading a catalog.
A broken catalog fails the routing call that asked, loudly: the run does not start. Consumers keep
no compiled copy to fall back on, so there is one list, and a broken one is never quietly replaced
by an older one.

The shipped file is package data, so the wheel must carry it.

## Interface

Library (2mw2lt): `unlimited.catalog.load() -> Catalog`, with
`Catalog.providers` (id → harness, usage, per-tier model) and
`Catalog.candidates(tier, now) -> [Candidate]`: live promotions first, in file
order, then every provider with a model at the tier, in file order; banned models omitted.

CLI (runner): `unlimited models [--tier standard|heavy] --json` prints the same list,
`unlimited models --provider <id> --tier <t>` prints one model id, and `unlimited models --catalog`
prints the whole merged catalog as JSON.

Expired promotions are dropped at read time: `until` is a date, and the promotion is live through
the end of that day in UTC. Nothing edits the file.

unlimited's README states the widened role: it reports usage and it lists what can run; which
account or model to use is still the consumer's choice.

## Promotions first, errors fall through

Consumers try promoted candidates before the rest. A promoted model that fails to launch or refuses
(the consumer's existing unavailability classification) falls to the next candidate, as a failed
provider does today. A promotion does not bypass independence: a stealth candidate is still excluded
when the author was stealth or unknown.

## Makers and independence

Provider ids are makers. 2mw2lt's `_MODEL_OF` gains `("muse", "meta")`, and a stealth model is
mapped by the catalog (any model listed under `stealth`, in a promotion or a tier) rather than by
name, since a stealth name says nothing. Stealth is an ordinary provider: independent of every other
provider, never of itself. This is an accepted owner decision, not a property: a stealth model that is
in fact the author's own maker's goes undetected. Where a model id is in the catalog, the catalog's
provider decides its maker; `_MODEL_OF`'s name match applies only to ids the catalog does not list. Which rounds it may take follows from its tiers like any provider's: a
stealth model listed at `heavy` can take a heavy final round, one only at `standard` cannot.

Banning is by model id, in `banned`: a banned model is never a candidate, as a tier's model or a
promotion's. There is no per-repo or per-data-policy rule: a model the owner will not
send work to is banned outright. A model id is listed under one provider only.

A ban is permanent until the file changes. To take a model, a provider or a `provider:model` pair
out for a while on one machine, `unlimited off TARGET --for 1d` (README, Models).

## Tie preference

`tie_preference` lists the low-cost providers, cheapest first (owner, 2026-09-25: Meta's Muse Spark
Contributor is cheaper than DeepSeek Flash; Command Code's models price alike). When usage leaves
candidates near-tied, the first of these among them takes the round. It replaces the runner's
DeepSeek-only tie rule, and a local list replaces the shipped one.

## Usage balancing

Each provider spends its catalog `usage` vendor: deepseek and meta the Go plan (`opencode`),
stealth Command Code (`commandcode`). The runner's `balance.py` scores a provider not known by name
on the provider that spends the same vendor (ren-diao/claude#158), and on a plan's monthly bucket
where it enforces one, else its week (ren-diao/claude#160). A usage vendor no named provider
spends, stealth's Command Code, is scored on its own (ren-diao/claude#162).

Its successor is endario/unlimited#92: one spending model in unlimited that both the runner and
2mw2lt consume, in place of each keeping its own.

## Testing

unlimited: parse and merge (key-wise provider override, whole-list promotions, a broken local file
raises), expiry, candidate order. Consumers: their existing routing tests, pointed at a fixture
catalog.

## Rollout

Each step is its own PR, and each works before the next exists. All four have shipped.

1. unlimited ships the catalog and `unlimited models` (release): #80.
2. agent-runner reads it for the named providers it has: `tier_model` goes. ren-diao/claude#156.
3. agent-runner generalises its per-provider branches onto `harness` (binary resolution, auth,
   preflight, `balance.py`'s `scored_limit`/`short_limits`/`ACCOUNTS` by `usage`), replaces the
   deepseek-only guard with the catalog check, and takes promotions: meta and stealth become
   routable. ren-diao/claude#158, with `tie_preference` in #159.
4. 2mw2lt reads it at call time: `PROFILE` models and `OPENCODE_MODEL` go; `meta` and `stealth`
   join reviewers. endario/2mw2lt#2343.
