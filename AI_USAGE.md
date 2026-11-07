# AI usage

## Tools

- **Claude Code (Claude Opus 5.5)** in VS Code. I used it to explain the brief, write the code, tests and docs, and run the tests. I set the direction and reviewed the output.

## Where I accepted its output

- The module layout (`events` / `sessionize` / `redact` / `validate` / `label` / `run`) and the stdlib-only implementation.
- The Luhn implementation and the email / card regexes, after the tests below confirmed them.
- The "repair only when nothing is invented, otherwise retry" policy for model output.
- Most of the test cases, after I read through them to check they test real edge cases rather than repeating the implementation.

## Where it was corrected or overridden

- **No hardcoding / keyword dependence.** I told it explicitly that the pipeline must work for any event stream, not just the sample. As a result, redaction is driven by generic detectors and field-label categories, never by the sample's specific names, numbers or IDs. I also had it add the randomly generated event-stream tests so this is actually checked.
- **The 10:00 boundary.** Checking the sample data showed a gap of exactly 10:00 between two unrelated tasks, so the rule became `gap >= 10 min`. The earlier `>` reading would have merged the invoice and vendor tasks. This is checked by a test.
- **App-change rule rejected.** The brief invites a second rule. The data shows one invoice task spanning NetSuite → Outlook → NetSuite, so we left it out and documented why.
- **Phone misread as a card.** A test showed `+4915112345678` was redacted as `[CARD]` because the digits happen to pass Luhn. The card detector now ignores numbers that start with `+`.
- **Card followed by more digits.** A first-draft card regex would have matched `4539 1488 0343 6467 2026` as one 20-digit run, failed Luhn and leaked the card. It now checks digit groups separately, and there is a test for it.
- **Prompt vs mock.** Including the allowed labels in the prompt sent to the mock would make it label everything `vendor_onboarding`, because it matches substrings. Instead of dropping the label list from the real prompt, the prompt is split into system and user parts, and only the mock adapter drops the system part. This is documented in the README.
- **Clamping confidence rejected.** Turning `1.7` into `1.0` would invent certainty, so out-of-range values are retried instead.

<!-- Review and edit this file so it reflects your own experience before submitting. -->
