# Manual steering implementation

Spec: [design.md](design.md)

The implementation was split by its shared contract:

1. Backend policy resolution, frozen account reset expiry and persistence; CLI set/list/clear;
   deterministic and sampled time-cost adjustment; post-cache schema-1 policy overlay.
2. macOS decoding and CLI bridge; serialized writes and fenced refreshes; Settings controls;
   shared vertical glyphs and variable-width strip geometry.
3. Integration verification, documentation, independent review and release.

Verification uses the repository's Python unittest suite, Swift Testing, the real CLI against
isolated fixture state, and rendered SwiftUI glyphs in both appearances. CI covers the supported
Python versions. The independent reviewer could not run Swift tests because their compiler and
SDK did not match; the local matched toolchain passed the Swift suite and app build.

Until-reset and account-scoped settings require explicit caller account bindings. Activation
warns about that requirement; caller-side adoption and remote policy propagation are outside
this utility's implementation.
