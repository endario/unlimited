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
tiers = ["standard", "heavy", "unproven"]  # capability classes; the file's keeper names them
banned = ["some-model"]                # a provider, model, vendor or offering id, never offered
tie_preference = ["meta", "deepseek"]  # providers preferred among near-equal candidates

[models.deepseek-v4-1-flash]           # a model, by a vendor-neutral name
provider = "deepseek"                  # its maker
tiers = ["standard"]                   # the tiers it serves

[[offerings]]                          # a vendor's route to a model
id = "opencode-go/deepseek-v4.1-flash" # the vendor's name for it: unique, what a caller launches
model = "deepseek-v4-1-flash"
vendor = "opencode"                    # whose account a use spends
# debit = 2                            # optional: a run uses this many times the account share a
#                                      # plain offering's does (default 1); scales its quota price

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
plan. A local file that does not parse,
states an unknown `schema`, or holds a top-level key a schema-2 file does not have, is an error,
never ignored.

A local `schema = 1` file is still read.

## Discovery

`unlimited routes` records, beside the local catalog in `discovered.json`, every dispatchable
route id each account's plan names that this file does not ship. Loading folds each one in as a
model at the `unproven` tier (provider `stealth` unless the maker is already known), with one
offering id-prefixed as its vendor's shipped offerings are. A route this file ships stands as
shipped. A `tiers` list without `unproven` leaves discovery out
entirely. A `discovered.json` that does not parse is an error, never ignored.

## Switches

JSON switch rows carry `policy_projection="unlimited-policy-keys-v1"` and `policy_keys`.
Intersect these opaque keys with an annotated reading's keys to match factual exclusions; require
both the marker and array. An empty array is authoritative, not permission to discard the switch:
non-usage targets still exclude catalog routes. Preserve `target` and `account` as returned for
exact `on` selectors, and `until` and `why` for display. `catalog.project_switch` returns a transient
copy; these fields are not switch persistence.

`unlimited off TARGET [--for 90m|12h|1d|1w] [--why TEXT]` takes a provider, model, vendor, offering id
or `provider:model` pair out of the catalog on this machine, until `unlimited on TARGET` or the time
passes; `unlimited off` lists what is off, `unlimited off --json` as a JSON array. A vendor target may
name a usage vendor with no catalog route here — the switch then applies to that vendor's accounts
(`verdict` excludes them), not to any route; `--account NAME` narrows it to one account (its id
from `read`, or an identity name like `claude-glm-2`), leaving the vendor's other accounts
offerable. A name that is both a provider and a usage vendor
(`commandcode`) does both. Kept in `switches.json` beside the local catalog; a write holds
`switches.lock` beside it across the whole read-modify-write. A banned model is permanent until
the file changes; a switch is for a while.

## Incentives

`unlimited incentive TARGET MULTIPLIER [--account NAME] [--for DURATION]` uses the same target
syntax without removing a route. A positive finite multiplier divides its time cost: above one
encourages, below one discourages. Quota cost is unchanged. Settings are machine-local in
`incentives.json` beside the catalog, with locked atomic updates independent of switches.

Requested multipliers must be finite and at least `1e-6`. Resolution caps the winning factor at
`1e6`; stored settings retain the request. A clamped winner reports the requested and effective
values. Requests above the cap have the same effective factor, not distinct strengths.

Omitting `--for` captures the latest upcoming usage-window reset for each affected account;
those expiry timestamps do not move with later readings. An explicit duration overrides reset
expiry and needs no vendor read. Missing reset information requires a duration. List active
settings with `incentive --json`; remove an exact scope with `incentive TARGET off`. Account
IDs and known identity aliases identify the same account scope.

A reading's `steering.settings` lists active applicable settings, including overridden ones;
`steering.routes` lists effective winners with concrete expiry timestamps. Ranking receives
explicit account bindings for account-scoped or reset-bound settings; see [choice.md](choice.md).
