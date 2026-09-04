# Ornith 1.5 source and model matrix

Baseline: 2026-09-04. The machine-readable sources are
`config/models/ornith-1.5-sources.v1.json`; this document does not create
release evidence.

| Variant | Official revision | Local artifact | Local target | State |
| --- | --- | --- | --- | --- |
| 9B dense | `489cb979…` | official GGUF Q4_K_M, `70c112…8fab6`, 5,780,090,816 bytes | RTX 3080 10 GiB | evaluation only |
| 35B-A3B MoE | `10fbf86f…` | official GGUF Q4_K_M, `427398…9d41f`, 21,713,463,040 bytes | 64 GB RAM plus GPU offload | evaluation only |
| 397B MoE | `8f6cc8a7…` | no admitted local weights | unsupported locally | unavailable |

The GGUF repositories are separately pinned at `abdd624b…` (9B) and
`12393612…` (35B). Config, template, GGUF and vision projection have distinct
digests in the manifest. Floating tags and community conversions are excluded.

Upstream model-card metadata declares MIT. At the audited revisions the linked
license text was not present in the repositories, so the license status remains
`declared`, not `approved`. This is the explicit
`model_license_text_missing` production blocker. Runtime, dataset and benchmark
licenses remain separate decisions.

Upstream describes text, reasoning, tools and multimodal input and a nominal
262,144-token context. Those are declared claims only. Ananta keeps JSON,
streaming, runtime parser behavior, practical context and vision unknown until
an exact artifact/runtime/hardware run is bound to Hub evidence. A3B describes
active computation per token; it does not reduce the resident 35B weight set to
3B.

The upstream vLLM recipe requests remote code. Ananta forbids it, therefore the
vLLM path is incompatible by default. Ollama, LM Studio/llama.cpp and optional
SGLang remain candidates subject to exact runtime-version and parser checks.
