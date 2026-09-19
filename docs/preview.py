"""Regenerates the README's previews from fixed readings:
    TZ=UTC uv run --with rich python docs/preview.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from rich.console import Console  # noqa: E402
from rich.text import Text  # noqa: E402

from unlimited import projection, show  # noqa: E402
from unlimited.schema import limit, reading  # noqa: E402

NOW = datetime(2026, 9, 21, 14, 0, tzinfo=timezone.utc)
H, D = timedelta(hours=1), timedelta(days=1)


def win(account, name, minutes, resets, pairs, past=()):
    """History for one window: (time before NOW, used) pairs, plus past windows' curves."""
    samples = [[(NOW - ago).isoformat(), u, resets.isoformat()] for ago, u in pairs]
    return {f"{account}\t{name}": {"samples": samples, "window_minutes": minutes, "past": [
        {"resets_at": (resets - timedelta(minutes=minutes) * (len(past) - i)).isoformat(),
         "curve": [round(f(x / projection.GRID), 4) for x in range(projection.GRID + 1)]}
        for i, f in enumerate(past)]}}


claude_week, codex_week = NOW + 3 * D + 21 * H, NOW + 2 * D + 5 * H
history = {
    **win("work", "five_hour", 300, NOW + 2 * H, [(2 * H, 0.08), (H, 0.2), (0 * H, 0.31)]),
    **win("work", "seven_day", 10080, claude_week, [(D, 0.33), (0 * H, 0.41)],
          past=[lambda x: min(0.95 * x ** 1.1, 1)] * 5),
    **win("codex", "codex", 10080, codex_week, [(D, 0.45), (6 * H, 0.62), (0 * H, 0.7)]),
}
readings = [
    reading("anthropic", "work", NOW, "ok", plan="default_claude_max_20x", limits=[
        limit("five_hour", window_minutes=300, used_at_least=0.31, resets_at=NOW + 2 * H, held=False),
        limit("seven_day", window_minutes=10080, used_at_least=0.41, resets_at=claude_week, held=False)]),
    reading("openai", "codex", NOW - 7 * 60 * timedelta(seconds=1), "ok", plan="pro", source="session-log", limits=[
        limit("codex", window_minutes=10080, used_at_least=0.7, resets_at=codex_week, held=False)]),
    dict(reading("zai", "glm", NOW - 12 * 60 * timedelta(seconds=1), "ok", plan="max", limits=[
        limit("five_hour", window_minutes=300, used_at_least=0.92, resets_at=NOW + 40 * 60 * timedelta(seconds=1),
              held=True, held_why="limit_reached")]),
         retry_until=(NOW + 3 * 60 * timedelta(seconds=1)).isoformat()),
]
readings = [projection.attach(r, history) for r in readings]
show._claude_dirs = lambda: {"work": "work"}

out = Path(__file__).parent
console = Console(record=True, width=104, file=open("/dev/null", "w"))
console.print(Text.from_ansi("$ unlimited\n" + show.render(readings, NOW, color=True).rstrip()))
(out / "status.svg").write_text(console.export_svg(title="unlimited"))
print(json.dumps([dict(readings[0], limits=readings[0]["limits"][1:])], indent=2))  # the README's JSON
