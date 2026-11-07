# UI events → labelled processes

A small Python pipeline that turns a stream of desktop UI events into labelled work sessions:

```
events.jsonl → load → sessionize → redact → prompt → mock LLM → validate / repair / retry → summary
```

## How to run

Requires Python 3.10+. The pipeline itself uses only the standard library; tests need `pytest`.

```bash
pip install -r requirements.txt
python main.py                      # run on data/events.jsonl
python main.py --show-prompts       # also print exactly what is sent to the model
python main.py --seed 7 --out results.jsonl   # different seed, write per-session records
pytest                              # run all tests
```

Sample output:

```
u1-s1      4 events    894s  invoice_processing   conf=0.82 attempts=2 [retried]
u1-s2      1 events      0s  vendor_onboarding    conf=0.82 attempts=1 [ok]
u1-s3      3 events    332s  vendor_onboarding    conf=0.82 attempts=1 [ok]
u2-s1      3 events    285s  expense_review       conf=0.82 attempts=1 [ok]

Sessions: 4
Sessions per process:
  invoice_processing     1
  vendor_onboarding      2
  expense_review         1
  unknown                0
Median session duration: 5m 08s
Needed a retry: 1
Fell back to unknown: 0
Repaired on first attempt (no retry): 2
```

## Layout

| File | Responsibility |
|---|---|
| `mock_llm.py` | The provided mock, unchanged |
| `data/events.jsonl` | The provided sample events |
| `pipeline/events.py` | Parse events; malformed lines are skipped and logged by line number only (never their content) |
| `pipeline/sessionize.py` | Per-user session splitting |
| `pipeline/redact.py` | Personal-data removal |
| `pipeline/validate.py` | Parse and validate model output into `{process, steps, confidence}` |
| `pipeline/label.py` | Prompt building, retries with new seeds, fallback |
| `pipeline/run.py` | Wiring and summary |
| `main.py` | CLI and adapter to the mock |

## Session rule

**A new session starts when a user has been inactive for 10 minutes or more** (gap ≥ 10:00 since that user's previous event). Events are grouped per user and sorted by timestamp first, so input order doesn't matter.

- **≥, not >.** "10 minutes of inactivity" is reached at 10:00. This matters for the sample data: u1 approves an invoice at 09:14:59 and starts a new vendor request at exactly 09:24:59. With `>` those two unrelated tasks would merge into one session.
- **No second rule (deliberately).** I considered splitting on app change and rejected it. In the sample, a single invoice task goes NetSuite → Outlook (emailing AP) → NetSuite. Real processes span apps, so splitting on app change would fragment exactly the processes we want to discover. Mixed sessions are better handled later (see "More time").
- Known effect: u1's vendor onboarding is split in two (`u1-s2`, `u1-s3`) by a 16-minute pause. The rule is applied as specified rather than tuned to the sample.

## Keeping personal data away from the model

Redaction runs on every event before a prompt is built, and the detectors run once more over the final prompt text. There are two independent layers:

1. **Field-based.** If an event's label names a personal field, its whole value becomes `[REDACTED]`, whatever it contains. Labels are matched on normalised words, so `Full name`, `full_name`, `FullName`, `Customer name`, `Date of birth`, `Phone`, `IBAN`, `Card`, and recipient fields (`To`, `Cc`, ...) all match. Values typed into such fields are also scrubbed from the **same user's** other events and sessions; for example, a name typed into a form and later into a search box.
2. **Content-based**, applied to every field:
   - emails → `[EMAIL]`
   - phones → `[PHONE]`: international numbers starting with `+` (8–15 digits), or the separated `(415) 555-0132` / `415-555-0132` formats
   - card numbers → `[CARD]`: 13–19 digits (spaces or dashes allowed) that **pass the Luhn check**. Groups are checked separately, so a card followed by other digits (`4539 1488 0343 6467 2026`) is still caught.

**Order and invoice numbers survive** because none of the detectors match them. Digits joined to letters or hyphens (`INV-2026-000417`, `PO-88213`, `ER-1182`) are never treated as phone or card numbers, and a bare 16-digit order number like `5432 1098 7654 3210` fails Luhn, so it is kept. The tests check this on both the sample data and random data.

Trade-offs, chosen on purpose:
- Bare unformatted digit runs without `+` (`Order 1234567890`) are **not** treated as phones. Otherwise every 10-digit order number would be redacted. A phone typed that way in free text would leak, unless it was typed into a phone field.
- A 13–19 digit order number that happens to pass Luhn (about 1 in 10) will be redacted. The spec says Luhn-valid means card, and over-redacting is the safe failure.
- Names in free text are only caught if the same user typed them into a name field somewhere. Catching names in general needs NER (see "More time").

## Handling the non-deterministic model

Model output is never trusted. `validate.py` either returns a valid record or raises an error.

| Mock behaviour | Handling |
|---|---|
| JSON wrapped in prose / markdown | **Repair**: extract the first complete JSON object (`json.JSONDecoder.raw_decode`) |
| Label like `"Invoice Processing"` | **Repair**: normalise case and separators. Accepted only if the result is in `ALLOWED` |
| Label outside the allowed set | **Reject → retry** |
| Truncated JSON | **Reject → retry.** Closing the brackets cannot recover a missing confidence, and inventing one is worse than asking again |
| Confidence out of range (`1.7`), NaN, string, bool | **Reject → retry.** Clamping 1.7 to 1.0 would report *more* certainty than any valid answer; it's a broken answer, not a high one |
| `steps` not a list of strings | **Reject → retry** (empty strings are dropped) |
| Client raises an exception | Counted as a failed attempt → retry |

Retries: up to `--max-attempts` (default 3), and attempt *k* uses seed `base_seed + k`. If every attempt fails, the record is `{"process": "unknown", "steps": [], "confidence": 0.0}` and is marked `fell_back`. Each session keeps its `attempts`, `repairs` and `errors` (written with `--out`), so you can see why it was repaired or retried. The pipeline never raises on a bad session: even a failure during redaction falls back to `unknown` instead of sending anything.

**Note on the mock adapter.** The real prompt has a system part (instructions and the allowed labels) and a user part (the redacted events). The mock takes a single string and chooses its label by substring match, so if the system part were included, it would see `vendor_onboarding` in every prompt and label everything as vendor onboarding. `main.mock_client` therefore passes only the user part, where a real chat API would receive the system part as a separate message.

## Tests

`pytest` runs about 200 tests:

- `test_sessionize.py`: gaps of 9:59 / exactly 10:00 / 10:01, gap measured from the previous event, app change doesn't split, users are separated, unsorted input, empty input, the sample's split, malformed lines.
- `test_redact.py`: Luhn, email / phone / card formats, a card followed by other digits, IDs that must survive (including Luhn-valid digits inside an ID), sensitive vs non-sensitive labels, scrubbing known values, idempotence.
- `test_validate.py`: clean output, prose, markdown fences, stray braces, several truncation points, label normalisation vs rejection, confidence range and type (NaN, bool, string), bad steps, missing fields, garbage.
- `test_label.py`: retry uses a new seed, exceptions, non-string output, fallback after N failures. Runs the **real mock** over 150 prompts and checks that every record is valid, and that each of the mock's four failure modes actually occurs.
- `test_pipeline.py`: end-to-end on the sample with a spy client that records everything sent to the model (no PII, IDs kept); 20 seeds; a client that is always down; and **40 randomly generated event streams** (shuffled, multiple users, PII planted in both sensitive fields and free text) checking that nothing crashes, nothing leaks and IDs survive.

## What I cut

- **Second session rule.** Rejected for the reasons above, not just for time.
- **Repairing truncated JSON.** Deliberately retried instead of repaired.
- **NER-based name detection**: names are caught via field labels and remembered values, not by a model.
- **Other identifiers** (IBAN in free text, national IDs, street addresses in free text) are only caught via field labels.
- **Async / concurrent model calls**: sessions are labelled one at a time.

## With more time

- Use NER (e.g. spaCy or Presidio) for names and addresses in free text, plus a precision/recall test on a labelled corpus.
- Make the session rule data-driven: look at the distribution of gaps per user and app to choose the threshold. Merge adjacent sessions the model gives the same label within a short gap (this would rejoin u1-s2 and u1-s3).
- Ask the model to segment mixed sessions instead of splitting on heuristics.
- Use majority vote / self-consistency across seeds for low-confidence labels, and calibrate confidence.
- Add structured output / JSON-schema mode with a real model, with timeouts, backoff and rate limiting.
- Make the sensitive-field list configurable per customer, and add an audit log of redaction counts per category (counts only, no values).
