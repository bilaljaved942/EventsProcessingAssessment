from datetime import datetime, timedelta, timezone

import pytest

from pipeline.events import Event
from pipeline.redact import (CARD, EMAIL, PHONE, REDACTED, is_sensitive_label, luhn_valid,
                             redact_events, redact_text)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def ev(label, text, i=0, app="App"):
    return Event(ts=T0 + timedelta(seconds=i), user_id="u", app=app, label=label, text=text)


# ---- Luhn -------------------------------------------------------------------

@pytest.mark.parametrize("n,ok", [
    ("4539148803436467", True),   # Visa test number
    ("4111111111111111", True),
    ("5555555555554444", True),
    ("378282246310005", True),    # Amex, 15 digits
    ("5432109876543210", False),
    ("4111111111111112", False),
    ("", False),
    ("abc", False),
])
def test_luhn(n, ok):
    assert luhn_valid(n) is ok


# ---- Content detectors --------------------------------------------------------

@pytest.mark.parametrize("text", [
    "mail jane.doe@example.com now",
    "cc: a.b+tag@sub.domain.co.uk",
    "UPPER@EXAMPLE.ORG",
])
def test_emails_redacted(text):
    out = redact_text(text)
    assert EMAIL in out and "@" not in out


@pytest.mark.parametrize("text", [
    "+91 98450 12345",
    "call +1 (415) 555-0132 today",
    "+44 20 7946 0958",
    "+4915112345678",
    "(415) 555-0132",
    "415-555-0132",
    "415.555.0132",
    "415 555 0132",
])
def test_phones_redacted(text):
    out = redact_text(text)
    assert PHONE in out
    assert not any(c.isdigit() for c in out)


@pytest.mark.parametrize("text", [
    "Charged to 4539 1488 0343 6467",
    "card 4111-1111-1111-1111 exp",
    "4111111111111111",
    "amex 3782 822463 10005",
])
def test_luhn_valid_cards_redacted(text):
    out = redact_text(text)
    assert CARD in out
    assert not any(c.isdigit() for c in out)


def test_card_followed_by_other_digits_still_redacted():
    # Greedy 13-19 digit match would swallow "2026" and fail Luhn; the card must still go.
    out = redact_text("4539 1488 0343 6467 2026")
    assert out == f"{CARD} 2026"


@pytest.mark.parametrize("text", [
    "Invoice INV-2026-000417",
    "Order 5432 1098 7654 3210 matched to PO-88213",   # 16 digits, fails Luhn
    "expense report ER-1182",
    "SO-2025-4111111111111111",                        # Luhn-valid digits inside an ID
    "PO#88213 and ticket 12345",
    "Order 1234567890",                                # bare 10 digits, unformatted
    "Total 1,234.56 EUR on 2026-11-02 at 09:14",
    "ref 2026-000417-AB",
])
def test_business_identifiers_survive(text):
    assert redact_text(text) == text


def test_redaction_is_idempotent():
    once = redact_text("x a@b.com +91 98450 12345 4539 1488 0343 6467")
    assert redact_text(once) == once


def test_known_values_scrubbed_case_insensitively():
    out = redact_text("Email from rahul mehta about INV-1", known_values={"Rahul Mehta"})
    assert out == f"Email from {REDACTED} about INV-1"


# ---- Field labels -------------------------------------------------------------

@pytest.mark.parametrize("label", [
    "Full name", "full_name", "FullName", "First Name", "Surname", "Vendor name",
    "Phone", "Mobile number", "E-mail", "Email address", "Date of birth", "DOB",
    "Home address", "IBAN", "Card", "Card number", "CVV", "Password", "SSN",
    "To", "cc", "Recipient",
])
def test_sensitive_labels(label):
    assert is_sensitive_label(label)


@pytest.mark.parametrize("label", [
    "Search", "Button", "Order", "Invoice number", "PO", "Amount", "Status",
    "Total", "Description", "Go to dashboard", "Notes", "",
])
def test_non_sensitive_labels(label):
    assert not is_sensitive_label(label)


def test_sensitive_field_value_fully_redacted_whatever_its_content():
    out = redact_events([ev("Full name", "Rahul Mehta"), ev("Date of birth", "1990-01-02", 1)])
    assert [e.text for e in out] == [REDACTED, REDACTED]


def test_sensitive_value_scrubbed_from_other_fields_in_same_events():
    out = redact_events([
        ev("Full name", "Rahul Mehta"),
        ev("Search", "vendor Rahul Mehta onboarding", 5),
    ])
    assert out[1].text == f"vendor {REDACTED} onboarding"


def test_non_sensitive_field_keeps_text_but_content_detectors_apply():
    out = redact_events([ev("Notes", "ping x@y.io re INV-9")])
    assert out[0].text == f"ping {EMAIL} re INV-9"


def test_offsets_relative_to_first_event():
    out = redact_events([ev("Search", "a"), ev("Search", "b", 90)])
    assert [e.offset_seconds for e in out] == [0, 90]
