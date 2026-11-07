"""Group each user's events into sessions.

Rule: a new session starts when the gap since the user's previous event is
>= INACTIVITY_GAP (10 minutes). Exactly 10:00 of inactivity counts as a break.

No app-context rule: in the sample data a single invoice task spans
NetSuite -> Outlook -> NetSuite, so splitting on app change would fragment
real processes. See README for details.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from .events import Event

INACTIVITY_GAP = timedelta(minutes=10)


@dataclass
class Session:
    session_id: str
    user_id: str
    events: list[Event] = field(default_factory=list)

    @property
    def start(self):
        return self.events[0].ts

    @property
    def end(self):
        return self.events[-1].ts

    @property
    def duration_seconds(self) -> float:
        return (self.end - self.start).total_seconds()

    @property
    def apps(self) -> list[str]:
        return list(dict.fromkeys(e.app for e in self.events))


def sessionize(events: list[Event], gap: timedelta = INACTIVITY_GAP) -> list[Session]:
    by_user: dict[str, list[Event]] = defaultdict(list)
    for e in events:
        by_user[e.user_id].append(e)

    sessions: list[Session] = []
    for user_id in sorted(by_user):
        user_events = sorted(by_user[user_id], key=lambda e: e.ts)  # stable sort
        current: Session | None = None
        n = 0
        for e in user_events:
            if current is None or e.ts - current.end >= gap:
                n += 1
                current = Session(session_id=f"{user_id}-s{n}", user_id=user_id)
                sessions.append(current)
            current.events.append(e)
    return sessions
