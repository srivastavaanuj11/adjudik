"""Generate deterministic support-routing tickets (SPEC §16.5, §13.9).

Prints one JSON ticket input per line (``cases.jsonl`` shape without ``expect``).
Used by the demo seed (M3b) and the offline benchmark (M5). Deterministic given a
seed so runs are reproducible.

Usage:
    python generate_sample.py [COUNT] [SEED] > tickets.jsonl
"""

from __future__ import annotations

import json
import random
import sys

FORMS = ["contact", "refund_form", "bug_report", "password_reset"]
PLANS = ["free", "pro", "enterprise"]

# (queue intent, subject, body) templates. The queue is implicit ground truth for
# the model cases; the seed's simulated reviewer uses it.
TEMPLATES = [
    (
        "billing",
        "Charged twice",
        "I was billed twice for invoice {n}. Please refund the duplicate.",
    ),
    (
        "billing",
        "Subscription change",
        "I want to downgrade my plan before the next billing cycle.",
    ),
    ("technical", "Export fails", "Clicking export returns a 500 error on report {n}."),
    ("technical", "Broken integration", "The webhook for event {n} stopped firing yesterday."),
    ("account", "Locked out", "I cannot sign in and the reset link for user {n} does not work."),
    ("account", "Permissions", "A teammate needs access to workspace {n} but gets denied."),
    ("shipping", "Late delivery", "Order {n} was due last week and has not arrived."),
    ("shipping", "Wrong address", "Please change the delivery address for order {n}."),
    ("other", "General question", "Just wondering how feature {n} is meant to be used."),
]


def generate(count: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for i in range(count):
        queue, subject, body = rng.choice(TEMPLATES)
        # Mostly free-text contact tickets (the model's job); some structured forms.
        form = rng.choices(FORMS, weights=[7, 1, 1, 1])[0]
        plan = rng.choice(PLANS)
        out.append(
            {
                "ticket": {
                    "id": f"T-{seed}-{i}",
                    "subject": subject,
                    "body": body.format(n=i),
                    "form": form,
                    "plan": plan,
                }
            }
        )
    return out


def main() -> None:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    for ticket in generate(count, seed):
        print(json.dumps(ticket))


if __name__ == "__main__":
    main()
