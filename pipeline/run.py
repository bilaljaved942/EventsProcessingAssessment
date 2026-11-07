"""Wire the stages together: sessionize -> redact -> prompt -> label -> summary."""
from __future__ import annotations

import logging
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable

from .events import Event
from .label import (DEFAULT_MAX_ATTEMPTS, LabelOutcome, LLMClient, build_system_prompt,
                    build_user_prompt, fallback_record, label_with_retries)
from .redact import redact_events, sensitive_values
from .sessionize import Session, sessionize

log = logging.getLogger(__name__)


@dataclass
class SessionResult:
    session: Session
    prompt: str
    outcome: LabelOutcome

    def to_dict(self) -> dict:
        s, o = self.session, self.outcome
        return {
            "session_id": s.session_id,
            "user_id": s.user_id,
            "start": s.start.isoformat(),
            "end": s.end.isoformat(),
            "duration_seconds": s.duration_seconds,
            "event_count": len(s.events),
            **o.record.to_dict(),
            "attempts": o.attempts,
            "retried": o.retried,
            "fell_back": o.fell_back,
            "repairs": o.repairs,
            "errors": o.errors,
        }


def run_pipeline(
    events: Iterable[Event],
    client: LLMClient,
    allowed: Iterable[str],
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_seed: int = 0,
) -> list[SessionResult]:
    events = list(events)
    allowed = list(allowed)
    system = build_system_prompt(allowed)

    # Personal values typed by a user in one session are scrubbed from all their sessions.
    by_user: dict[str, list[Event]] = defaultdict(list)
    for e in events:
        by_user[e.user_id].append(e)
    known_by_user = {u: sensitive_values(evs) for u, evs in by_user.items()}

    results: list[SessionResult] = []
    for session in sessionize(events):
        known = known_by_user.get(session.user_id, set())
        try:
            user = build_user_prompt(redact_events(session.events, known), known)
        except Exception as e:  # never send anything if redaction itself failed
            log.error("redaction failed for %s: %s", session.session_id, type(e).__name__)
            outcome = LabelOutcome(fallback_record(), 0, True, [], [f"redaction failed: {type(e).__name__}"])
            results.append(SessionResult(session, "", outcome))
            continue
        outcome = label_with_retries(system, user, client, allowed, max_attempts, base_seed)
        results.append(SessionResult(session, user, outcome))
    return results


def _fmt_duration(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60}m {s % 60:02d}s"


def summarise(results: list[SessionResult], allowed: Iterable[str]) -> dict:
    per_process = Counter(r.outcome.record.process for r in results)
    durations = [r.session.duration_seconds for r in results]
    return {
        "sessions": len(results),
        "per_process": {p: per_process.get(p, 0) for p in dict.fromkeys([*allowed, *per_process])},
        "median_duration_seconds": statistics.median(durations) if durations else 0.0,
        "retried": sum(r.outcome.retried for r in results),
        "fell_back_to_unknown": sum(r.outcome.fell_back for r in results),
        "repaired_without_retry": sum(bool(r.outcome.repairs) and r.outcome.attempts == 1 for r in results),
    }


def format_summary(summary: dict) -> str:
    lines = [f"Sessions: {summary['sessions']}", "Sessions per process:"]
    lines += [f"  {p:<22} {n}" for p, n in summary["per_process"].items()]
    lines += [
        f"Median session duration: {_fmt_duration(summary['median_duration_seconds'])}",
        f"Needed a retry: {summary['retried']}",
        f"Fell back to unknown: {summary['fell_back_to_unknown']}",
        f"Repaired on first attempt (no retry): {summary['repaired_without_retry']}",
    ]
    return "\n".join(lines)
