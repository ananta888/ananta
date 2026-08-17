# CodeCompass Vector Retrieval

## Flow

Agent, MCP and n8n consumers must use the canonical
[agentic retrieval contract](../architecture/agent-codecompass-hybrid-retrieval.md)
instead of talking to Qdrant directly.

CodeCompass vector retrieval is an optional hybrid-retrieval signal:

```text
CodeCompass
-> rag-helper/out/embedding.json
-> CodeCompassVectorRetrievalService
-> CodeCompassVectorStore
-> query embedding
-> HybridOrchestrator rerank
-> prompt context
```

`embedding.json` is a model-neutral CodeCompass output. It contains
`embedding_text` and metadata, not a guarantee that model vectors already exist.
The configured embedding provider turns that text into numeric vectors when the
VectorStore is refreshed.

## embedding.json Contract

Each embedding record should contain:

- `id` or `_provenance.record_id`
- `kind`
- `file` or `path`
- `embedding_text`
- `_provenance.output_kind = "embedding"`

Example:

```json
{
  "id": "emb-payment-service",
  "kind": "python_function",
  "file": "src/payment.py",
  "embedding_text": "PaymentService retry timeout handler",
  "_provenance": {
    "output_kind": "embedding",
    "record_id": "emb-payment-service"
  }
}
```

The loader also accepts a structured payload with a top-level `records` array.

## Manifest Contract

The manifest should expose:

- `manifest_hash`
- `profile_name`
- `source_scope`
- `retrieval_cache_state` when available

`manifest_hash`, provider config hash, model version, dimensions and
`embedding_text_profile` are index-state inputs. Any change rebuilds the vector
index deterministically.

## Provider Policy

The default provider is `local_hash`. It is offline and deterministic, useful for
tests and safe bootstrapping, but it is not a semantic-quality substitute for a
real embedding model.

Ollama and LM Studio are supported through the OpenAI-compatible provider only
when explicitly enabled:

- the task names an immutable `embedding.policy_profile`;
- the full embedding mapping exactly matches that Hub deployment profile;
- the Worker has the same canonical HTTPS origin in its separate immutable
  egress allowlist;
- no plaintext API key is written to index state or diagnostics

External providers may receive repository or documentation text. Keep cloud
providers default-deny unless data egress is explicitly approved.
See the [task and egress boundary](qdrant-vector-index-taskflow.md) for the
complete profile, secret-reference and pre-I/O validation contract.

## Hybrid Ranking

`codecompass_vector` is a fuzzy semantic signal. It does not override exact
repository-map matches by default. The ContextManager weights engines so exact
symbol, path and graph-like repository signals can stay ahead of only-similar
vector hits. When two engines identify the same source file, the chunk is
deduplicated and annotated with `metadata.cross_engine_signals`.

## Domain Scope

Domain scope applies to vector retrieval. Active `allowed_read_paths` are passed
to the CodeCompass vector service before searching and are enforced again by the
domain-scope filter before prompt construction. Empty or invalid scope must not
fall back to global vector search.

Productive Qdrant reads additionally require the Hub-authoritative,
task-bound runtime scope described in the
[Hub read-capability runbook](qdrant-vector-store-hub-read.md). Request fields
cannot create or widen this capability.

## Diagnostics

Hybrid context results include `retrieval_diagnostics.codecompass_vector`.
Diagnostics may include status, reason, manifest hash, dimensions, model name,
entry count and embedding text profile. They must not include secrets,
authorization headers or full raw chunk payloads.
