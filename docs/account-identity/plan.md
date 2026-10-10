# Account identity, slice 1: Implementation Plan

> **For agentic workers:** execute inline, task by task, test first.

**Goal:** every reading carries `vendor_account`, the vendor's own id, resolved once per key and kept.
**Architecture:** adapters gain `whoami`; a new `identities` module owns the per-vendor identity file;
`cache.through` resolves after its read loop and attaches the id like `names`.
**Spec:** [design.md](design.md), slice 1.

## Constraints

- `account` and the reading cache are untouched; `schema` stays 1.
- A usage read never waits on an identity request, and its pacing is unchanged.
- Only allowlisted fields are written: key id → `account`, or `retry_until` and `failures`.

## Task 1: `identities` module

- [x] `identities.path(vendor)`, `load(vendor)`, `accounts(vendor) -> {key_id: account}` (offline),
      `resolve(adapter, creds, now, get, directory)` writing with `state.write_json`.
- [x] Backoff: `MIN_BACKOFF × 2^(failures-1)` capped at `MAX_BACKOFF`, later of that and the vendor's
      deadline; `identity-none` backs off the same way.
- [x] Tests: success maps; failure backs off and is not re-asked inside the window; doubling; cap;
      a resolved key is never asked again; offline `accounts`; file holds only allowlisted fields.

## Task 2: adapters

- [x] `zai.whoami`: `subscription/list`, `data[*].customerId`, one distinct value or none.
- [x] `kimi.whoami`: `/coding/v1/me`, `user_id`.
- [x] `commandcode.whoami`: `/alpha/whoami`, `user.id`.
- [x] anthropic, openai, xai: `vendor_account` is `account`, no request.
- [x] Tests per adapter: parsed id; malformed or absent body is `None`; email and nickname never
      leave `whoami`.

## Task 3: `cache.through`

- [x] After the read loop and write: `identities.resolve` for discovered keys; attach
      `vendor_account` to every returned reading.
- [x] Tests: usage read and its 429 deadline unchanged when identity fails; a later success fills
      `vendor_account` without a usage read; existing call-count contracts updated for the one
      identity request per key in a key's life.

## Task 4: CLI

- [x] `unlimited accounts [--json]`, offline: vendor, account, vendor_account, names.
- [x] README: the field and the command; help text.

## Task 5: macOS app

- [x] `Reading.vendorAccount`; popover header and account row show it shortened, full id in the
      tooltip, selectable.
- [x] Swift tests for decoding with and without the field.

## Task 6: release

- [ ] Bump `pyproject.toml` (0.1.36), review, merge, tag, publish, install.
