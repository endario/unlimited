# The model catalog

Which models each provider offers at each tier, what is on promotion, what is banned, which
providers are preferred among near-equals, and what vendors publish about their models. Shipped in
[`src/unlimited/catalog.toml`](../src/unlimited/catalog.toml); a machine overrides it in
`$XDG_CONFIG_HOME/unlimited/catalog.toml` (default `~/.config/unlimited/catalog.toml`) with no
release. Read it with `unlimited models [--tier T] [--provider P] [--json]`, the whole merged catalog
with `unlimited models --catalog`, or `unlimited.catalog.load()`.

## Format (`schema = 1`)

```toml
schema = 1
tiers = ["standard", "heavy"]          # capability classes; the file's keeper names them
banned = ["some/model-id"]             # never offered, wherever listed
tie_preference = ["meta", "deepseek"]  # preferred among near-equal candidates, first most

[providers.deepseek]                   # a provider is the maker of the models listed under it
usage = "opencode"                     # the vendor whose account a use spends
standard = "opencode-go/deepseek-v4.1-flash"   # its model at a tier; absent tiers offer nothing

[[promotions]]                         # offered at a tier for a while, at no quota cost
provider = "stealth"
model = "commandcode/stealth/space-bunny-alpha"
tiers = ["standard"]
until = 2026-09-30                     # optional: live through that day, UTC

[[cards]]                              # what a vendor publishes about a model it sells
vendor = "commandcode"
plan = "goat"                          # optional
name = "GLM-5.3"
models = ["glm-5.3"]                   # the ids this catalog lists the model under
intelligence = 44.8                    # optional: the vendor's quoted index
tok_s = 63                             # optional: the vendor's quoted output speed
price = { input = 1.40, output = 4.40, cache_read = 0.26 }   # optional, USD per million tokens
source = "https://…"
as_of = 2026-09-26
```

A model id is listed under one provider only, so a model two vendors sell is two ids and two routes.
A provider table may carry keys of the reader's own (how it launches that provider, say); unlimited
keeps them in `--catalog` and never reads them.

## Merging a local file

Provider tables merge key by key; `tiers`, `promotions` and `tie_preference` are replaced whole;
`banned` is the union of both; a card replaces the shipped card of the same vendor, name and plan.
A local file that does not parse, or states another `schema`, is an error, never ignored.

## Switches

`unlimited off TARGET [--for 90m|12h|1d|1w] [--why TEXT]` takes a provider, a model id or a
`provider:model` pair out of the catalog on this machine, until `unlimited on TARGET` or the time
passes; `unlimited off` lists what is off. Kept in `switches.json` beside the local catalog. A
banned model is permanent until the file changes; a switch is for a while.
