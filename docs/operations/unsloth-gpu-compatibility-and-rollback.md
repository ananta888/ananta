# Unsloth GPU compatibility and rollback

## Scope

This runbook governs the manual NVIDIA release gate for the Unsloth training
profile. CPU CI proves contracts and security boundaries, but it does not prove
GPU support. A support claim is valid only when the manual gate completes the
full chain against one selected entry from
`docs/contracts/unsloth-gpu-compatibility-matrix.v1.json`.

The gate stops at the external GPU-provider boundary. It proves that the Hub
creates and resolves a provider-neutral runtime contract. The separate adapter
evaluation job performs a real adapter load and inference on the admitted GPU
worker. Starting or managing a third-party inference provider remains an
external deployment responsibility.

## Approved compatibility profile

| Matrix entry | Python | CUDA | Minimum NVIDIA driver | Torch | Unsloth | Approved model basename | Required runs |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `unsloth-2026.7.5-cu124-torch260-tiny-causal-lm` | `3.11.15` | `12.4` | `550.54.14` | `2.6.0+cu124` + `torchao 0.13.0` | `2026.7.5` | `tiny-causal-lm` | `3` |

The JSON matrix is authoritative for every pinned package and export format.
Changing a dependency, CUDA runtime, model basename, minimum driver, image
digest, or required run count creates a new candidate profile. Do not edit an
already attested entry in place.

The worker image digest is supplied at dispatch time because it identifies the
image that actually ran. A Dockerfile or lockfile digest is useful build
provenance, but it is not a substitute for the executed image digest.

## Preconditions

The manual workflow requires:

- A self-hosted Linux x64 runner labelled `gpu` and `nvidia`.
- The exact Python, CUDA and package versions selected by the matrix.
- An admitted local model directory whose basename is approved by the matrix.
- An immutable executed worker image digest in `sha256:<hex>` form.
- Externally supplied, valid source and run evidence identifiers.
- Sufficient disk and GPU memory for three independent runs.

The gate never manufactures source or run identifiers. Missing identifiers,
image evidence, model admission, GPU access, dependencies, or matrix selection
produce an explicit `not_run` result.

## Execution

Dispatch `.github/workflows/unsloth-release-gate.yml` and provide the evidence
and image values from the relevant external systems. The workflow invokes:

```bash
python scripts/run_lora_training_smoke.py \
  --skip-mock \
  --require-nvidia \
  --profile unsloth \
  --nvidia-model "$MODEL_PATH" \
  --runtime-image-digest "$ANANTA_LORA_WORKER_IMAGE_SHA256" \
  --compatibility-matrix docs/contracts/unsloth-gpu-compatibility-matrix.v1.json \
  --matrix-entry "$ANANTA_UNSLOTH_COMPATIBILITY_ENTRY" \
  --repeat 3 \
  --out unsloth-gpu-release-evidence.json
```

The workflow supplies source and run identifiers through the documented
environment variables. Do not place real evidence identifiers in source files,
fixtures, documentation, or commit messages.

## Required release chain

Each of the three runs must pass all of these stages:

1. Fixed train and validation datasets are written and hash-bound to the job.
2. The admitted base model is hash-bound to Unsloth training.
3. The training job produces adapter artifacts and training evaluation.
4. Adapter, merged 16-bit, and GGUF `q4_k_m` exports are produced.
5. A separate `evaluate_existing_adapter` worker job loads the adapter and runs inference.
6. Promotion binds evaluation, dataset, model, adapter, export and external evidence provenance.
7. The Hub creates a provider-neutral runtime endpoint revision from the promoted export.
8. Model invocation resolves the endpoint and records the inference contract.
9. The Hub rolls the endpoint back to the immutable prior revision.

Promotion and runtime handoff remain separate decisions. Runtime rollback does
not rewrite promotion history or provenance.

## Mandatory negative checks

Every run also proves rejection of tampered evidence at each transition:

| Transition | Required denial |
| --- | --- |
| Dataset to training | `dataset_hash_mismatch` |
| Base model to training | `base_model_hash_mismatch` |
| Adapter to export | `adapter_hash_mismatch` |
| Export to evaluation | `export_hash_mismatch` |
| Evaluation to promotion | `evaluation_hash_mismatch` |
| Promotion to runtime | `promotion_hash_mismatch` |
| Runtime revision to rollback | `endpoint_revision_mismatch` |

A missing transition hash is `not_run`, never a passing negative test.

## Evidence interpretation

The release artifact must contain:

- `nvidia_live_smoke.status = passed`.
- `deterministic_run_count = 3`.
- Three per-run attestation hashes.
- A passed compatibility attestation containing the selected entry and matrix digest.
- A supplied runtime image digest.
- Complete external source and run evidence bindings.
- Passed stage coverage for training, export, both evaluations, promotion, runtime load, rollback and tamper paths.
- `unsloth_support_claim.verified = true`.

The three runs use identical fixed inputs and configuration but independent
temporary workspaces, worker state, evaluation jobs and runtime registries.
Metrics may vary within the job's evaluation policy; a run is not accepted
merely because another run passed.

## Honest `not_run` outcomes

| Reason code | Operator action |
| --- | --- |
| `compatibility_matrix_entry_not_configured` | Select a versioned matrix entry. |
| `compatibility_matrix_unavailable` | Restore the tracked matrix file. |
| `compatibility_matrix_entry_unknown` | Use an existing candidate or add a reviewed new entry. |
| `runtime_image_digest_missing` | Supply the digest of the executed worker image. |
| `source_or_run_ids_missing` | Supply externally issued evidence identifiers. |
| `local_model_not_configured` | Mount and select the approved local model. |
| `local_model_missing` | Restore the approved model mount. |
| `local_model_path_not_admitted` | Use a non-symlink admitted model directory. |
| `nvidia_device_unavailable` | Restore the NVIDIA runner/device assignment. |
| `nvidia_training_dependencies_unavailable` | Build or select the pinned worker image. |
| `manual_gpu_runner_and_external_evidence_required` | Dispatch the manual GPU workflow with external evidence. |

Version, driver, model or run-count mismatches are failed profile attestations,
not `not_run`, because the selected environment contradicts the matrix.

## Runtime rollback

Use the Hub-owned runtime management action. Do not call a worker or provider
directly.

1. Record the current endpoint ID, revision, resolved artifact hash, image digest, matrix entry and external evidence references from the release artifact.
2. Stop new rollout decisions for that endpoint through the normal operational change control.
3. Select the last attested immutable endpoint revision.
4. Submit rollback through the Hub with the current endpoint revision as `expected_version` and an operator reason.
5. Confirm that the Hub creates a new revision pointing to the prior immutable artifact; it must not mutate or delete either historical revision.
6. Resolve the endpoint through the normal model-invocation path and compare the provider-neutral inference contract with the last attested contract.
7. Run the relevant inference health check before reopening traffic.
8. Preserve promotion records and provenance unchanged.

An `endpoint_revision_mismatch` means another controller changed the endpoint.
Refresh state and repeat the decision; never bypass optimistic concurrency.

## Dependency or image rollback

If the regression is below the runtime endpoint layer:

1. Select the last attested matrix entry and executed image digest.
2. Rebuild or redeploy only through the normal worker image pipeline.
3. Keep the model basename, CUDA runtime, driver floor and package set exactly aligned with that entry.
4. Rerun CPU gates.
5. Rerun all three manual GPU chains with newly supplied external evidence.
6. Promote the resulting candidate only after the new release artifact verifies the support claim.

Do not relabel an old artifact as evidence for a new image or dependency set.
Do not weaken the matrix to make a failing environment pass.
