# Dendritic-memory experiment integration

The experimental path is additive:

`Admin/API automation -> Hub admission -> Hub queue/revision -> isolated Worker ->`
`bounded Memory Pack -> Hub evaluation -> separate experiment registry -> optional runtime gate`

Workers claim fenced assignments through the Hub and return a closed
`ananta.dendritic-memory-worker-result.v1` response. The internal HTTP surface
is exactly `/internal/v1/dendritic-memory`; the Hub transport disables proxy
semantics, pins private container DNS, rejects redirects and bounds request and
response sizes. Workers execute one assignment and never enqueue or delegate
other work.

The browser calls only `/api/ml-intern-training/dendritic-memory/*`. Worker
transport authority is not exposed in frontend DTOs. The ordinary
`ananta.ml-intern-training.v2` and `ananta.lora-training.v1` contracts retain
their existing semantics.

The reference component uses branch keys and values with bounded top-k routing
and residual readout. It is only a falsifiable implementation hypothesis. It
does not claim biological fidelity, general memory, guaranteed recall or the
elimination of catastrophic forgetting.

An experiment report compares the unchanged base model, a parameter-budget
matched LoRA and the Memory Pack on identical dataset/test/hardware bindings.
Leakage and deterministic gates precede semantic performance interpretation.
Negative or ambiguous results are retained and do not become performance
claims.

Reports preserve disjoint train, validation, transfer, negative-control and
test split digests. Base, parameter-budget-matched LoRA and dendritic results
carry metric direction, multi-seed aggregation, resource measurements,
repository/image/lock provenance and deterministic-runtime labeling. Missing
or incomparable fields fail closed instead of being displayed as zero.
