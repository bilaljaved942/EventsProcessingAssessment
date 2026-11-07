"""End-to-end tests: sample data, a privacy spy, and randomly generated event streams."""
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from main import mock_client
from mock_llm import ALLOWED
from pipeline.events import Event, load_events
from pipeline.redact import luhn_valid
from pipeline.run import format_summary, run_pipeline, summarise

DATA = Path(__file__).resolve().parent.parent / "data" / "events.jsonl"


class Spy:
    """Wraps a client and records everything that would be sent to the model."""

    def __init__(self, inner=mock_client):
        self.inner = inner
        self.sent: list[str] = []

    def __call__(self, system, user, seed):
        self.sent.append(system + "\n" + user)
        return self.inner(system, user, seed)


# ---- Sample data ----------------------------------------------------------------

def test_sample_pipeline_end_to_end():
    spy = Spy()
    results = run_pipeline(load_events(DATA), spy, ALLOWED)
    assert [r.session.session_id for r in results] == ["u1-s1", "u1-s2", "u1-s3", "u2-s1"]
    for r in results:
        assert r.outcome.record.process in ALLOWED
        assert 0 <= r.outcome.record.confidence <= 1

    sent = "\n".join(spy.sent)
    for pii in ["ap-team@acme-example.com", "Rahul Mehta", "Rahul", "98450", "12345",
                "4539 1488 0343 6467", "4539"]:
        assert pii not in sent, pii
    for keep in ["INV-2026-000417", "PO-88213", "ER-1182", "5432 1098 7654 3210"]:
        assert keep in sent, keep


@pytest.mark.parametrize("seed", range(20))
def test_sample_summary_is_consistent_for_any_seed(seed):
    results = run_pipeline(load_events(DATA), mock_client, ALLOWED, base_seed=seed)
    s = summarise(results, ALLOWED)
    assert s["sessions"] == 4
    assert sum(s["per_process"].values()) == 4
    assert s["median_duration_seconds"] == pytest.approx((285 + 332) / 2)
    assert 0 <= s["fell_back_to_unknown"] <= s["retried"] <= 4
    assert "Median session duration: 5m 08s" in format_summary(s)


def test_pipeline_survives_a_client_that_always_fails():
    def broken(system, user, seed):
        raise ConnectionError("model down")

    results = run_pipeline(load_events(DATA), broken, ALLOWED)
    s = summarise(results, ALLOWED)
    assert s["fell_back_to_unknown"] == s["sessions"] == 4
    assert s["per_process"]["unknown"] == 4


def test_empty_input():
    s = summarise(run_pipeline([], mock_client, ALLOWED), ALLOWED)
    assert s["sessions"] == 0 and s["median_duration_seconds"] == 0


def test_name_typed_in_one_session_is_scrubbed_from_users_later_sessions():
    t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    events = [
        Event(t, "u", "CRM", "Full name", "Priya Sharma"),
        Event(t + timedelta(hours=2), "u", "CRM", "Search", "priya sharma invoices"),
    ]
    spy = Spy()
    run_pipeline(events, spy, ALLOWED)
    assert "priya" not in "\n".join(spy.sent).lower()


# ---- Random event streams -------------------------------------------------------

def make_card(rnd: random.Random) -> str:
    while True:
        n = "4" + "".join(rnd.choice("0123456789") for _ in range(15))
        if luhn_valid(n):
            return " ".join(n[i:i + 4] for i in range(0, 16, 4))


def make_non_card(rnd: random.Random) -> str:
    while True:
        n = "".join(rnd.choice("123456789") for _ in range(16))
        if not luhn_valid(n):
            return " ".join(n[i:i + 4] for i in range(0, 16, 4))


FIRST = ["Aisha", "Bruno", "Chen", "Dmitri", "Elena", "Farah", "Goran", "Hiro"]
LAST = ["Okafor", "Silva", "Wang", "Petrov", "Rossi", "Khan", "Novak", "Tanaka"]
APPS = ["SAP", "Workday", "Salesforce", "Excel", "Outlook", "Jira", "Zendesk"]


def random_stream(seed: int):
    """Events with PII planted in both sensitive fields and free text, plus IDs that must survive."""
    rnd = random.Random(seed)
    pii, ids, events = set(), set(), []
    for u in range(rnd.randint(1, 5)):
        user = f"user-{seed}-{u}"
        t = datetime(2026, 3, 1, 8, tzinfo=timezone.utc) + timedelta(minutes=rnd.randint(0, 600))
        for _ in range(rnd.randint(1, 25)):
            t += timedelta(seconds=rnd.choice([5, 30, 120, 400, 599, 600, 601, 3600]))
            name = f"{rnd.choice(FIRST)} {rnd.choice(LAST)}"
            email = f"{name.split()[0].lower()}.{rnd.randint(1, 999)}@corp-{rnd.randint(1, 9)}.example"
            phone = rnd.choice([f"+{rnd.randint(1, 99)} {rnd.randint(10000, 99999)} {rnd.randint(10000, 99999)}",
                                f"({rnd.randint(200, 999)}) {rnd.randint(200, 999)}-{rnd.randint(1000, 9999)}"])
            card = make_card(rnd)
            ident = rnd.choice([f"INV-{rnd.randint(2000, 2030)}-{rnd.randint(1, 999999):06d}",
                                f"PO-{rnd.randint(10000, 99999)}", f"TCK-{rnd.randint(1, 9999)}",
                                make_non_card(rnd)])
            label, text = rnd.choice([
                ("Full name", name), ("Customer name", name), ("Phone", phone), ("Email", email),
                ("To", email), ("Card number", card),
                ("Notes", f"spoke to {email} on {phone} about {ident}"),
                ("Comment", f"paid with {card} ref {ident}"),
                ("Search", f"{ident}"), ("Button", "Save"),
            ])
            if label in ("Full name", "Customer name"):
                pii.update([name])
            for value in (email, phone, card):
                if value in text:
                    pii.add(value)
            if ident in text:
                ids.add(ident)
            events.append(Event(t, user, rnd.choice(APPS), label, text))
    rnd.shuffle(events)  # input order must not matter
    return events, pii, ids


@pytest.mark.parametrize("seed", range(40))
def test_random_streams_never_crash_never_leak_and_keep_ids(seed):
    events, pii, ids = random_stream(seed)
    spy = Spy()
    results = run_pipeline(events, spy, ALLOWED, base_seed=seed)

    # Every event lands in exactly one session, sessions are per user and gap-respecting.
    assert sum(len(r.session.events) for r in results) == len(events)
    for r in results:
        evs = r.session.events
        assert len({e.user_id for e in evs}) == 1
        assert all(b.ts - a.ts < timedelta(minutes=10) for a, b in zip(evs, evs[1:]))
        rec = r.outcome.record
        assert rec.process in ALLOWED and 0 <= rec.confidence <= 1

    sent = "\n".join(spy.sent)
    for value in pii:
        assert value.lower() not in sent.lower(), value
    assert not re.search(r"[\w.]+@[\w-]+\.\w+", sent)
    for ident in ids:
        assert ident in sent, ident

    format_summary(summarise(results, ALLOWED))  # must not raise
