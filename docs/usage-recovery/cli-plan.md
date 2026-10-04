# CLI Message Extension Plan

Goal: human CLI status and the app share builtin failure messages; reading JSON is unchanged.

Design: [design.md](design.md).

- [x] Write failing CLI tests for readable reasons, retry delays, fallback and unchanged input data.
- [x] Create the canonical usage_errors.json registry and errors.describe helper; use it in show.render. Replace raw reasons with title/body and an unavailable marker. Describe retained backoff as refresh paused.
- [x] Package the canonical registry with SwiftPM, include its resource bundle in the app, and use registry lookup from Swift. Keep UI freshness and vendor login policy local.
- [x] Run Python and Swift suites, build a wheel and signed app, and inspect their shipped registry copies. Render CLI error fixtures and a native failure panel; use fake wrappers for any login test.
- [x] Review the coherent local diff, fix verified findings, preserve evidence, and report the installed/local-build distinction.

Critic disposition: the duplicate-map and copy-only-login alternatives conflict with the standing no-copy rule and explicit one-click request. The registry is a builtin presentation resource, not a new reading schema or caller policy.