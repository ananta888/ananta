# CodeCompass Agentic Retrieval for n8n

Stand: 2026-08-17 · Track: `agent-codecompass-hybrid-retrieval-qdrant-integration`

n8n soll dieselbe Retrieval-Anfrage stellen wie ein Ananta-Worker.
Der Produktvertrag ist **nicht** ein Qdrant-Node, sondern der versionierte
CodeCompass-Contract `codecompass.agentic-retrieval.v1`.

## Empfohlener Weg

```text
n8n HTTP Request oder MCP Tool
  -> CodeCompass retrieve
  -> Planner + HybridRetrievalService
  -> Evidence-Kontext
  -> LLM Node
```

### HTTP

`POST /api/codecompass/retrieve`

Authentifizierung: bestehender Hub-Auth-Header.

Beispiel-Body:

```json
{
  "query": "Wo wird PaymentService.retry_timeout implementiert?",
  "mode": "auto",
  "task_kind": "bugfix",
  "budget": { "top_k": 6, "max_chars": 4000 }
}
```

Die Antwort ist der Contract (`schema: codecompass.agentic-retrieval.v1`,
`kind: response`). Felder wie `collection`, `qdrant`, `api_key` oder
`capability` werden abgelehnt.

### MCP

Tool-Name: `codecompass.retrieve`

Dasselbe Argument-/Ergebnis-Schema. MCP darf den serverseitigen Scope
nicht erweitern. Eine optionale Server-Capability liegt in
`AGENT_CONFIG.codecompass_retrieval.capability`.

## Beispiel-Workflow

1. Trigger: Chat/Webhook mit einer Projektfrage.
2. HTTP Request Node gegen `/api/codecompass/retrieve` **oder** MCP
   `codecompass.retrieve`.
3. LLM Node bekommt nur `evidence[].excerpt`, `path`, `signals` und
   `diagnostics.plan`.
4. Antwort speichern oder in den Ananta-Worker zurückgeben.

Qdrant-Credentials sind in diesem Workflow nicht nötig.

## Debug-Alternative

Ein direkter Qdrant-Node ist nur Low-Level-Diagnose. Er ist kein
Produktvertrag, umgeht Scope/Budget und darf nicht der Standardpfad
für Agenten oder n8n-Produktivläufe sein.

## Troubleshooting

| Symptom | Bedeutung |
|---|---|
| `empty_scope` | Server-Capability ohne Workspace/Revision/Paths. Fail-closed. |
| `scope_widening_denied` | Request wollte Tenant/Workspace/Revision/Pfad erweitern. |
| `vector_unavailable` | Qdrant down; exact/graph dürfen weiterlaufen, Status ist `degraded`. |
| `unknown_retrieval_mode` | Nur `auto`, `hybrid`, `vector`, `exact`, `graph`. |
| `no_result` | Kein Treffer innerhalb des Scopes. |
