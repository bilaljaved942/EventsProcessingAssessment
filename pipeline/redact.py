"""Remove personal data before anything is sent to the model.

Two independent layers, so neither depends on the other being right:

1. Field-based: if an event's *label* names a personal field ("Full name",
   "Phone", "Date of birth", "To", ...), its whole value is replaced. Those
   values are also remembered and scrubbed wherever they reappear in the same
   user's free text (e.g. a name typed in a form and later in a search box).
2. Content-based: emails, phone numbers and Luhn-valid card numbers are
   detected in any text, whatever the field label says.

Business identifiers (INV-2026-000417, PO-88213, ER-1182, bare order numbers
that fail Luhn) are preserved because no detector matches them: digit runs
attached to letters or hyphens are never treated as phone/card numbers, and
long bare digit runs are only redacted when they pass the Luhn check.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

EMAIL = "[EMAIL]"
PHONE = "[PHONE]"
CARD = "[CARD]"
REDACTED = "[REDACTED]"

EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# 13-19 digits, optionally separated by single spaces/dashes, not glued to a word or ID.
CARD_CANDIDATE_RE = re.compile(r"(?<![\w+-])\d(?:[ -]?\d){12,18}(?![\w-])")  # '+...' is a phone
# International: leading '+', 8-15 digits in total (E.164).
PHONE_INTL_RE = re.compile(r"(?<![\w+-])\+\d[\d ().-]{6,}\d(?![\w-])")
# North-American style with separators: (415) 555-0132, 415-555-0132, 415.555.0132, 415 555 0132.
PHONE_NANP_RE = re.compile(r"(?<![\w-])(?:\(\d{3}\)\s?|\d{3}[ .-])\d{3}[ .-]\d{4}(?![\w-])")

# Field labels whose values are personal data. Matched on whole words of the
# normalised label, so "Full name", "full_name", "FullName" and "Vendor name" all match.
SENSITIVE_WORDS = {
    "name", "firstname", "lastname", "fullname", "surname", "middlename",
    "email", "mail", "phone", "mobile", "telephone", "tel", "fax",
    "address", "street", "city", "zip", "zipcode", "postcode", "postal",
    "dob", "birthday", "birthdate", "ssn", "passport", "iban", "swift", "bic",
    "card", "cvv", "cvc", "password", "passcode", "pin", "salary",
    "nationality", "gender", "signature", "username", "login",
}
SENSITIVE_PHRASES = (
    "date of birth", "social security", "national id", "tax id", "account number",
    "bank account", "routing number", "first name", "last name", "full name",
)
# Short labels that are only sensitive as the *whole* label (email recipient fields).
SENSITIVE_EXACT = {"to", "cc", "bcc", "from", "recipient", "recipients", "sender", "contact"}

MIN_KNOWN_VALUE_LEN = 3


def luhn_valid(digits: str) -> bool:
    if not digits.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _normalise_label(label: str) -> str:
    # Split camelCase, then lowercase and collapse punctuation to spaces.
    label = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", label)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", label.lower()).split())


def is_sensitive_label(label: str) -> bool:
    norm = _normalise_label(label)
    if not norm:
        return False
    if norm in SENSITIVE_EXACT:
        return True
    words = set(norm.split())
    if words & SENSITIVE_WORDS or norm.replace(" ", "") in SENSITIVE_WORDS:
        return True
    return any(p in norm for p in SENSITIVE_PHRASES)


def _redact_cards(text: str) -> str:
    def repl(m: re.Match) -> str:
        parts = re.split(r"([ -])", m.group(0))  # groups at even indexes, separators at odd
        groups, seps = parts[0::2], parts[1::2]
        out: list[str] = []
        i = 0
        while i < len(groups):
            # Longest run of whole groups starting at i that is a Luhn-valid card number.
            hit = next(
                (j for j in range(len(groups), i, -1)
                 if 13 <= len("".join(groups[i:j])) <= 19 and luhn_valid("".join(groups[i:j]))),
                None,
            )
            if out:
                out.append(seps[i - 1])
            if hit:
                out.append(CARD)
                i = hit
            else:
                out.append(groups[i])
                i += 1
        return "".join(out)

    return CARD_CANDIDATE_RE.sub(repl, text)


def _redact_phones(text: str) -> str:
    def intl(m: re.Match) -> str:
        n = sum(c.isdigit() for c in m.group(0))
        return PHONE if 8 <= n <= 15 else m.group(0)

    text = PHONE_INTL_RE.sub(intl, text)
    return PHONE_NANP_RE.sub(PHONE, text)


def redact_text(text: str, known_values: Iterable[str] = ()) -> str:
    if not text:
        return text
    for value in sorted(set(known_values), key=len, reverse=True):
        if len(value) >= MIN_KNOWN_VALUE_LEN:
            text = re.sub(re.escape(value), REDACTED, text, flags=re.IGNORECASE)
    text = EMAIL_RE.sub(EMAIL, text)
    text = _redact_cards(text)  # before phones, so a card is never half-eaten as a phone
    text = _redact_phones(text)
    return text


def sensitive_values(events) -> set[str]:
    """Values typed into personal fields; scrubbed wherever else they appear."""
    return {
        e.text.strip() for e in events
        if is_sensitive_label(e.label) and len(e.text.strip()) >= MIN_KNOWN_VALUE_LEN
    }


@dataclass(frozen=True)
class RedactedEvent:
    offset_seconds: float
    app: str
    label: str
    text: str


def redact_events(events, known_values: Iterable[str] = ()) -> list[RedactedEvent]:
    events = list(events)
    known = set(known_values) | sensitive_values(events)
    if not events:
        return []
    t0 = events[0].ts
    return [
        RedactedEvent(
            offset_seconds=(e.ts - t0).total_seconds(),
            app=redact_text(e.app, known),
            label=redact_text(e.label, known),
            text=REDACTED if is_sensitive_label(e.label) and e.text.strip() else redact_text(e.text, known),
        )
        for e in events
    ]
