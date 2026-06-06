# Model Routing and RAG Deployment Readiness

## Defaults

Local-first deployment:

```env
DEFAULT_PROVIDER=ollama
DEFAULT_MODEL=qwen2.5-coder:7b
MODEL_PROFILES_PATH=config/models/examples/local-ollama-rtx3080.model_profiles.yaml
CODECOMPASS_VECTOR_ENABLED=0
RAG_QUERY_NORMALIZE_MODE=keyword
```

`MODEL_PROFILES_PATH` enables profile routing. `DEFAULT_PROVIDER` and
`DEFAULT_MODEL` remain fallback values for rollback and older integrations.

## Hybrid Routing

Use `config/models/examples/hybrid-local-cloud.model_profiles.yaml` when cloud
profiles are required. Cloud profiles must keep `block_secret_context=true` and
must use `api_key_env`; never store API keys in profile files.

## Embeddings

The default embedding provider is offline `local_hash`. External embedding calls
require explicit provider config in `AGENT_CONFIG` with:

- `external_calls_allowed=true`
- `allowed_base_urls`
- `api_key_ref`

## Worker Handoff

Worker context handoff uses `worker_context_handoff.v3`. The hub builds the
payload, diagnostics, and policy metadata. Workers receive delegated context and
do not orchestrate other workers.

## Related Docs

- `docs/operator/model-routing.md`
- `docs/migrations/model-routing-profiles-migration.md`
- `docs/worker/embedding-provider-config.md`
- `docs/architecture/codecompass-worker-file-context-handoff.md`
- `docs/architecture/snakechat-routing-decision-model.md`
