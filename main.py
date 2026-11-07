"""Pipeline entry point: load -> sessionize -> redact -> label -> validate -> summary."""
from __future__ import annotations

import argparse
import json
import logging

from mock_llm import ALLOWED, label_session
from pipeline.events import load_events
from pipeline.run import format_summary, run_pipeline, summarise


def mock_client(system: str, user: str, seed: int) -> str:
    # The mock takes a single prompt string and picks its label by substring-matching
    # that string. A real chat API would receive `system` as a separate message; the
    # mock has no such channel, so only the (redacted) session content is passed. Sending
    # the system text would make the mock see "vendor_onboarding" in every prompt.
    return label_session(user, seed)


def main() -> None:
    ap = argparse.ArgumentParser(description="Sessionize UI events and label each session.")
    ap.add_argument("--events", default="data/events.jsonl")
    ap.add_argument("--seed", type=int, default=0, help="base seed; attempt k uses seed+k")
    ap.add_argument("--max-attempts", type=int, default=3)
    ap.add_argument("--out", help="optional path to write per-session records as JSONL")
    ap.add_argument("--show-prompts", action="store_true", help="print the redacted prompts")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    events = load_events(args.events)
    results = run_pipeline(events, mock_client, ALLOWED, args.max_attempts, args.seed)

    print(f"Loaded {len(events)} events\n")
    for r in results:
        o = r.outcome
        flags = "fallback" if o.fell_back else ("retried" if o.retried else "ok")
        print(f"{r.session.session_id:<8} {len(r.session.events):>3} events "
              f"{r.session.duration_seconds:>6.0f}s  {o.record.process:<20} "
              f"conf={o.record.confidence:.2f} attempts={o.attempts} [{flags}]")
        if args.show_prompts:
            print("    " + r.prompt.replace("\n", "\n    "))
    print()
    print(format_summary(summarise(results, ALLOWED)))

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r.to_dict()) + "\n")


if __name__ == "__main__":
    main()
