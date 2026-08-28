# Optional training backends

Axolotl, LLaMA-Factory, AutoTrain Advanced and torchtune are optional Worker
implementations. The Hub remains the only control plane. None is enabled by the
base Compose deployment and no third-party Web UI is exposed.

## Status

| Backend | Pin | Maintenance | Product state |
| --- | --- | --- | --- |
| Axolotl | 0.18.0 | active | experimental, default-off |
| LLaMA-Factory | 0.9.5 | active | experimental, default-off |
| AutoTrain Advanced | 0.8.36 | upstream unmaintained | experimental, default-off, no production recommendation |
| torchtune | 0.6.1 | development wound down | experimental, default-off, no production recommendation |

Software licenses do not grant rights to a base model, dataset or generated
adapter. Validate these independently. Before deployment, retain the image digest,
`pip inspect` inventory, license-policy result and vulnerability scan result.

## Install and enable

Choose exactly one opt-in Compose file and provide the same Hub URL, service token,
state volume and model mount as the existing LoRA worker. For example, validate the
Axolotl expansion with:

```console
docker compose -f docker/compose-next/compose.lora-training.yml \
  -f docker/compose-next/compose.training-axolotl.yml \
  --profile training-axolotl config
```

Build and start only after the automatic image gate passes local policy:

```console
python scripts/run_training_backend_image_gate.py \
  --build --scan --output artifacts/training-backend-image-gate.json
```

The gate builds all four images, executes netless/read-only installation smokes,
creates an SBOM with the digest-pinned Syft image and evaluates vulnerabilities
with the digest-pinned Grype image. Critical, high and unresolved-license limits
are machine-owned by `config/security/training-backend-scanners.v1.json`; no human
approval can reinterpret a failure as success. The Worker health probe reports
unavailable when the exact package or executable is missing; other backends and
the Ananta core remain operational.

## Verify

Install the reviewed templates from `docs/ci/` under `.github/workflows/` using
a GitHub credential with `workflow` scope. The CPU contract runs on repository
changes; the self-hosted NVIDIA prerequisite also runs weekly and on relevant
main-branch changes without operator input. A manual dispatch is only an optional
retry mechanism. It is never required by a test or release path.

A real GPU result is only `verified` when the evidence includes the immutable
container digest, GPU/driver attestation and all declared test results. Use
`scripts/run_training_backends_acceptance.py` to validate such evidence.
`not_run`, `blocked` and `failed` are distinct and must never be rewritten as
success. The automatic image prerequisite alone cannot claim that real training,
CUDA behavior, VRAM bounds or model quality were verified.

Training success does not activate an adapter. Evaluation, Hub approval, registry
admission and promotion remain mandatory.

## Disable and rollback

Stop and remove the opt-in Worker service or omit its Compose overlay on the next
deployment. Do not delete Hub job or artifact history. In-flight fallback is
forbidden: a retry using another backend must be a new visible attempt. AutoTrain or
torchtune require a fresh maintainer, supply-chain and product review before any
production promotion.
