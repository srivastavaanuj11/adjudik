# Support routing example

Routes a support ticket to one of five queues (SPEC §5.10). Structured forms are
settled by rules; free-text `contact` tickets go to the model. This example drives
the demo seed (§16.5) and the offline benchmark (§13.9).

Files:

- `decision.yaml` — the decision definition.
- `policy.yaml` — thresholds 0.95 for every queue except `other` (null), audit 0.05.
- `cases.jsonl` — rule-row and model test cases.
- `generate_sample.py` — deterministic ticket generator for seeding and benchmarks.

```bash
adjudik validate decision.yaml
adjudik test decision.yaml --cases cases.jsonl
python generate_sample.py 3500 1 > /tmp/tickets.jsonl
```
