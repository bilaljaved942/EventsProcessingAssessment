from datetime import datetime, timedelta, timezone
from pathlib import Path

from pipeline.events import Event, load_events
from pipeline.sessionize import sessionize

T0 = datetime(2026, 11, 2, 9, 0, 0, tzinfo=timezone.utc)
DATA = Path(__file__).resolve().parent.parent / "data" / "events.jsonl"


def ev(offset: timedelta, user="u1", app="NetSuite", text="x"):
    return Event(ts=T0 + offset, user_id=user, app=app, label="Button", text=text)


def sizes(sessions):
    return [len(s.events) for s in sessions]


def test_gap_just_under_10_minutes_stays_in_session():
    s = sessionize([ev(timedelta(0)), ev(timedelta(minutes=9, seconds=59))])
    assert sizes(s) == [2]


def test_gap_of_exactly_10_minutes_starts_new_session():
    s = sessionize([ev(timedelta(0)), ev(timedelta(minutes=10))])
    assert sizes(s) == [1, 1]


def test_gap_over_10_minutes_starts_new_session():
    s = sessionize([ev(timedelta(0)), ev(timedelta(minutes=10, seconds=1))])
    assert sizes(s) == [1, 1]


def test_gap_measured_from_previous_event_not_session_start():
    # Events every 9 minutes for 45 minutes: never 10 min idle -> one session.
    s = sessionize([ev(timedelta(minutes=9 * i)) for i in range(6)])
    assert sizes(s) == [6]


def test_app_change_alone_does_not_split():
    s = sessionize([
        ev(timedelta(0), app="NetSuite"),
        ev(timedelta(minutes=3), app="Outlook"),
        ev(timedelta(minutes=6), app="NetSuite"),
    ])
    assert sizes(s) == [3]
    assert s[0].apps == ["NetSuite", "Outlook"]


def test_users_are_sessionized_independently():
    # Interleaved users within seconds of each other must not share a session.
    s = sessionize([
        ev(timedelta(0), user="u1"),
        ev(timedelta(seconds=30), user="u2"),
        ev(timedelta(minutes=1), user="u1"),
    ])
    assert {(x.user_id, len(x.events)) for x in s} == {("u1", 2), ("u2", 1)}


def test_unsorted_input_is_sorted_before_splitting():
    s = sessionize([ev(timedelta(minutes=20)), ev(timedelta(0)), ev(timedelta(minutes=5))])
    assert sizes(s) == [2, 1]
    assert s[0].start == T0


def test_empty_input():
    assert sessionize([]) == []


def test_duration_and_ids():
    s = sessionize([ev(timedelta(0)), ev(timedelta(minutes=4)), ev(timedelta(minutes=30))])
    assert [x.session_id for x in s] == ["u1-s1", "u1-s2"]
    assert s[0].duration_seconds == 240
    assert s[1].duration_seconds == 0


def test_sample_data_splits_into_expected_sessions():
    sessions = sessionize(load_events(DATA))
    # u1: invoice (09:00-09:14:59), then exactly 10:00 idle -> vendor onboarding; u2: expense.
    assert [(x.session_id, len(x.events)) for x in sessions] == [
        ("u1-s1", 4), ("u1-s2", 1), ("u1-s3", 3), ("u2-s1", 3),
    ]


def test_malformed_lines_are_skipped(tmp_path):
    p = tmp_path / "e.jsonl"
    p.write_text(
        '{"ts":"2026-11-02T09:00:00Z","user_id":"u1","app":"A","label":"B","text":"c"}\n'
        "not json\n"
        '{"user_id":"u1"}\n'
        "\n"
    )
    assert len(load_events(p)) == 1
