import json

import pytest

from mock_llm import ALLOWED
from pipeline.validate import ValidationError, validate_output

GOOD = {"process": "invoice_processing", "steps": ["open record", "review details", "submit"],
        "confidence": 0.82}


def dumps(**overrides):
    return json.dumps({**GOOD, **overrides})


def test_clean_json():
    v = validate_output(json.dumps(GOOD), ALLOWED)
    assert v.record.to_dict() == GOOD
    assert v.repairs == []


def test_json_wrapped_in_prose_is_extracted():
    v = validate_output("Sure! Here is the JSON you asked for:\n" + json.dumps(GOOD) + "\nHope it helps", ALLOWED)
    assert v.record.process == "invoice_processing"
    assert v.repairs


def test_json_in_markdown_fence():
    v = validate_output("```json\n" + json.dumps(GOOD) + "\n```", ALLOWED)
    assert v.record.confidence == 0.82


def test_prose_with_stray_brace_before_json():
    v = validate_output("Format {like this}: " + json.dumps(GOOD), ALLOWED)
    assert v.record.process == "invoice_processing"


@pytest.mark.parametrize("cut", [1, 6, 20, len(json.dumps(GOOD)) - 1])
def test_truncated_json_rejected(cut):
    with pytest.raises(ValidationError):
        validate_output(json.dumps(GOOD)[:-cut], ALLOWED)


@pytest.mark.parametrize("label,expected", [
    ("Invoice Processing", "invoice_processing"),
    ("VENDOR-ONBOARDING", "vendor_onboarding"),
    ("  expense review ", "expense_review"),
])
def test_label_variants_normalised(label, expected):
    v = validate_output(dumps(process=label), ALLOWED)
    assert v.record.process == expected
    assert any("normalised" in r for r in v.repairs)


@pytest.mark.parametrize("label", ["payroll", "invoice", "", "Invoice Processing Extra"])
def test_label_outside_allowed_set_rejected(label):
    with pytest.raises(ValidationError):
        validate_output(dumps(process=label), ALLOWED)


@pytest.mark.parametrize("conf", [1.7, -0.1, 82, 1.0000001])
def test_out_of_range_confidence_rejected(conf):
    with pytest.raises(ValidationError):
        validate_output(dumps(confidence=conf), ALLOWED)


@pytest.mark.parametrize("conf", [0, 1, 0.0, 1.0, 0.5])
def test_boundary_confidence_accepted(conf):
    assert validate_output(dumps(confidence=conf), ALLOWED).record.confidence == float(conf)


@pytest.mark.parametrize("conf", ["0.8", None, True, [0.8]])
def test_non_numeric_confidence_rejected(conf):
    with pytest.raises(ValidationError):
        validate_output(dumps(confidence=conf), ALLOWED)


@pytest.mark.parametrize("raw", ['{"process":"unknown","steps":[],"confidence":NaN}',
                                 '{"process":"unknown","steps":[],"confidence":Infinity}'])
def test_nan_and_inf_rejected(raw):
    with pytest.raises(ValidationError):
        validate_output(raw, ALLOWED)


@pytest.mark.parametrize("steps", ["open; submit", [1, 2], None, [["nested"]]])
def test_bad_steps_rejected(steps):
    with pytest.raises(ValidationError):
        validate_output(dumps(steps=steps), ALLOWED)


def test_empty_steps_dropped():
    v = validate_output(dumps(steps=[" open ", "", "  "]), ALLOWED)
    assert v.record.steps == ["open"]


@pytest.mark.parametrize("missing", ["process", "steps", "confidence"])
def test_missing_field_rejected(missing):
    obj = dict(GOOD)
    del obj[missing]
    with pytest.raises(ValidationError):
        validate_output(json.dumps(obj), ALLOWED)


@pytest.mark.parametrize("raw", ["", "   ", "no json here", "[1,2,3]", "null", None, 42])
def test_garbage_rejected(raw):
    with pytest.raises(ValidationError):
        validate_output(raw, ALLOWED)


def test_extra_keys_ignored():
    assert validate_output(dumps(reasoning="..."), ALLOWED).record.to_dict() == GOOD
