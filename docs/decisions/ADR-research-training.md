# ADR: Governed full-model research training

Status: experimental, accepted for an opt-in mock/dry-run slice; not production-ready.

## Decision

Full-model research training is a separate bounded context from LoRA training. The Hub owns recipes,
admission, the stage DAG, attempts, state revisions, lineage, evaluation, and release decisions. A Worker
executes exactly one signed Hub-delegated stage and returns a closed artifact manifest. Workers never add
stages, route tasks, or contact other Workers.

Contracts live in `ananta_contracts/research_training.py`; Hub services remain free of ML runtimes; Worker
backends implement the small `ResearchWorkerBackend` port. This protects SRP and DIP while preserving the
hub-worker architecture. The first implementation deliberately provides a deterministic mock backend, not
a claim that real full-weight training is production-ready.

## Automation and compatibility

Every decision has a headless path. Policy, resource preflight, retry exhaustion, evaluation, and release
return stable reason codes and never wait for a person. An optional future interactive client may observe or
request a run, but is not part of correctness. Existing LoRA APIs remain unchanged; research endpoints and
capability fields are additive and default off.

## Consequences

Executable checkpoint ingress is denied in this initial slice. Real tokenizer, Torch/DDP, SFT/RL and safe
model-export backends require later adapter work. Until then, the capability projection advertises
`experimental`, `not_production_ready`, and `claims_not_verified`.
