# Account identity

Owner intent (2026-10-10): every account unlimited reports is uniquely identified by the vendor's own
account id, so readings of one account taken on different machines, or through different keys,
correlate and de-duplicate. A hash of the API key stands in only where no vendor source of an id
exists. The id reaches the JSON output, the CLI and the macOS app.

## Sources

| Vendor | `account` | Source | Measured 2026-10-10 |
|---|---|---|---|
| anthropic | `oauthAccount.accountUuid` | config dir (unchanged) | — |
| openai | ChatGPT `account_id` | auth.json (unchanged) | — |
| xai | `user_id` | `~/.grok/auth.json` (unchanged) | — |
| zai | `customerId` | `GET https://api.z.ai/api/biz/subscription/list`, `data[*].customerId` | two keys, two distinct ids |
| kimi | `user_id` | `GET https://api.kimi.com/coding/v1/me` | answers `user_id` (plus email, nickname: never kept) |
| commandcode | `user.id` | `GET https://api.commandcode.ai/alpha/whoami` | answers `user.id`, `org` |
| opencode | key id | none: `zen/go/v1/usage` resolves the key's workspace and user but returns neither ([anomalyco/opencode#54284](https://github.com/anomalyco/opencode/pull/54284) proposes it) | — |
| neuralwatt | key id | none: `/v1/quota` names only the key's label | — |

A **key id** is today's `credential.account_of(key)`, `sha256(key)[:16]`. It identifies a key, not an
account.

## Shape

Each reading gains two fields; `schema` stays 1, since both are additive:

- `account_source`: `"vendor"` when `account` is the vendor's id, `"key"` when it is a key id.
- `key_ids`: the key ids on this machine that resolve to this account, like `names` local and never
  cached. Empty for vendors without API keys.

`account` changes value for zai, kimi and commandcode. That is the breaking part, so the release is
0.2.0.

## Resolution

An adapter with a vendor source exposes `whoami(key, now, get) -> Answer-like (account | None, why,
retry_until)`. Resolution is per key and permanent: a key belongs to one account for its life.

- `~/.cache/unlimited/identities.json` (0600, atomic write) maps `{vendor: {key_id: account}}` for
  resolved keys and `{vendor: {key_id: {"retry_until": iso}}}` for a failed attempt. It holds no
  secret and no personal data. It is the only way an offline caller (`discover`, `names`,
  `choice.vendors_here`, `incentives.account_context`) learns a key's account.
- `cache.through` resolves, under the vendor lock and before the read loop, every discovered key
  that has no mapping and is not backing off. A failure records the backoff (`MIN_BACKOFF` floor,
  the transport's deadline when longer).
- `discover()` stays offline: it returns one `Credential` per **account**, grouping keys that map to
  the same account (the first key by key id reads; all are listed in `key_ids`). A key whose
  vendor-sourced account is not yet known is a `Credential(account=None, key_ids=(kid,))`.
- An unresolved key yields an `unread` reading with `why="identity-unread"`, `account=None` and its
  `key_ids`, no limits. It is never cached as a reading and never falls back to the key id: an
  account must not change id once it has readings. A vendor whose source answers without an id (a
  Z.ai key with no subscription) is the same `identity-unread` case, with `why="identity-none"`.
- For opencode and neuralwatt, `account` is the key id and `account_source` is `"key"`, resolved
  locally with no request.

## Aliases and stored state

Every key id becomes an alias of its account, alongside `names`. `identity.names()`,
`policy_keys` and the app's `Tile.policyKeys` include `vendor/<key_id>`. That carries everything
written against the old id:

- A switch (`off --account <hash>`) keeps excluding the account: it fails closed, never released.
- An incentive group or reset binding frozen with the old hash keeps matching: `_group_identity`
  accepts a frozen account that is the canonical account or one of its key ids.
- New writes keep today's rule: the selector as typed.

## Cache namespace

zai, kimi and commandcode move to `<vendor>.v2.json` and `<vendor>.v2.lock`. Older installs on the
same machine (an agent pinned to an older unlimited, a Go port of it) keep writing `<vendor>.json`
keyed by key id; sharing one file would make each side miss the other's entries, read upstream again,
and drop the other's rows on write. Two files cost a second vendor read per interval while both
generations run, and nothing else.

On first write of a `.v2` file, its projection history is seeded from the legacy file's, re-keyed
`key_id\tname` → `account\tname` through `identities.json`; where two keys share an account the first
key id's history wins. Readings are not imported: the first call reads upstream.

## CLI

- `read`, `status --json`, `verdict --json` carry the two fields.
- `unlimited accounts [--json]` lists, offline, each account here: vendor, account, source, names,
  key ids. It is how a person finds the id to write in a caller's configuration.
- `status` labels by names first, as today; an account with no names shows its id's first 8
  characters, and an unresolved key shows `key <key id[:8]>?`.

## macOS app

- `Reading` decodes `accountSource` and `keyIds` (absent on older CLIs: `nil` / `[]`).
- A tile's id stays `vendor/account`; an unresolved key's is `vendor/key:<key id>`.
- On load, a `Preferences` entry (label, hidden, order) keyed `vendor/<key id>` moves to the
  account's tile id when no entry exists there yet. Nothing else is rewritten.
- The account row and popover header show the account id, shortened, with a `key` marker when
  `account_source` is `"key"`; the full id is in the tooltip and copyable. An unresolved key shows
  "Identifying…" with its `why`.

## Out of scope

- 2mw2lt (its Python agent derives zai and commandcode ids with `account_of`, its Go port has its own
  copy, and coordination keys state on the id) migrates in its own work, after this release. Its
  `uv.lock` pins unlimited, so nothing moves under it until it bumps.
- `.claude` configuration that names accounts follows likewise.

## Acceptance

- With both GLM keys on a machine, `unlimited accounts --json` shows two zai accounts with
  `account_source: "vendor"` and their `customerId`s; the same keys on a second machine give the
  same ids.
- Two env files holding keys of one account produce one reading with both key ids.
- An existing `off zai --account <old hash>` still excludes the account after upgrade.
- Offline (`get` raising), `discover()` and `names()` return the same ids as online once resolved.
- The app keeps a hidden account hidden and a custom label across the upgrade.
- No cache or identity file contains a key, an email or a name the vendor returned.
