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

## Manual GPU pre-release gate

The `gpu-unsloth-prerelease` job is available only through
`workflow_dispatch` and targets a self-hosted runner with the labels
`linux`, `x64`, `gpu`, and `nvidia`. It is absent from normal PR execution, but
its successful evidence artifact is required before claiming that an Unsloth
version is supported.

The operator must supply:

- one or more existing `SRC_*` source identifiers;
- one or more existing `RUN_*` execution identifiers;
- the digest of the image actually executed, in
  `sha256:<64 lowercase hex>` form;
- an absolute runner-local path to an approved model snapshot.
- the selected entry from
  `docs/contracts/unsloth-gpu-compatibility-matrix.v1.json`.

The workflow and runner never synthesize source or run identifiers. Missing or
malformed identifiers keep `unsloth_support_claim.verified` false.

## Evidence semantics

`scripts/run_lora_training_smoke.py --profile unsloth` selects
`backend=unsloth` in the worker request. A verified support claim requires all
of the following:

1. The selected profile is `unsloth`.
2. The NVIDIA smoke finishes with `status=passed`.
3. At least one supplied `SRC_*` and one supplied `RUN_*` ID are present.
4. A runtime image digest was supplied.
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
- `evidence_ids` contains only operator-supplied identifiers.

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

GPU pre-release execution:

```bash
python scripts/run_lora_training_smoke.py \
  --skip-mock \
  --require-nvidia \
  --profile unsloth \
  --nvidia-model /approved/local/model \
  --runtime-image-digest sha256:<actual-image-digest> \
  --src-id "$EXISTING_SRC_ID" \
  --run-id "$EXISTING_RUN_ID" \
  --compatibility-matrix docs/contracts/unsloth-gpu-compatibility-matrix.v1.json \
  --matrix-entry "$ANANTA_UNSLOTH_COMPATIBILITY_ENTRY" \
  --repeat 3 \
  --out unsloth-gpu-release-evidence.json
```

Placeholders above are documentation only and are not valid release evidence.

## Remaining external blockers

The repository cannot provide the following without operator-controlled
infrastructure:

- a self-hosted NVIDIA runner and compatible driver/runtime;
- an approved local model snapshot;
- the digest of the image actually executed;
- valid source and run identifiers from the release evidence system;
- approval that the resulting evidence is sufficient for a support claim.

## Complete acceptance chain

The manual release profile performs three independent runs. Every run covers
the fixed dataset, Unsloth training, adapter/merged/GGUF exports, a separate
adapter evaluation worker job, immutable promotion, provider-neutral runtime
load and inference-contract resolution, and runtime endpoint rollback.

Every run also records negative tamper checks for dataset, model, adapter,
export, evaluation, promotion and endpoint-revision transitions. Missing
transition evidence is `not_run`; it is never converted to a passing result.

The support claim is verified only when all three runs pass, the selected
model/driver/CUDA/package matrix entry is exact, the executed image digest is
present, and all externally supplied evidence bindings are complete.

Operational interpretation, exact `not_run` behavior, the compatibility matrix
and rollback procedures are documented in
[`operations/unsloth-gpu-compatibility-and-rollback.md`](operations/unsloth-gpu-compatibility-and-rollback.md).
