# ADR: Governed full-model research training

Status: accepted as an experimental, opt-in local/CPU capability; production promotion remains policy- and evidence-gated.

## Decision

Full-model research training remains a bounded context beside LoRA. The Hub owns dataset admission, automatic
`SRC_*` issuance, recipe and budget decisions, the DAG, Worker selection, immutable assignments, execution leases,
automatic `RUN_*` reservation, quota, lineage, quality gates and promotion. A Worker consumes exactly one closed
assignment and cannot create a next stage, broaden evidence, or orchestrate another Worker.

The execution side uses small adapters: deterministic byte BPE, a tiny decoder-only Torch model, full-weight
pretraining/SFT, versioned evaluation tasks, optional bounded RL, inference benchmarking and safe Safetensors export.
Torch and Safetensors exist only in the Worker image. This split protects SRP and DIP and keeps the Hub free of ML
runtimes.

## Automation, compatibility and evidence

All paths are headless. Approval policy either grants an eligible automatic action or returns a bounded reason code;
no test or run waits for a person. The Hub Evidence Registry derives immutable identities from admitted content and
pre-reserved execution bindings. Test/synthetic evidence exercises all mechanics but cannot satisfy production gates.

Research endpoints, schemas, state and containers are additive. Existing LoRA APIs and execution images are
unchanged. The local backend is not a blanket production claim: GPU/runtime profiles become releasable only after
their own automatic assignment-bound evidence succeeds.

## Consequences

The implementation accepts curated, staged datasets only, denies executable artifact ingress, uses digest-pinned
inputs and safe tensor serialization, and isolates generated-code evaluation in a no-network container. SQLite is
appropriate for the current single-Hub experimental deployment; a future multi-Hub implementation must replace the
persistence adapters without changing the domain ports.
