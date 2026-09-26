# The model catalog

Which models each provider offers at each tier, what is on promotion, what is banned, which
providers are preferred among near-equals, and what vendors publish about their models. Shipped in
[`src/unlimited/catalog.toml`](../src/unlimited/catalog.toml); a machine overrides it in
`$XDG_CONFIG_HOME/unlimited/catalog.toml` (default `~/.config/unlimited/catalog.toml`) with no
release. Read it with `unlimited models [--tier T] [--provider P] [--json]`, the whole merged catalog
with `unlimited models --catalog`, or `unlimited.catalog.load()`.

## Format (`schema = 2`)

```toml
schema = 2
tiers = ["standard", "heavy"]          # capability classes; the file's keeper names them
banned = ["some-model"]                # a provider, model, vendor or offering id, never offered
tie_preference = ["meta", "deepseek"]  # providers preferred among near-equal candidates

[models.deepseek-v4-1-flash]           # a model, by a vendor-neutral name
provider = "deepseek"                  # its maker
tiers = ["standard"]                   # the tiers it serves

[[offerings]]                          # a vendor's route to a model
id = "opencode-go/deepseek-v4.1-flash" # the vendor's name for it: unique, what a caller launches
model = "deepseek-v4-1-flash"
vendor = "opencode"                    # whose account a use spends

[[offerings]]
id = "commandcode/stealth/space-bunny-alpha"
model = "space-bunny-alpha"
vendor = "commandcode"
free = true                            # a promotion: no quota cost
until = 2026-09-30                     # optional: offered through that day, UTC

[[cards]]                              # what a vendor publishes about a model it sells
vendor = "commandcode"
plan = "goat"                          # optional
name = "GLM-5.3"
models = ["glm-5.3"]                   # the offering ids the figures apply to
intelligence = 44.8                    # optional: the vendor's quoted index
tok_s = 63                             # optional: the vendor's quoted output speed
price = { input = 1.40, output = 4.40, cache_read = 0.26 }   # optional, USD per million tokens
source = "https://…"
as_of = 2026-09-26
```

One model may have several offerings, one per vendor that sells it; each is its own route, with its
own history. `Catalog.routes(now, tier)` lists every live one (`id`, `provider`, `model`, `vendor`,
`tiers`, `free`), free ones first. `model(provider, tier)` and the `providers` and `promotions` keys
of `--catalog` are a view for readers that launch one offering per provider: one vendor per provider,
its first live offering at each tier.

## Merging a local file

Models merge key by key; an offering replaces the shipped offering with the same id, or is added;
`tiers` and `tie_preference` are replaced whole (a tier the local list drops is served by no shipped
model); `banned` is the union of both; a card replaces the shipped card of the same vendor, name and
plan. A local file that does not parse, or states an unknown `schema`, is an error, never ignored.

A local `schema = 1` file is still read.

## Switches

`unlimited off TARGET [--for 90m|12h|1d|1w] [--why TEXT]` takes a provider, model, vendor, offering id
or `provider:model` pair out of the catalog on this machine, until `unlimited on TARGET` or the time
passes; `unlimited off` lists what is off. Kept in `switches.json` beside the local catalog. A
banned model is permanent until the file changes; a switch is for a while.
