# unlimited

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
