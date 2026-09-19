"""One HTTP result shape for every adapter, so backoff does not depend on which vendor refused."""

from __future__ import annotations

import email.utils
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class Answer:
    body: dict | None          # the parsed JSON object, when there is one
    status: int | None         # HTTP status; None when no response arrived
    why: str | None            # fixed-vocabulary failure word; never carries request content
    retry_until: datetime | None = None


USER_AGENT = "unlimited (+https://github.com/endario/unlimited)"

# A refusal never blocks for longer than this, so a bad header or a clock jump cannot stop reads
# indefinitely.
MAX_BACKOFF = timedelta(hours=24)


def retry_until(value: str | None, now: datetime) -> datetime | None:
    """`Retry-After` as a deadline: delta-seconds or an HTTP-date (RFC 9110 §10.2.3)."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        t = now + timedelta(seconds=min(int(value), int(MAX_BACKOFF.total_seconds())))
    else:
        try:
            t = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
        t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    return min(t, now + MAX_BACKOFF)


def get(url: str, headers: dict[str, str], now: datetime, timeout: float = 20) -> Answer:
    # Python-urllib's default User-Agent is refused by some vendors' edges (opencode.ai answers
    # it 403), so every request names this tool.
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT,
                                               **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return Answer(None, e.code, f"http-{e.code}", retry_until(e.headers.get("Retry-After"), now))
    except (urllib.error.URLError, OSError):
        return Answer(None, None, "unreachable")
    except ValueError:
        return Answer(None, 200, "not-json")
    if not isinstance(body, dict):
        return Answer(None, 200, "not-an-object")
    return Answer(body, 200, None)
