# unlimited

## What unlimited is

A public, general-purpose utility, decoupled from its users and useful on its own: a usage tracker,
a vendor and model catalog, and a load balancer that answers "which candidate is best for this
task". It is domain agnostic (owner, 2026-09-26):

- Keep callers' scenarios out of it: no review, critique, round, gate, seat or harness concept in
  its code, flags, data model, defaults or docs. A caller maps its scenario onto generic parameters
  (a duration, a deadline, how much to explore, what time is worth).
- Hold no caller's configuration: no round order, effort policy, budgets, launch details or
  independence rules. Those belong to the caller.
- Let a caller attach its own metadata (an opaque task label, key/value pairs): record it with the
  request for later analysis, and never branch on it.
- Record each choice and each attempt as it was asked and as it came out: the whole request and
  the whole result.
- Name things for what they are in general, not for the first caller's use of them. Before adding a
  field, flag, constant or sentence, ask whether it names a particular caller's scenario; if it does,
  it belongs to the caller, and unlimited takes a generic parameter instead.

## Release after every merged change

A change is not done when its PR merges; it is done when it is on PyPI. At the stop point after
merging to `main`:

1. If the merged work has not bumped it, bump `version` in `pyproject.toml` in its own PR and merge.
2. Tag the merge commit `vX.Y.Z` and push the tag; the Publish workflow releases to PyPI.
3. Create the GitHub release: `gh release create vX.Y.Z --generate-notes --latest` (the workflow
   does not).
4. Confirm PyPI serves it, then `uv tool install --force unlimited==X.Y.Z` on each machine that
   uses it.

A merge that changes only `macos/**` ships no Python, so it needs no version bump or release.
