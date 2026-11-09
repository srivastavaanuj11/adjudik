# adjudik (core)

The Django-free decision engine at the heart of [Adjudik](../../README.md).
Import package: `adjudik_core`. Distribution name: `adjudik`.

This package loads and validates decision definitions, evaluates FEEL-lite rules,
screens for prompt injection, builds prompts, runs the model step, routes results,
computes calibration/replay statistics, produces canonical JSON and verifies the
audit chain, and provides the `adjudik` CLI. It imports no Django, DRF or Celery —
a contract enforced by `import-linter` and a unit test.

Provider SDKs are optional extras: `adjudik[anthropic]`, `adjudik[openai]`.

Status: M0 scaffold. The public API in SPEC §4.5 is stubbed; implementations land
in M1 (definitions, rules) and M2 (model step, engine).
