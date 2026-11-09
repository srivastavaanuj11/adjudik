# Refund approval example

A refund-approval decision (SPEC §5.9). Rules settle the clear cases (cancelled
orders, the 30-day window, high-value and frequent-refunder reviews, small gold
damaged auto-approvals); everything else is handed to the model.

Files:

- `decision.yaml` — the decision definition.
- `policy.yaml` — an automation policy (`approve` 0.95, `deny` 0.99, audit 0.05),
  used by the CLI and library. On the server, policies are created via the API.
- `cases.jsonl` — test cases covering every rule row, null `days_since_delivery`
  and a missing `reason_code`.

Validate and run the rule-only cases:

```bash
adjudik validate decision.yaml
adjudik test decision.yaml --cases cases.jsonl
```

Cases that reach the model are reported `skipped` without `--with-model`.
