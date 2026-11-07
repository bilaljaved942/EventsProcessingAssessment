"""Turn untrusted model output into a validated LabelRecord, or raise.

Repairs are applied only when no information has to be invented:
  * JSON wrapped in prose        -> extract the JSON object
  * label like "Invoice Processing" -> normalise to "invoice_processing" if that is allowed
  * whitespace around steps      -> stripped; empty steps dropped
Everything else is rejected (and the caller retries with a new seed):
  * truncated / unparseable JSON (closing brackets can't recover a lost confidence)
  * label not in the allowed set even after normalisation
  * confidence missing, non-numeric, NaN/inf, or outside [0, 1] (1.7 is not "probably 1.0")
  * steps not a list of strings
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Iterable

MAX_STEPS = 50
MAX_STEP_LEN = 200


class ValidationError(ValueError):
    pass


@dataclass(frozen=True)
class LabelRecord:
    process: str
    steps: list[str]
    confidence: float

    def to_dict(self) -> dict:
        return {"process": self.process, "steps": list(self.steps), "confidence": self.confidence}


@dataclass
class Validated:
    record: LabelRecord
    repairs: list[str] = field(default_factory=list)


def extract_json_object(text: str) -> tuple[dict, bool]:
    """Return (first decodable JSON object in text, whether surrounding prose was stripped)."""
    if not isinstance(text, str) or not text.strip():
        raise ValidationError("empty or non-string output")
    stripped = text.strip()
    try:
        obj = json.loads(stripped)
        if isinstance(obj, dict):
            return obj, False
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    first_dict = None
    for i, ch in enumerate(stripped):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(stripped, i)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            if "process" in obj:
                return obj, True
            first_dict = first_dict or obj
    if first_dict is not None:
        return first_dict, True
    raise ValidationError("no complete JSON object found (truncated or not JSON)")


def normalise_process(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def validate_output(text: str, allowed: Iterable[str]) -> Validated:
    allowed = list(allowed)
    obj, stripped_prose = extract_json_object(text)
    repairs = ["extracted JSON from surrounding text"] if stripped_prose else []

    process = obj.get("process")
    if not isinstance(process, str):
        raise ValidationError("process missing or not a string")
    if process not in allowed:
        norm = normalise_process(process)
        if norm not in allowed:
            raise ValidationError(f"process {process!r} not in allowed set")
        repairs.append(f"normalised process {process!r} -> {norm!r}")
        process = norm

    steps = obj.get("steps")
    if not isinstance(steps, list) or not all(isinstance(s, str) for s in steps):
        raise ValidationError("steps must be a list of strings")
    clean = [s.strip()[:MAX_STEP_LEN] for s in steps if s.strip()]
    if len(clean) != len(steps):
        repairs.append("dropped empty steps")
    if len(clean) > MAX_STEPS:
        raise ValidationError(f"too many steps ({len(clean)})")

    conf = obj.get("confidence")
    if isinstance(conf, bool) or not isinstance(conf, (int, float)):
        raise ValidationError("confidence missing or not a number")
    if not math.isfinite(conf) or not 0.0 <= conf <= 1.0:
        raise ValidationError(f"confidence {conf!r} outside [0, 1]")

    return Validated(LabelRecord(process, clean, float(conf)), repairs)
