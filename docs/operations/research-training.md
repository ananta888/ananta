# Operating research training

Research training is disabled by default. Enable only the deterministic bounded slice with:

```text
ANANTA_RESEARCH_TRAINING_ENABLED=true
ANANTA_RESEARCH_TRAINING_MODE=mock
ANANTA_RESEARCH_TRAINING_AUTOMATIC_RELEASE_ENABLED=false
```

Keep state and artifact paths on Hub-owned persistent storage. The policy file caps GPU hours, storage, cost,
world size, stages, and artifact size. Admission and preflight happen before a run is persisted. State changes
are immutable SQLite revisions with optimistic concurrency; retries are bounded by each stage's
`max_attempts`.

Run `python scripts/check_research_training_boundaries.py` after changing the subsystem. The automatic smoke
suite is `pytest -q tests/research_training`. It uses no network, GPU, model download, prompt, checkbox, or
human response.

Release is always a machine decision. It remains denied unless the run completed, automatic release is
enabled by Hub policy, evaluation is attested and bound to the run/dataset, metrics pass, and configured
`SRC_*` plus `RUN_*` evidence is present. A denial is terminal data with reason codes, never a request for
manual approval.

## Phased rollout and rollback

`config/research-training/rollout.v1.json` is a separate Hub policy. It is
disabled and kill-switched by default, never enables production routes, and
permits automatic phase progression only after every gate for the current
phase is true. Missing or false gates yield a terminal, machine-readable
decision; no phase waits for a person.

| Phase | Runtime | Exit criteria |
| --- | --- | --- |
| 0 | Schema and dry-run | Closed schema contracts, dry-run and boundary-security gates |
| 1 | Tiny local/CPU | Tiny E2E, complete lineage and failure cleanup |
| 2 | Single GPU | Hardware attestation, recovery, quality and cost budgets |
| 3 | Multi GPU | DDP, distributed recovery and scale budget |
| 4 | Optional RL | RL sandbox, reward robustness and automatic rollback |

The kill switch fences progression even when all reported gates are green.
Rollback disables only research admission/runtime and explicitly leaves the
existing adapter-training path and production routes unchanged. Upstream
nanochat is `review_only`: automatic code synchronization is forbidden and any
adopted claim must first receive an immutable Hub source binding.
