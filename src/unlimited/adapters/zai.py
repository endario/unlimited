"""Z.ai GLM coding plan: `quota/limit` on each API key the claude-glm wrappers read."""

from __future__ import annotations

from datetime import datetime, timezone

from ..credential import Credential, EnvKeys, env_value
from ..schema import OK, REFUSED, UNREAD, failed, limit, reading

VENDOR = "zai"
URL = "https://api.z.ai/api/monitor/usage/quota/limit"
# The customer the key belongs to: for a team plan's key, the member holding the seat.
WHOAMI_URL = "https://api.z.ai/api/biz/customerService/zaiUserInfo"
# A team plan's usage: the same query asked as a team, in the team's organisation and project.
TEAM_URL = URL + "?type=2"
# Z.ai's mainland China site, bigmodel.cn, answers the same usage query on its own host; an env
# file's GLM_HOST names it. It has no zaiUserInfo, so its member is the invitation code.
INVITE_URL = "https://api.z.ai/api/biz/invitation/info"
HOST = "api.z.ai"
# Minutes per Z.ai unit: 3 is hours, 6 is the plan's week. An unknown unit keeps no length.
UNIT_MINUTES = {3: 60, 6: 10080}
NAMES = {300: "five_hour", 10080: "seven_day"}


# A GLM session exports its own file and key; neither may narrow discovery to that one account.
KEYS = EnvKeys("GLM_API_KEY", "claude-glm*.env", named="CLAUDE_GLM_ENV")
names = KEYS.names


def discover() -> list[Credential]:
    """Each key's credential, carrying the team organisation and project its env file names."""
    extra = {}
    for f in KEYS.files():
        key, org, project, host = (env_value(f, v) for v in
                                   (KEYS.var, "GLM_ORGANIZATION_ID", "GLM_PROJECT_ID", "GLM_HOST"))
        if not key:
            continue
        extra[key] = ({"organization": org, "project": project} if org and project else {}) | (
            {"host": host} if host and host != HOST else {})
    return [Credential(c.account, {**c.secret, **extra[c.secret["key"]]}) if extra.get(c.secret["key"]) else c
            for c in KEYS.discover()]


def _on(cred: Credential, url: str) -> str:
    return url.replace(HOST, cred.secret.get("host", HOST), 1)


def _usage(cred: Credential) -> tuple[str, dict]:
    headers = {"Authorization": f"Bearer {cred.secret['key']}"}
    if "organization" not in cred.secret:
        return _on(cred, URL), headers
    return _on(cred, TEAM_URL), {**headers, "Bigmodel-Organization": cred.secret["organization"],
                      "Bigmodel-Project": cred.secret["project"]}


def _millis(value: object) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def _num(x: object) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def limits(body: dict, now: datetime) -> list[dict]:
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    out = []
    for l in data.get("limits") or []:
        if not isinstance(l, dict):
            continue
        unit, number = l.get("unit"), l.get("number")
        minutes = (UNIT_MINUTES[unit] * number if unit in UNIT_MINUTES
                   and isinstance(number, int) and not isinstance(number, bool) else None)
        resets = _millis(l.get("nextResetTime"))
        total, spent, pct = l.get("usage"), l.get("currentValue"), l.get("percentage")
        kind = l.get("type") if isinstance(l.get("type"), str) else None
        if resets is None and kind in ("TOKENS_LIMIT", "CREDIT_LIMIT") and (
                (_num(pct) and pct == 0) or (_num(spent) and spent == 0)):
            # A usage window nothing has opened yet is answered with no reset time, on either
            # plan type: token or credit.
            used = 0.0
        elif resets is None or resets <= now:
            used = None
        elif _num(total) and _num(spent) and total > 0:
            used = spent / total
        else:
            # Some accounts are answered in TOKENS_LIMIT with only a percentage, whatever the plan.
            used = pct / 100 if _num(pct) and 0 <= pct <= 100 else None
        # Z.ai has no word for a held limit short of the 429 at the cap, so `held` is unknown.
        # Credit and token limits are usage windows; a TIME_LIMIT is the monthly tool quota.
        name = NAMES.get(minutes) if kind in ("CREDIT_LIMIT", "TOKENS_LIMIT") else None
        out.append(limit(name or f"{str(kind).lower()} {unit}x{number}", window_minutes=minutes,
                         used_at_least=used, resets_at=resets, held=None, kind=kind))
    return out


def whoami(cred: Credential, now: datetime, get):
    """The key's customer number (on another host, its invitation code), and nothing personal."""
    url, field = (WHOAMI_URL, "customerNumber") if "host" not in cred.secret else (_on(cred, INVITE_URL), "userCode")
    ans = get(url, {"Authorization": f"Bearer {cred.secret['key']}"}, now)
    data = ans.body.get("data") if isinstance(ans.body, dict) else None
    number = data.get(field) if isinstance(data, dict) else None
    return (number if isinstance(number, str) and number else None), ans


def read(cred: Credential, now: datetime, get) -> dict:
    ans = get(*_usage(cred), now)
    if ans.body is None:
        return failed(VENDOR, cred.account, now, ans)
    if ans.body.get("success") is False:
        code = ans.body.get("code")
        return reading(VENDOR, cred.account, now, REFUSED,
                       why=f"vendor-{code}" if isinstance(code, int) else "vendor-refused")
    found = limits(ans.body, now)
    if not found:
        # An empty answer is unknown, never "nothing used". Z.ai answers success with empty data
        # for a team key sent without its organisation headers.
        return reading(VENDOR, cred.account, now, UNREAD, why="no-limits")
    level = (ans.body.get("data") or {}).get("level") if isinstance(ans.body.get("data"), dict) else None
    return reading(VENDOR, cred.account, now, OK, limits=found, plan=level if isinstance(level, str) else None)
