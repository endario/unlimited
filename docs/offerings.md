# Offerings: one model, several vendors

Issue #104. Today a model id belongs to one provider and a provider to one usage vendor, so a model
sold by two vendors can only be listed by inventing a second provider, which breaks the provider's
meaning as the model's maker. As on OpenRouter, a model should have several offerings, and the
chooser should spread one model's load across them.

**The offering id is the route key everywhere**: in the catalog's candidates, in `choose`'s input and
decision, in attempts and in the statistics learned from them. Provider and model are descriptive
fields of an offering.

## Delivery (after critic round 2: decompose)

1. **Catalog identity** (this change): `schema = 2` with models and offerings, `Catalog.routes`
   listing every live route, schema-1 local files converted, bans and switches by vendor or
   offering id.
2. **Choice over routes**: `choose --quota` keyed by offering id (the caller's projection for the
   account it would launch that route on; routes on one account get the same value), attempts record
   an explicit `offering` field, statistics keyed by it (a failure on one vendor's route leaves
   another's alone), and `unavailable` counted against the route.
3. **Callers and data**: the agent runner and 2mw2lt read `routes` and launch by vendor; then the
   shipped catalog adds second offerings (Command Code for DeepSeek Flash and Muse Spark).

Deferred: pricing pay-per-token routes in dollars, and unlimited choosing accounts itself.
