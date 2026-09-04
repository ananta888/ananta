# Research-training integration

The additive `/api/ml-intern-training/research` Hub API exposes capabilities, dataset admission, recipes and sweeps,
preflight, run lifecycle, dispatch, heartbeat/preemption, result ingress, lineage, normalized metrics, evaluation,
quality and promotion decisions. Browser and Worker clients never receive a direct Worker URL or arbitrary shell
contract.

Dataset admission reads only staged relative references, performs stable reads, license policy, secret/PII scans,
deduplication and split-contamination checks, then asks the Hub Evidence Registry to issue immutable source IDs.
Dispatch selects a currently compatible Worker inventory, claims a fenced attempt, reserves storage and an evidence
run, and persists the exact closed assignment. Result ingress re-loads that authoritative assignment, validates the
authenticated Worker/lease/result/content bindings, publishes atomically, records lineage and completes the stage.

Worker stages are implemented in `worker/training/research/`; contracts stay in `ananta_contracts/`. The real backend
supports tokenizer train/eval, from-scratch pretraining, base evaluation, full-weight SFT with assistant-only loss,
chat evaluation, optional REINFORCE/group-relative RL, RL evaluation, repeatable inference benchmarks and safe model
export. Versioned evaluation tasks are independently registered; generated code can run only through the hardened
container sandbox.

The Angular workbench consumes Hub run, lineage and normalized metric views. It displays the current DAG/timeline,
artifact parents and evaluation metrics with accessible live status. It never reads storage paths or Worker services.

Promotion is a separate automatic Hub decision. It requires a completed eligible run, attested aggregate evaluation,
per-task/latency/memory/RL quality gates, and registry verification of every `SRC_*`/`RUN_*` binding. Its provenance
manifest binds repository, dataset, recipe, pipeline, every stage artifact, evaluation and quality-decision digests.
