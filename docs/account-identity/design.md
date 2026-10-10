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

Each reading gains three fields; `schema` stays 1, since both are additive:

- `account_source`: `"vendor"` when `account` is the vendor's id, `"key"` when it is a key id.
- `key_ids`: the key ids on this machine that resolve to this account, like `names` local and never
  cached. Empty for vendors without API keys.
- `provisional`: `null`, or why the key's vendor id is not yet known: `"identity-unread"` (the
  request failed) or `"identity-none"` (the source answered without one). `why` keeps its meaning,
  the reason a usage read failed, so nothing that reads `why` sees an identity reason.

`account` changes value for zai, kimi and commandcode. That is the breaking part, so the release is
0.2.0.

## Resolution

An adapter with a vendor source exposes `whoami(key, now, get) -> Answer-like (account | None, why,
retry_until)`. Resolution is per key and permanent: a key belongs to one account for its life.

- `~/.cache/unlimited/identities/<vendor>.json` (0600, atomic write, written only under that
  vendor's lock, so vendors never contend for it) maps `{key_id: account}` for resolved keys and
  `{key_id: {"retry_until": iso, "failures": n}}` for a failed attempt. It holds no secret and no
  personal data. It is the only way an offline caller (`discover`, `names`,
  `choice.vendors_here`, `incentives.account_context`) learns a key's account.
- `cache.through` resolves, under the vendor lock and before the read loop, every discovered key
  that has no mapping and is not backing off. A failure records the backoff (`MIN_BACKOFF` floor,
  the transport's deadline when longer).
- `discover()` stays offline: it returns one `Credential` per **account**, grouping keys that map to
  the same account (the first key by key id reads; all are listed in `key_ids`). A key whose
  vendor-sourced account is not yet known is a provisional `Credential` whose account is its key id.
- An unresolved key is still read, so a machine whose identity requests fail sees what it sees
  today. Its reading is **provisional**: `account` is the key id, `account_source` is `"key"`, and
  `provisional` says why the vendor id is missing (`identity-none` covers a Z.ai key with no
  subscription). A
  provisional reading is never written to the reading cache or the projection history, so no state
  binds to an id the account is about to leave. Callers must not persist it either. It still
  carries its key id as an alias, so a switch naming that key excludes it.
- A failed identity request backs off with the reading cache's own `retry_until` and
  `_backing_off`, from `MIN_BACKOFF`, doubling on each further failure up to the transport's cap, so
  a source that never answers costs a request per cap interval, not per read.
- Keys of one account resolve independently. While one is resolved and another backs off, the
  account shows twice: once under its vendor id and once provisionally.
- A mapping is permanent. Should a vendor ever re-identify an account, the person sees it as a
  break in that account's usage; deleting `identities/<vendor>.json` makes its keys resolve again on
  the next read.
- `discover()` depends on a previous `cache.through` having resolved the keys. On a machine's first
  run every key of these vendors is unresolved until that read completes.
- For opencode and neuralwatt, `account` is the key id and `account_source` is `"key"`, resolved
  locally with no request.

## Aliases and stored state

Every key id becomes an alias of its account, alongside `names`. `identity.names()`,
`policy_keys` and the app's `Tile.policyKeys` include `vendor/<key_id>`. That carries everything
written against the old id:

- A switch (`off --account <hash>`) keeps excluding the account: it fails closed, never released.
- An incentive group or reset binding frozen with the old hash keeps matching: the exact comparison
  in `incentives._group_identity` (`canonical == binding["account"]`) accepts a frozen account that
  is the canonical account or one of its key ids.
- New writes keep today's rule: the selector as typed.

## Cache namespace

zai, kimi and commandcode move to `<vendor>.v2.json` and `<vendor>.v2.lock`. Older installs on the
same machine keep writing `<vendor>.json`: today 2mw2lt's Python agent runs unlimited 0.1.31 from its
own environment through `cache.through`, and its Go agent ports the same cache, both
keyed by key id; sharing one file would make each side miss the other's entries, read upstream again,
and drop the other's rows on write. Two files cost a second vendor read per interval while both
generations run, and nothing else.

Projection history follows each account across, whenever it resolves: when an account has no
history in the `.v2` file and the legacy file holds history under one of its key ids
(`key_id\tname`), that history is copied in as `account\tname`, the first key id's where several
have some. The legacy file is only read. Readings are not imported: the first call reads upstream.

## CLI

- `read`, `status --json`, `verdict --json` carry the two fields.
- `unlimited accounts [--json]` lists, offline, each account here: vendor, account, source, names,
  key ids. It is how a person finds the id to write in a caller's configuration.
- `status` labels by names first, as today; an account with no names shows its id's first 8
  characters, and a provisional reading is marked `(identifying)`.

## macOS app

- `Reading` decodes `accountSource`, `keyIds` and `provisional` (absent on older CLIs).
- A tile's id stays `vendor/account`. A provisional tile moves to its account's id once resolved,
  carrying its preferences the same way as below.
- On load, a `Preferences` entry (label, hidden, order) keyed `vendor/<key id>` moves to the
  account's tile id when no entry exists there yet. Nothing else is rewritten.
- The account row and popover header show the account id, shortened, with a `key` marker when
  `account_source` is `"key"`; the full id is in the tooltip and copyable. A provisional reading shows
  its usage with "Identifying…" in place of the id.

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
- An existing `off zai --account <old hash>` still excludes the account after upgrade, and excludes
  its provisional reading before the account resolves.
- With every identity request failing, a key's usage is still shown, marked provisional, and nothing
  under its key id reaches the cache files.
- Offline (`get` raising), `discover()` and `names()` return the same ids as online once resolved.
- The app keeps a hidden account hidden and a custom label across the upgrade.
- No cache or identity file contains a key, an email or a name the vendor returned.
