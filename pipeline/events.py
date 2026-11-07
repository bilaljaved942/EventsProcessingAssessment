"""Event model and loading from JSONL."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Event:
    ts: datetime
    user_id: str
    app: str
    label: str
    text: str


def parse_ts(value: str) -> datetime:
    ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    # Naive timestamps are assumed UTC so they can be compared with aware ones.
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _str(value) -> str:
    return "" if value is None else str(value)


def parse_event(raw: dict) -> Event:
    if not isinstance(raw, dict):
        raise TypeError("event is not an object")
    user_id = _str(raw["user_id"]).strip()
    if not user_id:
        raise ValueError("empty user_id")
    return Event(
        ts=parse_ts(raw["ts"]),
        user_id=user_id,
        app=_str(raw.get("app")),
        label=_str(raw.get("label")),
        text=_str(raw.get("text")),
    )


def load_events(path: str | Path) -> list[Event]:
    """Load events, skipping (and logging) malformed lines instead of crashing."""
    events: list[Event] = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                events.append(parse_event(json.loads(line)))
            except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
                # Log the line number only, never the content: it may contain personal data.
                log.warning("skipping malformed event on line %d: %s", lineno, type(e).__name__)
    return events
