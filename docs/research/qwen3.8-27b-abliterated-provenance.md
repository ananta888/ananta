# Qwen3.8-27B abliterated provenance

Audited 2026-09-04. The official base is pinned at `1d4bf0f…`; the community
safetensors derivative at `739e3c5b…`; and the GGUF repository at `6386362d…`.
Exact file sizes and SHA-256 values are recorded in
`config/models/qwen3.8-27b-abliterated-sources.v1.json`.

The base and derivative repositories contain matching Apache-2.0 license text.
The GGUF repository declares Apache-2.0 and is tied to that derivative, but is
still evaluation-only. The community description calls the work a proof of
concept. Claims that MTP and vision were not modified are declared, not
verified end-to-end facts.

UD and UD-DW lines are distinct identities. Unknown lineage stays unknown;
quantization names never supply missing provenance. Q2_K is 10,864,592,160
bytes, so it does not fit wholly in 10 GiB VRAM with runtime reserve.
