"""Anthropic (Claude Code, claude.ai sign-in). Two sources:

- local: the `rate_limits` Claude Code hands its statusline, saved by `unlimited capture
  claude-statusline` into a per-account file. No network, no token.
- api: `oauth/usage` on the token Claude Code keeps in the macOS keychain (or
  `.credentials.json` elsewhere). Read, never refreshed: refreshing rotates the refresh token and
  would break Claude Code's own copy.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from ..credential import Credential
from ..schema import OK, UNREAD, credits, failed, limit, reading

VENDOR = "anthropic"
URL = "https://api.anthropic.com/api/oauth/usage"
PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"
WINDOWS = {"five_hour": 300, "seven_day": 10080, "seven_day_opus": 10080, "seven_day_sonnet": 10080}
KEYCHAIN = "Claude Code-credentials"


def _default_dir() -> Path:
    return Path.home() / ".claude"


def account_of(config_dir: Path) -> str | None:
    for f in (config_dir / ".claude.json",) + ((Path.home() / ".claude.json",)
                                               if config_dir.resolve() == _default_dir().resolve() else ()):
        try:
            o = json.loads(f.read_text()).get("oauthAccount")
        except (OSError, ValueError, AttributeError):
            continue
        uuid = o.get("accountUuid") if isinstance(o, dict) else None
        if isinstance(uuid, str) and uuid:
            return uuid
    return None


def config_dirs() -> list[Path]:
    """Every `~/.claude*` directory. Only those signed in to a claude.ai account yield a
    credential: wrapper directories for other vendors (claude-glm, claude-kimi) carry no
    `oauthAccount`, even when their keychain slot holds a copied token."""
    return sorted(p for p in Path.home().glob(".claude*") if p.is_dir())


def _services(config_dir: Path) -> list[str]:
    named = KEYCHAIN + "-" + hashlib.sha256(str(config_dir).encode()).hexdigest()[:8]
    # The default directory's suffixed item can go stale while the unsuffixed one stays current.
    return [named, KEYCHAIN] if config_dir.resolve() == _default_dir().resolve() else [named]


def _oauth(config_dir: Path) -> list[dict]:
    recs = []
    if sys.platform == "darwin":
        for service in _services(config_dir):
            r = subprocess.run(["security", "find-generic-password", "-s", service, "-w"],
                               capture_output=True, text=True)
            if r.returncode == 0:
                try:
                    recs.append(json.loads(r.stdout).get("claudeAiOauth"))
                except (ValueError, AttributeError):
                    pass
    try:
        recs.append(json.loads((config_dir / ".credentials.json").read_text()).get("claudeAiOauth"))
    except (OSError, ValueError, AttributeError):
        pass
    return [r for r in recs if isinstance(r, dict) and isinstance(r.get("accessToken"), str)]


def _expiry(rec: dict) -> datetime | None:
    at = rec.get("expiresAt")
    if isinstance(at, bool) or not isinstance(at, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(at / 1000, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


class _Token(Mapping):
    """An account's token, read from the keychain on first use. `discover()` runs on every
    `cache.through`, including cache hits that send no token."""

    def __init__(self, dirs: list[Path]):
        self._dirs, self._got = dirs, None

    def _secret(self) -> dict:
        if self._got is None:
            now = datetime.now(timezone.utc)
            recs = [r for d in self._dirs for r in _oauth(d)]
            rec = next((r for r in recs if (_expiry(r) or now) > now), recs[0] if recs else {})
            self._got = {"token": rec.get("accessToken"), "expires": rec.get("expiresAt")}
        return self._got

    def __getitem__(self, key):
        return self._secret()[key]

    def __iter__(self):
        return iter(self._secret())

    def __len__(self):
        return len(self._secret())


def discover() -> list[Credential]:
    """One credential per account. An account signed in under several directories is read once,
    on whichever token is unexpired (or, failing that, any)."""
    found: dict[str, list[Path]] = {}
    for d in config_dirs():
        who = account_of(d)
        if who is not None:
            found.setdefault(who, []).append(d)
    return [Credential(who, _Token(dirs)) for who, dirs in sorted(found.items())]


# Anthropic's `limits` list names each limit by kind and group, not by window.
GROUP_MINUTES = {"session": 300, "weekly": 10080}


def _severity_limits(body: dict, now: datetime) -> list[dict]:
    """Anthropic's second vocabulary: the account's own severity for each limit it applies,
    and whether it is applying it now. Kept verbatim; no threshold is drawn on it here."""
    if body.get("limits") == []:
        # Asked, and the account names no limit it applies: its own word that nothing holds it,
        # which a missing list does not say.
        return [limit("limits:none", window_minutes=None, used_at_least=None, resets_at=None,
                      held=False, held_why=None, kind="none")]
    out = []
    for l in body.get("limits") if isinstance(body.get("limits"), list) else []:
        if not isinstance(l, dict):
            continue
        scope = l.get("scope") if isinstance(l.get("scope"), dict) else {}
        model = (scope.get("model") or {}).get("display_name") if isinstance(scope.get("model"), dict) else None
        name = f"limits:{l.get('kind')}" + (f":{model}" if model else "")
        resets = _iso(l.get("resets_at"))
        pct = l.get("percent")
        sev = l.get("severity")
        out.append(limit(name, window_minutes=GROUP_MINUTES.get(l.get("group")),
                         # A severity entry need not carry a reset; only a passed one voids its figure.
                         used_at_least=pct / 100 if isinstance(pct, (int, float)) and not isinstance(pct, bool)
                         and (resets is None or resets > now) else None,
                         resets_at=resets, held=None, severity=sev if isinstance(sev, str) else None,
                         active=l.get("is_active") if isinstance(l.get("is_active"), bool) else None,
                         kind=l.get("kind") if isinstance(l.get("kind"), str) else None))
    return out


def _money(m: object) -> tuple[float, str] | None:
    """A vendor money object, `{amount_minor, currency, exponent}`, as (major units, currency)."""
    if not isinstance(m, dict):
        return None
    minor, exp, cur = m.get("amount_minor"), m.get("exponent"), m.get("currency")
    if not all(isinstance(x, int) and not isinstance(x, bool) for x in (minor, exp)) \
            or not 0 <= exp <= 6 or not isinstance(cur, str):
        return None
    return minor / 10 ** exp, cur


def _credits(body: dict, now: datetime) -> dict | None:
    """The account's `spend`: what it may spend past its windows, and whether it is on."""
    s = body.get("spend")
    if not isinstance(s, dict) or not isinstance(s.get("enabled"), bool):
        return None
    used, limit, balance = (_money(s.get(k)) for k in ("used", "limit", "balance"))
    text = lambda k: s.get(k) if isinstance(s.get(k), str) else None
    return credits(now, enabled=s["enabled"], used=used[0] if used else None, limit=limit[0] if limit else None,
                   balance=balance[0] if balance else None,
                   currency=next((m[1] for m in (used, limit, balance) if m), None),
                   severity=text("severity"), disabled_reason=text("disabled_reason"),
                   can_purchase=s.get("can_purchase_credits") if isinstance(s.get("can_purchase_credits"), bool) else None)


def _limits(body: dict, now: datetime) -> list[dict]:
    out = []
    for name, w in body.items():
        if name == "extra_usage" or not isinstance(w, dict) or "utilization" not in w:
            continue
        resets = _iso(w.get("resets_at"))
        u = w.get("utilization")
        num = isinstance(u, (int, float)) and not isinstance(u, bool)
        # No reset and nothing used is a window that has not started: the vendor's own zero.
        unopened = resets is None and num and u == 0
        locked = w.get("locked_reason")
        out.append(limit(name, window_minutes=WINDOWS.get(name),
                         used_at_least=0.0 if unopened else u / 100 if num
                         and resets is not None and resets > now else None,
                         resets_at=resets, held=locked is not None,
                         held_why=str(locked) if locked is not None else None))
    return out


def _iso(v: object) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else None


def read(cred: Credential, now: datetime, get) -> dict:
    token = cred.secret.get("token")
    if token is None:
        return reading(VENDOR, cred.account, now, UNREAD, why="no-credential")
    if token == "":
        # Claude Code's own tombstone for a failed OAuth refresh (`invalid_grant`): the record
        # exists but says re-login is required, which is operationally different from finding
        # none at all.
        return reading(VENDOR, cred.account, now, UNREAD, why="signed-out")
    exp = _expiry({"expiresAt": cred.secret.get("expires")})
    if exp is not None and exp <= now:
        # Asking on an expired token answers 429, which reads like a real limit.
        return reading(VENDOR, cred.account, now, UNREAD, why="credential-expired")
    ans = get(URL, {"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20"}, now)
    if ans.body is None:
        return failed(VENDOR, cred.account, now, ans)
    # The plan is the organisation's current rate-limit tier. The keychain keeps the tier from
    # sign-in time, which goes stale on an upgrade, so it is asked for; a failure costs only it.
    prof = get(PROFILE_URL, {"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20"}, now)
    org = (prof.body or {}).get("organization") if isinstance((prof.body or {}).get("organization"), dict) else {}
    tier = org.get("rate_limit_tier") if isinstance(org.get("rate_limit_tier"), str) else None
    return reading(VENDOR, cred.account, now, OK, plan=tier,
                   limits=_limits(ans.body, now) + _severity_limits(ans.body, now),
                   credits=_credits(ans.body, now))


# ---- local: statusline captures ----------------------------------------------------------

def capture_dir() -> Path:
    from ..cache import default_dir
    return default_dir() / "claude-statusline"


def capture(payload: dict, now: datetime) -> dict | None:
    """Save the statusline's `rate_limits` for the account this Claude Code runs as. Returns the
    reading written, or None when the payload has none (API-key sign-in, or before the first
    response)."""
    rl = payload.get("rate_limits") if isinstance(payload, dict) else None
    d = Path(os.environ.get("CLAUDE_CONFIG_DIR") or _default_dir()).expanduser()
    who = account_of(d)
    if not isinstance(rl, dict) or who is None:
        return None
    out = []
    for name in ("five_hour", "seven_day"):
        w = rl.get(name)
        if not isinstance(w, dict):
            continue
        at, pct = w.get("resets_at"), w.get("used_percentage")
        try:
            resets = datetime.fromtimestamp(at, tz=timezone.utc) if isinstance(at, (int, float)) \
                and not isinstance(at, bool) else None
        except (OSError, OverflowError, ValueError):
            resets = None
        out.append(limit(name, window_minutes=WINDOWS[name],
                         used_at_least=pct / 100 if isinstance(pct, (int, float)) and not isinstance(pct, bool)
                         and resets is not None and resets > now else None,
                         resets_at=resets, held=None))
    if not out:
        return None
    r = reading(VENDOR, who, now, OK, limits=out, source="statusline")
    path = capture_dir()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path / f".{who}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(r, f)
    os.replace(tmp, path / f"{who}.json")
    return r


def local(now: datetime) -> list[dict]:
    out = []
    for f in capture_dir().glob("*.json"):
        try:
            r = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(r, dict) and r.get("vendor") == VENDOR and r.get("account"):
            out.append(r)
    return out
