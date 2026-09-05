# Unsloth release gates

## Scope

The Unsloth release gate separates deterministic CPU evidence from a real
pre-release GPU support claim. Normal pull requests never need an Unsloth
installation, CUDA, a model download, or a self-hosted runner.

The gate does not change the Hub-owned training contract. It only verifies the
existing isolated worker boundary and records release evidence.

## Always-on CPU gate

`.github/workflows/unsloth-release-gate.yml` runs domain, route, Studio
transport, MCP adapter, modality, export, evaluation, promotion, runtime
handoff, contract, schema, backend-mock, CUDA-admission and artifact-security
tests on ordinary GitHub-hosted CPU runners. A second CPU job runs the Angular
unit suite and the `/model-training` Fake-Hub Playwright contract in Chromium.
Unsloth is not installed or imported by either CPU job.

This gate proves:

- closed training contracts and stable error codes;
- CPU-mocked Unsloth parameter mapping;
- artifact containment, archive and Safetensors validation;
- safe configuration defaults;
- CUDA allocator fail-closed behavior without requiring CUDA.
- Hub-only Dry-run/Confirm, replay and denial behavior for Unsloth mutations;
- provider-neutral runtime handoff and immutable rollback contracts;
- explicit capability degradation for unavailable Studio, MCP and modalities.

It does not prove that an Unsloth release works with a particular GPU, driver,
Torch build or model.

## Headless GPU pre-release gate

The `gpu-unsloth-prerelease` job is available only through
`workflow_dispatch` and targets a self-hosted runner with the labels
`linux`, `x64`, `gpu`, and `nvidia`. It is absent from normal PR execution, but
its successful evidence artifact is required before claiming that an Unsloth
version is supported.

The dispatch supplies only:

- a runner-local path to the immutable model snapshot;
- the selected entry from
  `docs/contracts/unsloth-gpu-compatibility-matrix.v1.json`.

The workflow builds the NVIDIA worker from the checked-out commit and writes
that commit to `org.opencontainers.image.revision`. The Hub Evidence Registry
then admits the repository bundle, model snapshot, and executed image, issues
their `SRC_*` identities, reserves the assignment-bound `RUN_*` identity, and
dispatches only the closed assignment projection to the worker. Callers and
workers never choose evidence identities or an image digest.

## Evidence semantics

`scripts/run_hub_evidence_unsloth_gpu_gate.py` owns identity admission and
reservation and delegates the real execution to
`scripts/run_lora_training_smoke.py --profile unsloth`. A verified local
pre-release result requires all of the following:

1. The selected profile is `unsloth`.
2. The NVIDIA smoke finishes with `status=passed`.
3. Three Hub-issued source identities and one pre-reserved run identity are present.
4. The executed image digest is observed and its OCI revision equals Git `HEAD`.
5. The installed Unsloth distribution version was observed.

The report keeps these values distinct:

- `image_attestation.build_input_sha256` hashes the repository inputs copied
  into the worker image;
- `image_attestation.runtime_image_digest` identifies the image actually
  executed and is never inferred from source files;
- `versions` records installed Unsloth, Torch, TorchAO, Transformers, TRL,
  PEFT, bitsandbytes and Safetensors versions;
- `peak_vram` records PyTorch peak allocated and reserved bytes plus detected
  CUDA/cuDNN versions;
- `source_ids` and `run_id` contain only identities issued or reserved by the Hub.

A build-input hash is reproducibility evidence, not an image identity.

## Local commands

CPU-only contract tests:

```bash
pytest -q \
  tests/test_unsloth_release_attestation.py \
  tests/worker/test_lora_training_backends.py \
  tests/worker/test_lora_training_contract_schemas.py \
  tests/worker/test_lora_training_contracts.py
```

Commit-bound image build and GPU pre-release execution:

```bash
revision=$(git rev-parse HEAD)
docker build --target nvidia \
  --build-arg "SOURCE_REVISION=$revision" \
  --file docker/compose-next/Dockerfile.lora-training-worker \
  --tag ananta-lora-training-worker:local-nvidia .
python scripts/run_hub_evidence_unsloth_gpu_gate.py \
  --image ananta-lora-training-worker:local-nvidia \
  --model data/gpu-models/tiny-causal-lm \
  --output artifacts/unsloth-gpu-release-evidence.json
```

The resulting local evidence is deliberately
`production_release_eligible=false`; a real GPU run is not silently promoted
to production evidence.

## Remaining production boundaries

The repository cannot provide the following without operator-controlled
infrastructure:

- an environment registered by Hub policy as production or canary;
- production tenant/project bindings and release policy;
- production-managed model/dataset admissions and provider credentials;
- any required public DNS/TLS/provider endpoints.

## Complete acceptance chain

The manual release profile performs three independent runs. Every run covers
the fixed dataset, Unsloth training, adapter/merged/GGUF exports, a separate
adapter evaluation worker job, immutable promotion, provider-neutral runtime
load and inference-contract resolution, and runtime endpoint rollback.

Every run also records negative tamper checks for dataset, model, adapter,
export, evaluation, promotion and endpoint-revision transitions. Missing
transition evidence is `not_run`; it is never converted to a passing result.

The local pre-release result is verified only when all three runs pass, the
selected model/driver/CUDA/package matrix entry is exact, the commit-bound
executed image digest is unchanged, and all Hub-issued evidence bindings are
complete.

Operational interpretation, exact `not_run` behavior, the compatibility matrix
and rollback procedures are documented in
[`operations/unsloth-gpu-compatibility-and-rollback.md`](operations/unsloth-gpu-compatibility-and-rollback.md).
