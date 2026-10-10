"""Vendor adapters. Each exposes `VENDOR`, `discover() -> list[Credential]` and
`read(cred, now, get) -> reading`, where `get(url, headers, now)` returns a transport.Answer."""

from . import anthropic, anthropic_console, commandcode, kimi, neuralwatt, opencode, openai, xai, zai

REGISTRY = {m.VENDOR: m for m in (anthropic, anthropic_console, commandcode, kimi, neuralwatt, opencode, openai, xai, zai)}
