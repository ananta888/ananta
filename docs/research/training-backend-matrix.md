# Optional training-backend matrix

Baseline: 2026-08-27. This matrix records engineering inputs, not verified
runtime evidence. No `SRC_*` or `RUN_*` identifiers were supplied.

| Backend | Pin | License | Python / runtime baseline | Initial Ananta capability | Maintenance / decision |
| --- | --- | --- | --- | --- | --- |
| Axolotl | `0.18.0`, commit `2f5cb9d…` | Apache-2.0 | Python >=3.10; upstream 0.18 images target current PyTorch/CUDA lines | text SFT, LoRA/QLoRA, single GPU, safetensors adapter | Active; experimental and default-off until live gate |
| LlamaFactory | `0.9.5`, commit `7af9095…` | Apache-2.0 | Python >=3.11; PyTorch >=2.4 | text SFT, LoRA/QLoRA, single GPU, safetensors adapter | Active; experimental and default-off until live gate |
| AutoTrain Advanced | `0.8.36`, wheel SHA-256 `03e5400…` | Apache-2.0 | Upstream local CLI; isolated image required because dependency set can diverge | local-only text SFT contract, LoRA/QLoRA | Upstream says unmaintained; experimental/default-off, production No-Go |
| torchtune | `0.6.1`, commit `a6290a5…` | BSD-3-Clause | Python >=3.9; release tested with PyTorch 2.6-era stack | allowlisted text LoRA recipe, single GPU | Development wound down in 2025; experimental/default-off, production No-Go |

Official references are version-bound in
`config/licenses/training-backends.v1.json`. The package lock and generated
SBOM are separate release inputs. A framework license never grants rights to
the selected base model, tokenizer, dataset, trained adapter, merged model or
GGUF export.

## Capability interpretation

- Only text SFT plus LoRA/QLoRA is admitted initially.
- DPO, ORPO, full fine-tuning, FSDP and multimodal paths remain undeclared.
- Model and datasets are immutable local snapshots; downloads are separate
  Hub-authorized import jobs.
- Third-party WebUIs, tracking, telemetry, arbitrary plugins, remote code and
  user-provided CLI arguments are outside the worker contract.
- Checkpoint and export compatibility is proven by hashes and manifest fields,
  never inferred from matching filenames.

## RTX 3080 interpretation

The 10-GB profile is a benchmark target, not a compatibility promise. A
backend becomes verified for it only after the manual NVIDIA gate records the
exact model, dataset, versions, driver, CUDA runtime, peak VRAM and result
digests. CPU contract results cannot satisfy that gate.
