# Research-training integration

The Hub exposes the additive `/api/ml-intern-training/research` API for capabilities, deterministic recipe
resolution, bounded sweeps, preflight, run creation/read/cancel, Worker transitions, evaluation, and automatic
release decisions. The normal ML-intern capability response also projects `research_training`.

All request contracts are closed and versioned in `schemas/research-training/`. Dataset and source inputs are
SHA-256 bindings, not implicit paths. Evaluation evidence accepts only configured `SRC_*` and `RUN_*` IDs;
unknown identifiers fail closed. No endpoint can turn an ungrounded claim into a release by human approval.

The Worker-side `ResearchStageRunner` accepts one stage already present in the Hub-owned DAG. It verifies the
stage against the run spec and a backend capability before execution, then returns content plus a bound
manifest. The Hub fences transitions with tenant, run, stage, attempt, spec digest, and an HMAC authorization.

Current backend support is the deterministic `mock` adapter for tests and contract integration. `local` is a
reserved policy value and must not be enabled until a production adapter reports matching capabilities.
