# Model Intelligence architecture

## Boundary

Model Intelligence is a separate bounded context. It does not extend the
CodeCompass source-code graph and does not reuse repository-analysis evidence
as model-analysis evidence.

The Hub owns:

- model-analysis job state
- idempotency and queue admission
- task projection and worker delegation
- cancellation and lease fencing
- tenant policy and artifact authorization
- report composition and release-gate decisions

Workers own only the execution of one delegated operation. They may inspect an
admitted snapshot, produce a bounded result and publish an artifact reference.
They must not create tasks, choose another worker or continue an analysis DAG.

## Contract layers

The wire contracts live in `ananta_contracts/model_intelligence.py` and
`ananta_contracts/model_intelligence_execution.py`.

They deliberately separate:

- `ModelIdentity`: immutable model coordinates and canonical identity
- `CapabilityDescriptor`: truthful support state and reason code
- `AnalysisJob`: Hub-owned execution request
- `ArtifactRef`: content-addressed, tenant-bound result reference
- `ErrorEnvelope`: sanitised stable failure
- `ResourceLease`: worker execution fence and resource budget
- `CancellationSignal`: Hub-issued cooperative cancellation
- `AnalysisCompletion`: idempotent fenced worker completion

Neither contract module imports Flask, Hub services or worker implementations.

## Snapshot admission

`ModelAnalysisSnapshotAdmission` composes the restricted-inference manifest
validator and adds analysis-specific limits:

- maximum individual and aggregate bytes
- maximum file count
- archive rejection
- Pickle and executable-code rejection
- path, symlink and hardlink rejection
- sparse-file expansion bounds
- immutable hashes, revision, provenance and license state

The admitted external manifest contains no host or container-local path. It
contains a content digest and a tenant-bound admission ID.

## Analysis ports

Static analysis reads Safetensors headers without materialising tensor payloads.
Tokenizer and quantisation inspection read bounded data-only JSON. Dynamic
trace capture is a distinct opt-in port and is limited to an admitted local
runtime. It stores aggregate statistics by default, never raw activations.

LoRA analysis does not merge or modify the base model. Evaluation comparisons
only rank runs whose versioned profiles are compatible.

## Graph and artifacts

The model graph uses `model_graph.v1`, not `domain_graph_artifact.v1`. Node IDs
derive from canonical model and entity identities. Traversal depth, node count,
page size and execution time are server bounded.

Artifact storage is tenant scoped and content addressed. Canonical JSON is the
source of truth. Offline HTML is a derived representation with no CDN or
network dependency. Report sections use one of:

- `available`
- `unsupported`
- `not_run`
- `failed`

## SOLID review

- SRP: admission, contracts, execution, graph, persistence and rendering are
  separate components.
- OCP: additional runtimes and analyzers implement small ports rather than
  modifying the Hub state machine.
- LSP: an adapter may advertise a capability only when its contract probe
  succeeds.
- ISP: static inspection, inference and trace capture are different interfaces.
- DIP: Hub and workers depend on repository, task, artifact, resource and
  analyzer ports rather than concrete infrastructure.

Known preserved constraint: cancellation is cooperative. A non-cooperative
native runtime needs process or container termination supplied by an extended
execution profile.
