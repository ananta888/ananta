# Scope Boundary: CodeCompass TurboQuant vs. LLM KV-Cache

**Status:** Reference  
**Date:** 2026-06-22  
**Author:** Ananta Architecture  
**Risk:** False-expectation risk is explicitly documented here.

---

## What Ananta Implements

Ananta implements **CodeCompass index quantization**: the process of encoding the float32 vectors in `CodeCompassVectorStore` into a more compact representation (float16, int8, 4bit, or TurboQuant-inspired rotation + 4bit).

Location: `worker/retrieval/vector_encoding.py`  
Store: `worker/retrieval/codecompass_vector_store.py`  
Index file: `codecompass_vector_index.v2` (JSON on disk)

This is:
- CPU-side Python code.
- Applied once during index build (`rebuild`) or refresh.
- Stored as base64-encoded payloads in JSON.
- Retrieved and decoded at query time before cosine similarity computation.
- Auditable via `EncodedVector.diagnostics` (compression_ratio, max_abs_error, mode).

---

## What Ananta Does NOT Implement

### LLM KV-Cache Quantization

The TurboQuant paper (arxiv 2504.19874) was motivated in part by KV-Cache quantization in large language model servers. This refers to:

- The Key and Value tensors produced by each transformer layer during LLM inference.
- Stored in GPU VRAM to avoid recomputation for cached context.
- Quantized to reduce VRAM usage for long context windows.
- Implemented inside LLM serving frameworks such as vLLM, llama.cpp, Ollama, or TGI.

**Ananta does not touch this.**

No Ananta TODO, no implementation file, and no configuration setting relates to:

- GPU kernel optimization for KV-Cache storage.
- Patching vLLM, Ollama, llama.cpp, or any other inference server.
- Intercepting or modifying the KV-Cache of a running model.
- Training or fine-tuning quantization codebooks for transformer layers.

---

## Why This Boundary Matters

When reading about TurboQuant and KV-Cache, it is tempting to conflate:

1. *"TurboQuant compresses vectors for vector search"* → Ananta implements this (CodeCompass index).
2. *"TurboQuant compresses KV-Cache inside an LLM server"* → Ananta does not implement this and has no current plans to do so.

These are different systems, different layers, different code, and different hardware. Conflating them produces false expectations:

- "Does Ananta make LLM inference faster?" → No, this is not the goal.
- "Does Ananta save GPU VRAM?" → No.
- "Does Ananta require changes to Ollama internals?" → No.
- "Can Ananta quantize any model's KV-Cache?" → No.

---

## Risk of False Expectations

This section is intentional. The following claims are **false** and must not appear in demos, README files, or job application materials:

| False Claim | Reality |
|---|---|
| "Ananta implements TurboQuant for LLM inference" | Ananta implements index quantization for CodeCompass vector search |
| "Ananta's turboquant_mse_experimental is production-grade TurboQuant" | It is a seam: rotation + 4bit scalar quantization, not a full paper implementation |
| "VectorEncoding speeds up model inference" | It reduces index storage size; inference speed is unrelated |
| "Ananta patches or extends vLLM / Ollama" | No; Ananta calls these as external services at the API level only |

---

## Future Adapter Possibility (Local Inference Only)

A future path exists for adapting Ananta to coordinate with **local** inference engines that expose their KV-Cache state via an API (e.g., a hypothetical `ollama-extended` endpoint that reports per-layer activations).

This is:
- A future research path, not a current TODO.
- Only relevant for fully local inference engines under operator control.
- Not a prerequisite for any current feature.
- Not dependent on GPU kernels or vLLM patches.

If this path is pursued, it would be documented as a separate ADR and implemented as an optional `TransformerFeatureProvider` adapter, not as a modification to the base quantization layer.

---

## Prerequisites for Current Roadmap

The following are **not** prerequisites for the current CodeCompass VectorEncoding roadmap:

- GPU with CUDA support.
- vLLM installation.
- Ollama source code access.
- Any model weight files beyond what EmbeddingProvider already uses.
- Custom CUDA kernels.
- Triton or PyTorch custom ops.

The current implementation requires only: Python 3.11+, `struct`, `hashlib`, `base64` (all stdlib).

---

## Related

- `docs/research/turboquant-for-codecompass.md` — full research overview (Deutsch)
- `worker/retrieval/vector_encoding.py` — actual implementation
- `docs/architecture/transformer-feature-provider.md` — future local model feature extraction
