"""Prompt building and labelling with validation, retries and fallback."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Iterable

from .redact import RedactedEvent, redact_text
from .validate import LabelRecord, ValidationError, validate_output

log = logging.getLogger(__name__)

FALLBACK_PROCESS = "unknown"
DEFAULT_MAX_ATTEMPTS = 3

# (system, user, seed) -> raw model text
LLMClient = Callable[[str, str, int], str]


def build_system_prompt(allowed: Iterable[str]) -> str:
    labels = ", ".join(allowed)
    return (
        "You label a user's work session from a list of UI events.\n"
        f"Choose process from exactly one of: {labels}. Use \"{FALLBACK_PROCESS}\" if unsure.\n"
        "Reply with only a JSON object: "
        '{"process": string, "steps": [short step strings], "confidence": number between 0 and 1}.\n'
        "Values shown as [EMAIL], [PHONE], [CARD] or [REDACTED] were removed for privacy."
    )


def _fmt_offset(seconds: float) -> str:
    s = int(seconds)
    return f"[{s // 60:02d}:{s % 60:02d}]"


def build_user_prompt(events: list[RedactedEvent], known_values: Iterable[str] = ()) -> str:
    lines = ["Session events (offset from start | app | field | value):"]
    lines += [f"{_fmt_offset(e.offset_seconds)} | {e.app} | {e.label} | {e.text}" for e in events]
    # Defence in depth: run the content detectors over the final prompt text too.
    return redact_text("\n".join(lines), known_values)


@dataclass
class LabelOutcome:
    record: LabelRecord
    attempts: int
    fell_back: bool
    repairs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def retried(self) -> bool:
        return self.attempts > 1


def fallback_record() -> LabelRecord:
    return LabelRecord(FALLBACK_PROCESS, [], 0.0)


def label_with_retries(
    system: str,
    user: str,
    client: LLMClient,
    allowed: Iterable[str],
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_seed: int = 0,
) -> LabelOutcome:
    """Call the model up to max_attempts times with different seeds. Never raises."""
    allowed = list(allowed)
    errors: list[str] = []
    for attempt in range(max(1, max_attempts)):
        seed = base_seed + attempt
        try:
            raw = client(system, user, seed)
            v = validate_output(raw, allowed)
            return LabelOutcome(v.record, attempt + 1, False, v.repairs, errors)
        except ValidationError as e:
            errors.append(f"seed {seed}: invalid output: {e}")
        except Exception as e:  # the model client itself failed; treat like a bad answer
            errors.append(f"seed {seed}: client error: {type(e).__name__}: {e}")
        log.info("label attempt %d failed: %s", attempt + 1, errors[-1])
    return LabelOutcome(fallback_record(), max(1, max_attempts), True, [], errors)
