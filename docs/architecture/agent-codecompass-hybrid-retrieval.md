# Agent → CodeCompass Hybrid Retrieval

Stand: 2026-08-17 · Track: `agent-codecompass-hybrid-retrieval-qdrant-integration`

## Canonical path

```text
Agent / Worker tool / MCP / n8n
  -> codecompass.search | codecompass.retrieve
  -> CodeCompassAgenticRetrievalService
  -> CodeCompassAgenticRetrievalPlanner
  -> HybridRetrievalService (merge, rerank, budget)
  -> exact KnowledgeIndex + graph expansion + VectorStore port
  -> QdrantVectorStore (internal only)
  -> budgeted evidence envelope
```

Qdrant is an internal VectorStore backend. Agents never receive collection
names, endpoints or credentials. The public contract is
`schemas/codecompass.agentic-retrieval.v1.json`.

## What already existed

- `HybridOrchestrator` / `ContextManager` for repository-map vs vector ranking
- `HybridRetrievalService` for channel merge, dedup and CodeCompass budgets
- `QdrantVectorStore` + fail-closed `QdrantFilterBuilder`
- Worker tools `codecompass.search`, `resolve_context`, `plan_context`
- Vector index refresh keyed by manifest hash, model, dimensions and
  `embedding_text_profile`

## What this track unifies

- one request/response envelope for Worker, MCP and n8n
- deterministic signal planning (`auto` / `hybrid` / `vector` / `exact` / `graph`)
- typed, secret-free backend errors (`vector_unavailable`, `vector_stale_index`, …)
- server-side capability intersection so requests cannot widen scope

## Integrations

| Surface | Entry | Notes |
|---|---|---|
| Worker tool loop | `codecompass.search`, `codecompass.retrieve` | Search keeps the legacy evidence envelope and embeds the contract in `data.retrieval`. |
| MCP | `codecompass.retrieve` | Adapter only. Optional server capability from `AGENT_CONFIG.codecompass_retrieval.capability`. |
| n8n | `POST /api/codecompass/retrieve` | See `docs/codecompass-agentic-retrieval-n8n.md`. |

Hierarchical architecture context (`todo.codecompass-hierarchical-architecture-context.json`)
must call this service for prefill and expand/zoom. It must not grow a
second vector/graph pipeline.

## Troubleshooting

| Symptom | Meaning |
|---|---|
| `empty_scope` | Bound capability missing workspace, revision or allowed paths. |
| `scope_widening_denied` | Request tried to leave the server capability. |
| `vector_unavailable` / `vector_timeout` | Qdrant down or slow. Exact/graph may continue; status is `degraded`. |
| `vector_stale_index` | Manifest/model/dimensions/profile changed. Do not keep serving the old index. |
| `vector_fail_closed` | Unauthorized or incompatible collection. No global fallback. |
| `unknown_retrieval_mode` | Only `auto`, `hybrid`, `vector`, `exact`, `graph`. |
| `no_result` | Nothing inside the scoped budget. |
