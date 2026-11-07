import json

from main import mock_client
from mock_llm import ALLOWED
from pipeline.label import label_with_retries
from pipeline.validate import validate_output

GOOD = json.dumps({"process": "expense_review", "steps": ["a"], "confidence": 0.9})


def scripted(*responses):
    """Fake client returning responses in order; records the seeds it was called with."""
    calls = []

    def client(system, user, seed):
        calls.append(seed)
        r = responses[len(calls) - 1]
        if isinstance(r, Exception):
            raise r
        return r

    client.calls = calls
    return client


def test_first_attempt_ok():
    o = label_with_retries("s", "u", scripted(GOOD), ALLOWED)
    assert (o.record.process, o.attempts, o.retried, o.fell_back) == ("expense_review", 1, False, False)


def test_retry_uses_new_seed_and_succeeds():
    client = scripted(GOOD[:-6], GOOD)
    o = label_with_retries("s", "u", client, ALLOWED, base_seed=10)
    assert client.calls == [10, 11]
    assert o.retried and not o.fell_back
    assert o.record.process == "expense_review"
    assert len(o.errors) == 1


def test_all_attempts_bad_falls_back_to_unknown():
    bad = json.dumps({"process": "payroll", "steps": [], "confidence": 0.5})
    o = label_with_retries("s", "u", scripted(bad, "garbage", '{"process":'), ALLOWED, max_attempts=3)
    assert o.fell_back
    assert o.record.to_dict() == {"process": "unknown", "steps": [], "confidence": 0.0}
    assert o.attempts == 3 and len(o.errors) == 3


def test_client_exception_is_retried_not_raised():
    o = label_with_retries("s", "u", scripted(TimeoutError("boom"), GOOD), ALLOWED)
    assert o.record.process == "expense_review" and o.retried


def test_client_always_raising_falls_back():
    o = label_with_retries("s", "u", scripted(*[RuntimeError("x")] * 3), ALLOWED)
    assert o.fell_back and o.record.process == "unknown"


def test_client_returning_non_string_falls_back():
    o = label_with_retries("s", "u", scripted(None, 123, {"process": "x"}), ALLOWED)
    assert o.fell_back


def test_real_mock_always_yields_valid_record_across_many_seeds_and_prompts():
    # The real mock is random per (seed, prompt); whatever it does, the outcome must be valid.
    prompts = [f"event {i} vendor" for i in range(50)] + [f"event {i} expense" for i in range(50)] \
        + [f"event {i}" for i in range(50)]
    retried = fell_back = 0
    for i, p in enumerate(prompts):
        o = label_with_retries("system", p, mock_client, ALLOWED, base_seed=i * 100)
        r = o.record
        assert r.process in ALLOWED
        assert isinstance(r.steps, list) and all(isinstance(s, str) for s in r.steps)
        assert 0.0 <= r.confidence <= 1.0
        retried += o.retried
        fell_back += o.fell_back
    assert retried > 0  # the mock does misbehave, so the retry path was exercised


def test_mock_raw_outputs_cover_every_failure_mode():
    # Sanity check that our validator sees each kind of mock misbehaviour at least once.
    kinds = set()
    for seed in range(400):
        raw = mock_client("", "some prompt", seed)
        if not raw.lstrip().startswith("{"):
            kinds.add("prose")
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            if raw.lstrip().startswith("{"):
                kinds.add("truncated")
            continue
        if obj["process"] not in ALLOWED:
            kinds.add("bad_label")
        if not 0 <= obj["confidence"] <= 1:
            kinds.add("bad_confidence")
        validate_output(raw, ALLOWED) if obj["process"] in ALLOWED and obj["confidence"] <= 1 else None
    assert kinds == {"prose", "truncated", "bad_label", "bad_confidence"}
