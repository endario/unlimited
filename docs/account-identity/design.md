# Account identity

Owner intent (2026-10-10): every account unlimited reports is uniquely identified by the vendor's own
account id, so readings of one account taken on different machines, or through different keys,
correlate and de-duplicate. A hash of the API key stands in only where no vendor source of an id
exists. The id reaches the JSON output, the CLI and the macOS app.

The critic's third round decomposed the first design, which also made the vendor id the `account`
field at once. Doing so moves the reading cache, the routes cache, switches, incentive bindings, the
app's preferences and usage pacing in one step, and two of those break: a provisional reading that
is never cached loses its 429 deadline, and an alias set built from the keys present today releases
a switch written against a key that has since been removed. So identity arrives in two slices.

## Slice 1: resolve and report (this design)

`account` keeps its value. Each reading gains the vendor's id beside it, resolved once per key and
kept, so every consumer can correlate accounts today, and slice 2 has a tested foundation.

### Sources

| Vendor | `vendor_account` | Source | Measured 2026-10-10 |
|---|---|---|---|
| anthropic | `account` (`accountUuid`) | config dir | — |
| openai | `account` (ChatGPT `account_id`) | auth.json | — |
| xai | `account` (`user_id`) | `~/.grok/auth.json` | — |
| zai | `customerId` | `GET https://api.z.ai/api/biz/subscription/list`, `data[*].customerId` | two keys, two distinct ids |
| kimi | `user_id` | `GET https://api.kimi.com/coding/v1/me` | answers `user_id`, plus email and nickname, which are never kept |
| commandcode | `user.id` | `GET https://api.commandcode.ai/alpha/whoami` | answers `user.id` and `org` |
| opencode | `null` | none: `zen/go/v1/usage` resolves the key's workspace and user but returns neither ([anomalyco/opencode#54283](https://github.com/anomalyco/opencode/issues/54283)) | — |
| neuralwatt | `null` | none: `/v1/quota` names only the key's label | — |

### Shape

Each reading gains one field; `schema` stays 1:

- `vendor_account`: the vendor's id for the account, or `null` where the vendor has no source or the
  key is not yet resolved.

### Resolution

An adapter with a source exposes `whoami(cred, now, get) -> (account | None, Answer)`, returning only
the id it parsed.

- `~/.cache/unlimited/identities/<vendor>.json`, written with `state.write_json` only under that
  vendor's cache lock, maps a key id to `{"account": id}` when resolved, or to
  `{"retry_until": iso, "failures": n}` after a failed attempt. It holds key ids and vendor account
  ids, which are linkable identifiers, so it is owner-only like the reading cache, and only those
  allowlisted fields are written, never a response.
- `cache.through` resolves after its read loop, so a usage read never waits on an identity request:
  each discovered key with no mapping and no live `retry_until` is asked once. Its outcome is
  attached to this call's readings too.
- A failure sets `retry_until` to `MIN_BACKOFF × 2^(failures-1)`, capped at the transport's
  `MAX_BACKOFF`, or the vendor's own deadline when that is later. An answer without an id
  (`identity-none`, such as a Z.ai key with no subscription) backs off the same way, so it is asked
  again later rather than recorded as having no account.
- A mapping is permanent: a key belongs to one account for its life. Should a vendor ever
  re-identify an account, deleting `identities/<vendor>.json` makes its keys resolve again.
- `vendor_account` is attached like `names`: from the identity file on every call, never stored in
  the reading cache, so a reading cached before resolution gains it as soon as it is known.
- The map is available offline through `identities.accounts(vendor) -> {key_id: account}`.

### CLI

- `read`, `status --json` and `verdict --json` carry `vendor_account`.
- `unlimited accounts [--json]` lists, offline, each account here: vendor, `account`,
  `vendor_account`, names. It is how a person finds the id to write in a caller's configuration.

### macOS app

- `Reading` decodes `vendorAccount` (absent on older CLIs).
- The popover header and the account row's description show the vendor id, shortened, with the full
  id in the tooltip and selectable; nothing is shown where it is `null`.

### Acceptance

- With both GLM keys on a machine, `unlimited accounts --json` shows two zai accounts whose
  `vendor_account`s are their `customerId`s; the same keys on a second machine give the same ids.
- With the identity endpoint failing, usage is read and paced exactly as today; the identity request
  is made again only after its backoff; the next success fills `vendor_account` without a usage read.
- Offline (`get` raising), `identities.accounts` returns what was resolved online.
- No identity or cache file contains a key, an email or a name the vendor returned.

## Slice 2: the vendor id becomes `account` (recorded, not designed)

Its own design, after slice 1 ships and before 2mw2lt follows. What it owes, from the critique:

- **Quota scope.** A successful `whoami` proves who owns a key, not that two keys share one quota.
  Before grouping keys by account, measure two keys of one account (Command Code also returns `org`;
  Z.ai's adapter notes organisation-dependent quota answers). Needs a second key on one account.
- **Pacing.** Every per-key usage deadline survives whatever replaces caching for an unresolved key.
- **Durable aliases.** A switch or a frozen incentive binding written against a key id keeps applying
  after that key leaves the machine: aliases come from every key the identity file ever resolved to
  the account, not from the keys present.
- **Both caches.** `routes_through` keys by account too and needs the same namespace decision as
  the readings.
- **App preferences.** A rule for several old tiles collapsing into one: labels, order, and hidden
  winning over shown.
- **2mw2lt.** Its Python agent derives zai and commandcode ids with `account_of`, its Go agent ports
  the cache with its own copy, and coordination keys state on the id. Its Go agent should read
  `identities/<vendor>.json`, not resolve on its own.
