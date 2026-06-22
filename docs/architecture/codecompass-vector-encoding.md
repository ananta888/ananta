# CodeCompass Vector Encoding

CodeCompass vector encoding is the Ananta seam for using model/agent-generated vector features without making the model the authority.

The important split is:

```text
CodeCompass records
  -> embedding_text
  -> EmbeddingProvider or future TransformerFeatureProvider
  -> VectorEncodingProfile
  -> VectorEncoder
  -> CodeCompassVectorStore
  -> deterministic vector search
  -> HybridOrchestrator rerank
  -> ContextTrace / diagnostics
```

## Why this exists

TurboQuant shows that high-dimensional model vectors often do not need full float precision for useful downstream work. For Ananta this is not mainly a stock-market or RAM story. It is an architecture story:

- AI/model layers may produce features.
- Ananta stores those features in an explicit, reproducible format.
- Retrieval and routing remain deterministic and auditable.
- Exact repository signals can still outrank fuzzy vector signals.
- Other AI agents can be used as workers/providers without becoming hidden authorities.

## Current modes

`worker.retrieval.vector_encoding.VectorEncodingProfile` currently supports:

- `off` / `float32`: backwards-compatible raw float storage.
- `float16`: compact baseline encoding.
- `int8`: symmetric int8 quantization.
- `symmetric4bit`: experimental 4-bit scalar quantization.
- `turboquant_mse_experimental`: TurboQuant-inspired seam using deterministic sign rotation plus 4-bit scalar quantization.

The last two are intentionally marked experimental. The current `turboquant_mse_experimental` mode is not a full implementation of TurboQuant-prod. It is the production seam where a real codebook/rotation implementation can land without changing the store/orchestrator contract.

## Store contract

`CodeCompassVectorStore` writes schema `codecompass_vector_index.v2` when vector encoding metadata is present.

State includes:

- `vector_encoding_profile`
- `vector_encoding_config_hash`
- `vector_encoding_compression_ratio`
- `vector_encoding_max_abs_error`

Entries may contain:

- `vector` for backwards-compatible raw storage.
- `encoded_vector` for encoded storage.

When an encoding profile changes, `refresh()` returns reason `vector_encoding_changed` and rebuilds the index.

## Safety boundary

This is deliberately not hidden LLM magic.

- Encoding is optional.
- Default remains safe/backwards-compatible.
- Diagnostics expose mode, hash, compression and error, not raw vectors or secrets.
- Experimental modes must be visible in UI/CLI before being recommended.
- Future TransformerFeatureProvider output must enter the same VectorEncoding layer.

## Bewerbungs-/Demo-Satz

Ananta can use model or agent-generated vector features as compressed, auditable retrieval signals while deterministic policy, domain scope and hybrid ranking remain in control.
