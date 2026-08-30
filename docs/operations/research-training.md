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
