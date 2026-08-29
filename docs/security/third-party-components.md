# Third-party component register

## Purpose

This register records the third-party boundary for optional Ananta
integrations. It is an engineering inventory and does not replace legal
review, an SBOM, image signature verification, or repository-administration
approval.

No entry in this document is a `SRC_*` or `RUN_*` claim. Source and run IDs are
valid only when an authoritative system supplies them to the release gate.

## Unsloth integration baseline

Baseline date: 2026-07-28.

| Component | Pinned version | Ananta profile | Published license | Distribution boundary | Authoritative reference |
| --- | --- | --- | --- | --- | --- |
| Unsloth Core | `2026.7.5` | NVIDIA training worker | Apache-2.0 | Installed only in the optional NVIDIA worker image | [Unsloth package](https://pypi.org/project/unsloth/2026.7.5/) |
| Unsloth Zoo | `2026.7.6` | NVIDIA training worker | LGPL-3.0-or-later | Installed only in the optional NVIDIA worker image | [Unsloth Zoo package](https://pypi.org/project/unsloth-zoo/2026.7.6/) |
| Unsloth Studio UI | Deployment image must be digest pinned | `unsloth` Compose profile | AGPL-3.0 | Separate opt-in container; not linked into the Hub or worker Python package | [Upstream license boundary](https://github.com/unslothai/unsloth/blob/main/README.md?plain=1#L1608) |
| PyTorch | `2.6.0+cu124` | NVIDIA training worker | BSD-3-Clause | Optional NVIDIA worker image | [PyTorch](https://github.com/pytorch/pytorch) |
| Transformers | `4.57.3` | NVIDIA training worker | Apache-2.0 | Optional NVIDIA worker image | [Transformers](https://github.com/huggingface/transformers) |
| PEFT | `0.18.0` | NVIDIA training worker | Apache-2.0 | Optional NVIDIA worker image | [PEFT](https://github.com/huggingface/peft) |
| TRL | `0.24.0` | NVIDIA training worker | Apache-2.0 | Optional NVIDIA worker image | [TRL](https://github.com/huggingface/trl) |
| sentence-transformers | `5.1.2` | Embedding modality | Apache-2.0 | Optional NVIDIA worker image | [Sentence Transformers](https://github.com/huggingface/sentence-transformers) |
| bitsandbytes | `0.45.5` | 4-bit QLoRA | MIT | Optional NVIDIA worker image | [bitsandbytes](https://github.com/bitsandbytes-foundation/bitsandbytes) |
| xFormers | `0.0.29.post3` | CUDA attention kernels | BSD-3-Clause | Optional NVIDIA worker image | [xFormers](https://github.com/facebookresearch/xformers) |

The executable dependency pins are maintained in
`docker/compose-next/requirements.lora-training-nvidia.txt`. A change to that
file requires updating this table in the same change.

## Studio obligations and controls

Unsloth's upstream README states that core remains Apache-2.0 while optional
components including Studio UI use AGPL-3.0. Operators that deploy, modify, or
network-serve Studio must complete an applicable-license review and satisfy
the resulting source-offer and notice obligations.

Ananta reduces accidental coupling through these technical boundaries:

| Control | Repository implementation |
| --- | --- |
| Opt-in deployment | Dedicated `unsloth` Compose profile |
| Immutable supply chain | Studio image reference requires a digest |
| No public listener | No host port; private Compose network only |
| No implicit tunnel | No `--secure` or `--cloudflare` process argument |
| Executable Studio tools | Upstream process receives `--disable-tools` |
| Hub boundary | Studio mutations become authenticated Hub commands |
| Browser boundary | Angular has no direct Studio or worker URL |
| Secrets | References or mounted files, never committed plaintext |

These controls do not alter or remove upstream license obligations.

## Model and dataset licenses

The Unsloth software license does not grant rights to a model, tokenizer,
dataset, generated artifact, or model output. Each imported model snapshot and
dataset snapshot requires its own license metadata and approval before live
training. Merge and GGUF export do not change the source model license.

Fail-closed admission must reject:

| Condition | Required result |
| --- | --- |
| Missing model license metadata | Import unavailable |
| Unapproved model license | Import unavailable |
| Missing dataset license review | Recipe and training unavailable |
| Unapproved dataset license | Recipe and training unavailable |
| License changes at an immutable source ID | New catalog version and review |

## Required release evidence

The following values cannot be manufactured by source code and remain
unverified until supplied by their authoritative systems:

| Evidence | Authority |
| --- | --- |
| Studio runtime image digest | Container registry and runtime inspection |
| Upstream source commit, if a source build is used | Approved source mirror |
| `SRC_*` identifiers | Ananta source/evidence catalog |
| `RUN_*` identifiers | Ananta run/evidence catalog |
| GPU driver and CUDA runtime | Attested self-hosted runner |
| License approval | Designated organizational reviewer |
| Branch protection | GitHub repository administration |

An absent value is not a warning-only state. It prevents the corresponding
release claim from becoming verified.

## Update procedure

1. Change the executable pin or image digest input.
2. Review upstream package metadata, license files, and release notes.
3. Update this register without inventing evidence identifiers.
4. Run the CPU contract gate.
5. Run the manual GPU gate with an approved local model.
6. Supply authoritative source and run IDs to the attestation command.
7. Obtain the required organizational license and release approvals.

The technical gate records evidence but cannot grant an organizational
approval.

## Optional multi-backend training baseline

Baseline date: 2026-08-27. The machine-readable source of truth is
`config/licenses/training-backends.v1.json`.

| Component | Pin | License | Maintenance | Ananta boundary |
| --- | --- | --- | --- | --- |
| Axolotl | `0.18.0` / `2f5cb9d…` | Apache-2.0 | Active | Isolated optional worker image; no WebUI, raw CLI args or egress |
| LlamaFactory | `0.9.5` / `7af9095…` | Apache-2.0 | Active | Isolated optional worker image; CLI only behind typed compiler |
| AutoTrain Advanced | `0.8.36` / wheel `03e5400…` | Apache-2.0 | Upstream unmaintained | Local-only contract adapter, experimental and default-off |
| torchtune | `0.6.1` / `a6290a5…` | BSD-3-Clause | Development wound down in 2025 | Allowlisted recipes only, experimental and default-off |

Every live image still requires a dependency lock, SBOM, vulnerability scan,
container digest and explicit release approval. The two unmaintained projects
cannot become production-ready merely by passing contract tests.

## Optional DSPy optimization worker

Baseline observation date: 2026-08-29. The optional worker pins `dspy==3.2.1`;
the official release tag resolves to commit `29448ae12756abdd14bd8796c819247ebb83673c`.
DSPy declares the MIT license with copyright held by Stanford Future Data
Systems. Its package metadata includes OpenAI, LiteLLM, Pydantic, Diskcache,
Cloudpickle and GEPA among the direct dependency set.

This is an unverified navigation baseline, not release evidence: no allowed
`SRC_*` reference has been supplied. A release still requires an immutable
source-catalog binding, complete transitive license/security inventory,
dependency lock, SBOM, vulnerability scan, built image digest and allowed
`RUN_*` evidence. Until then the optional image and production release remain
blocked even when local contract tests pass.

Cloudpickle may be present transitively but Ananta forbids executable program
deserialization. DSPy imports stay in `worker/optimization/dspy/`; the Hub,
contracts and standard worker remain dependency-free.
