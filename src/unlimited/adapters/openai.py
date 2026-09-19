"""OpenAI Codex (ChatGPT sign-in): `/wham/usage` on the token in Codex's own auth.json."""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from ..credential import Credential
from ..schema import OK, UNREAD, failed, limit, reading

VENDOR = "openai"
URL = "https://chatgpt.com/backend-api/wham/usage"


def _home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def discover() -> list[Credential]:
    try:
        got = json.loads((_home() / "auth.json").read_text())
    except (OSError, ValueError):
        return []
    tokens = got.get("tokens") if isinstance(got, dict) else None
    if not (isinstance(tokens, dict) and isinstance(tokens.get("access_token"), str)):
        return []
    who = tokens.get("account_id")
    return [Credential(who if isinstance(who, str) and who else None,
                       {"access_token": tokens["access_token"], "account_id": who or ""})]


def _jwt_expiry(token: str) -> datetime | None:
    if token.count(".") != 2:
        return None
    part = token.split(".")[1]
    try:
        exp = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))).get("exp")
        return datetime.fromtimestamp(exp, tz=timezone.utc) if isinstance(exp, (int, float)) else None
    except (ValueError, TypeError, AttributeError, OSError, OverflowError):
        return None


def _seconds(value: object) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def limits(body: dict, now: datetime) -> list[dict]:
    named = [("codex", body.get("rate_limit"))]
    for extra in body.get("additional_rate_limits") or []:
        if isinstance(extra, dict):
            named.append((str(extra.get("limit_name") or extra.get("metered_feature")),
                          extra.get("rate_limit")))
    out = []
    for name, rl in named:
        if not isinstance(rl, dict):
            continue
        # The vendor's own word that the limit has stopped work; nothing is derived from a number.
        reached = rl.get("limit_reached") is True or rl.get("allowed") is False
        for which in ("primary", "secondary"):
            w = rl.get(f"{which}_window")
            if not isinstance(w, dict):
                continue
            resets = _seconds(w.get("reset_at"))
            pct = w.get("used_percent")
            secs = w.get("limit_window_seconds")
            out.append(limit(
                name if which == "primary" else f"{name} ({which})",
                window_minutes=secs // 60 if isinstance(secs, int) and not isinstance(secs, bool) else None,
                used_at_least=pct / 100 if isinstance(pct, (int, float)) and not isinstance(pct, bool)
                and resets is not None and resets > now else None,
                resets_at=resets, held=reached, held_why="limit_reached" if reached else None))
    return out


def read(cred: Credential, now: datetime, get) -> dict:
    token = cred.secret["access_token"]
    expires = _jwt_expiry(token)
    if expires is not None and expires <= now:
        # Codex refreshes its own token on its next run; unlimited never writes auth.json.
        return reading(VENDOR, cred.account, now, UNREAD, why="credential-expired")
    ans = get(URL, {"Authorization": f"Bearer {token}",
                    "ChatGPT-Account-Id": str(cred.secret["account_id"])}, now)
    if ans.body is None:
        return failed(VENDOR, cred.account, now, ans)
    plan = ans.body.get("plan_type")
    return reading(VENDOR, cred.account, now, OK, limits=limits(ans.body, now),
                   plan=plan if isinstance(plan, str) else None)


def _session_limits(snap: dict, now: datetime) -> list[dict]:
    """One `rate_limits` snapshot from a Codex session log, in the same shape `limits()` gives
    `/wham/usage`."""
    name = "codex" if snap.get("limit_id") == "codex" else str(snap.get("limit_name") or snap.get("limit_id"))
    out = []
    for which in ("primary", "secondary"):
        w = snap.get(which)
        if not isinstance(w, dict):
            continue
        resets = _seconds(w.get("resets_at"))
        pct, mins = w.get("used_percent"), w.get("window_minutes")
        reached = snap.get("rate_limit_reached_type") is not None
        out.append(limit(
            name if which == "primary" else f"{name} ({which})",
            window_minutes=mins if isinstance(mins, int) and not isinstance(mins, bool) else None,
            used_at_least=pct / 100 if isinstance(pct, (int, float)) and not isinstance(pct, bool)
            and resets is not None and resets > now else None,
            resets_at=resets, held=reached, held_why="limit_reached" if reached else None))
    return out


TAIL = 1 << 20


def _tail(f) -> str:
    """The last TAIL bytes of a session log, from its first whole line: logs grow to hundreds of
    megabytes, and only their newest events matter."""
    with open(f, "rb") as h:
        size = h.seek(0, 2)
        h.seek(max(size - TAIL, 0))
        data = h.read()
    if size > TAIL:
        data = data[data.find(b"\n") + 1:]
    return data.decode(errors="replace")


def local(now: datetime) -> list[dict]:
    """The newest `rate_limits` Codex itself logged, with no network call.

    A session log does not name its account, so only events written after auth.json last
    changed are trusted to belong to the account signed in now. auth.json is also rewritten on
    token refresh; that costs a network read, never a wrong attribution.
    """
    creds = discover()
    if not creds or creds[0].account is None:
        return []
    try:
        since = datetime.fromtimestamp((_home() / "auth.json").stat().st_mtime, tz=timezone.utc)
        files = sorted((_home() / "sessions").rglob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return []
    # Codex logs each limit as its own event, so one file may hold only some of them: gather the
    # newest of each across recent files.
    latest: dict[str, tuple[datetime, dict]] = {}
    for f in files[-5:]:
        try:
            if datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc) < since:
                continue
            lines = _tail(f).splitlines()
        except OSError:
            continue
        for line in lines:
            if '"rate_limits"' not in line:
                continue
            try:
                ev = json.loads(line)
                snap = ev["payload"]["rate_limits"]
                at = datetime.fromisoformat(ev["timestamp"].replace("Z", "+00:00"))
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
            key = str(snap.get("limit_id")) if isinstance(snap, dict) else None
            if key and at > since and (key not in latest or at > latest[key][0]):
                latest[key] = (at, snap)
    # Without the main limit a reading would look complete while missing the one that matters.
    if "codex" not in latest:
        return []
    taken = min(at for at, _ in latest.values())  # as old as its oldest part
    found = [l for _, snap in latest.values() for l in _session_limits(snap, now)]
    return [reading(VENDOR, creds[0].account, taken, OK, limits=found, source="session-log")]
