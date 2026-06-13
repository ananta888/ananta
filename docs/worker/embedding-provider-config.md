# Embedding Provider Configuration

## Scope

Ananta uses one canonical embedding provider contract for worker retrieval,
CodeCompass vector search and semantic output correction.

The implementation is split intentionally:

| Area | File | Responsibility |
|------|------|----------------|
| Config model and policy | `agent/services/embedding_provider_config_service.py` | Normalize config, enforce external-call policy, expose diagnostics |
| Provider implementations | `worker/retrieval/embedding_provider.py` | Hash, fake and OpenAI-compatible provider execution |
| Worker retrieval index | `worker/retrieval/index_builder.py` | Build incremental indexes with provider metadata |
| CodeCompass vector store | `worker/retrieval/codecompass_vector_store.py` | Store vectors and detect provider/model/dimension mismatch |
| CodeCompass engine | `worker/retrieval/codecompass_vector_engine.py` | Resolve provider for `codecompass_vector` scope |
| Semantic correction | `worker/coding/semantic_output_correction.py` | Use the shared provider config for enum correction |
| Embedding text | `worker/retrieval/embedding_text_builder.py` | Build deterministic text input, not provider selection |
| RAG helper text generation | `rag-helper/rag_helper/utils/embedding_text.py` | Produce `embedding_text` records for helper outputs |

## Default Policy

Default config is offline and deterministic:

```json
{
  "embedding_provider": {
    "provider": "local_hash",
    "model_version": "hash-v1",
    "dimensions": 12,
    "external_calls_allowed": false
  }
}
```

`local_hash` is suitable for tests and pipeline safety. It proves that the
retrieval flow works, but it is not a semantic-quality substitute for a real
embedding model.

## OpenAI-Compatible Providers

External or local OpenAI-compatible providers require explicit opt-in:

```json
{
  "embedding_provider": {
    "provider": "openai_compatible",
    "model": "nomic-embed-text",
    "model_version": "nomic-embed-text",
    "dimensions": 768,
    "base_url": "http://localhost:11434/v1",
    "external_calls_allowed": true,
    "allowed_base_urls": ["http://localhost:11434"],
    "api_key_ref": "OLLAMA_EMBEDDING_API_KEY"
  }
}
```

Security rules:

- `external_calls_allowed=false` blocks all OpenAI-compatible providers.
- `allowed_base_urls` is matched by parsed scheme, host, port and path boundary.
- API keys are not serialized in diagnostics or index files.
- Embedding requests transmit chunk text to the provider. Treat external
  providers as potential data egress.

## Scope Overrides

Use overrides when one subsystem needs different dimensions or a different
provider:

```json
{
  "embedding_provider": {
    "provider": "local_hash",
    "dimensions": 12
  },
  "embedding_provider_overrides": {
    "codecompass_vector": {
      "provider": "local_hash",
      "dimensions": 16
    },
    "semantic_output_correction": {
      "provider": "local_hash",
      "dimensions": 12
    }
  }
}
```

Supported scopes are `worker_retrieval`, `codecompass_vector`,
`semantic_output_correction` and `rag_helper`.

## Rebuild Rules

Indexes must not mix vectors from different provider identities.

Rebuild or degrade when any of these change:

- provider id
- model/model version
- dimensions
- embedding text profile
- provider config hash
- manifest hash and retrieval cache state for CodeCompass vector indexes

The vector store records provider metadata without secrets. Query-time dimension
mismatch returns a degraded diagnostic instead of mixing incompatible vectors.

For the full CodeCompass flow, see
[`docs/worker/codecompass-vector-retrieval.md`](codecompass-vector-retrieval.md).

## Diagnostics

The config service returns `ready`, `degraded` or `blocked` per scope.

Common reasons:

| Reason | Meaning | Action |
|--------|---------|--------|
| `external_calls_not_allowed` | External provider configured without opt-in | Set `external_calls_allowed=true` only for approved endpoints |
| `base_url_not_in_allowed_list` | Endpoint does not match allowed URL origins | Add the exact local/approved base URL |
| `missing_base_url` | External provider has no endpoint | Configure `base_url` |
| `fake_provider_not_for_production` | Fake provider is active | Use only in tests |
| `dimension_mismatch` | Query vector and stored index dimensions differ | Rebuild index with the active provider |

## SOLID Review

- SRP: provider execution, config policy and text construction stay separate.
- DIP: worker code depends on the provider protocol, not a concrete service.
- OCP: new providers should be added behind the existing provider contract and
  config service instead of patching every retrieval caller.
