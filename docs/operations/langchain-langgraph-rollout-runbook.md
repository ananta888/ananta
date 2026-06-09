# LangChain and LangGraph Rollout Runbook

Date: 2026-06-09
Companion to: [ADR-langchain-langgraph-worker-adapters.md](decisions/ADR-langchain-langgraph-worker-adapters.md), [langchain-langgraph.md](setup/langchain-langgraph.md), [langchain-langgraph-adapters.md](architecture/langchain-langgraph-adapters.md)

This runbook describes the safe activation of the optional
LangChain and LangGraph worker adapters. The rollout is staged
because both adapters introduce new execution surfaces (a chain
runtime, a graph runtime) that the rest of Ananta does not yet
exercise in production.

## Zweck

Bring LangChain and LangGraph from "default-off, dry-run safe" to
"profile-opt-in live" without breaking the existing n8n / webhook
workflow integration. The path is opt-in at every step; the Hub
remains the only place where the rollout decision is made.

## Architekturgrenzen

- Hub bleibt Eigentümer von Routing, Policy und Task-Queue.
- LangChain/LangGraph-Ausführung erfolgt als delegierter
  Worker-Pfad mit eigenem Adapter und eigenem Policy-Gate.
- Keine Worker-zu-Worker-Orchestrierung.
- CodeCompass bleibt der einzige Retriever-Source für beide
  Adapter (LCG-001). Auch ein aktivierter LangChain-Chain
  bekommt Kontext ausschließlich aus CodeCompass.
- Lokale-first: Default-Mode ist `dry_run`; `local_live` ist
  explizit zu aktivieren; `cloud_gated` erfordert
  `external_calls_allowed=true` (Hub-Approval).

## Phasen

### Phase 0 — Default-off (Status: produktiv, dieser Runbook-Stand)

Beide Adapter sind in der Codebase vorhanden, das
`pip install ananta`-Paket zieht sie nicht. Die Registry listet
sie mit Status `disabled` (oder `degraded` für diejenigen
Nutzer, die `ananta[langchain]` installiert haben, ohne den
Adapter zu aktivieren). Kein Verhalten ändert sich für
bestehende Nutzer.

Smoke-Tests laufen ohne Framework-Installation: 15 Tests in
`tests/test_workflow_lc_lg_smoke.py`, 94 LCG-Tests insgesamt.

### Phase 1 — Lokales dry-run (Opt-in per Profil)

Ziel: ein Profil aktiviert `langchain` und/oder `langgraph`
explizit für `mode=dry_run`, der Hub routet einen realen Task an
den Adapter, der Adapter produziert einen `DryRunResult` mit
Plan, Policy-Decisions und Audit-Trace. Keine LLM-Calls.

Aktivierung pro Profil (siehe
`docs/architecture/profile-snippet-langchain-langgraph.json`):

1. `pip install 'ananta[lc-lg]'` auf dem Hub-Container
2. Profil kopieren: `cp docs/architecture/profile-snippet-langchain-langgraph.json config/profiles/<user>/providers.json`
3. In `config/profiles/<user>/profile.json` mergen:
   ```json
   {
     "providers": {
       "langchain": {
         "enabled": true, "mode": "dry_run",
         "allowed_tools": ["summarize_doc"]
       }
     }
   }
   ```
4. Hub neu starten
5. Smoke-Check: `curl http://hub:5000/api/workflow_adapters/`
   muss `adapter.langchain` mit `status: "ready"` und
   `reason: "dry_run_mode"` zurückgeben.

Wenn Schritt 5 grün ist, Phase 1 abgeschlossen.

### Phase 2 — Lokales live (Opt-in, ein Task-Typ)

Ziel: ein einzelner realer Task-Typ (z. B. `summarize`) wird
tatsächlich von der LangChain-Kette ausgeführt. Der Chain-Plan
ist im `DryRunResult` sichtbar, also kann der Nutzer vorher
trocken prüfen, was passieren wird.

Vorbedingungen:

- Phase 1 abgeschlossen
- `local.default`-Modell läuft (Ollama, llama.cpp etc.)
- `allowed_tools` enthält genau die Werkzeuge, die der Chain
  verwenden darf
- `external_calls_allowed: false` (default)
- `artifact_first: true` (default)

Aktivierung:

1. `mode: "local_live"` setzen
2. Ersten realen Task starten, Output beobachten:
   - `execution_trace` muss `codecompass_query` und
     `langchain_rag_query` enthalten
   - `policy_decisions` muss für jeden Tool-Call den
     `allowlisted`/`always_blocked`-Grund nennen
   - `audit_log` (via `execution_trace`) zeigt pro Task genau
     die Events des Tasks, nicht eines vorherigen
3. Wenn etwas blockiert wird: `block_reason` lesen. Häufig:
   - `default_deny_empty_allowlist` → `allowed_tools` ergänzen
   - `tool_blocked:<name>` → Tool-Name prüfen, in Allowlist
     aufnehmen oder Tool aus Chain-Descriptor entfernen
   - `external_calls_blocked` → entweder Hub-Approval holen
     oder externes Tool aus Chain entfernen

Erfolgs-Kriterium: ein `summarize`-Task erzeugt einen
`artifact_write`-Eintrag mit der erwarteten Markdown-Antwort,
Audit-Trace endet mit `live_execution_complete` (nicht
`execute_blocked`).

### Phase 3 — Multi-task und Graph

LangGraph-Adapter wird aktiviert. Der Graph-Descriptor wird
aus `examples/langgraph/graph.example.research_summarize.v1.json`
kopiert und an die Domäne angepasst.

1. `mode: "local_live"` für `langgraph`
2. `human_in_loop_required_for: [...]` enthält die Actions, die
   der Graph ohne menschliche Bestätigung **nicht** ausführen
   darf (Default-Set im `LangGraphProviderConfig.default_off()`)
3. `checkpoint_policy: "local_ephemeral"` (default) — Graph-State
   wird nur im Container gespeichert
4. Erster Graph-Lauf: `dry_run` zeigt alle Knoten als
   `node:<id>`-Plan-Steps, Human-Gate-Knoten triggern
   `approval_required: true`

### Phase 4 — Cloud-gated (Hub-Approval erforderlich)

`mode: "cloud_gated"` aktiviert externe Calls. Vorbedingung:

- `external_calls_allowed: true`
- Hub-Approval dokumentiert in `docs/decisions/`
- Geheimnisse in `secret_refs`, nicht in `metadata`
- `allowed_base_urls` (falls in der Provider-Config vorhanden)
  explizit gesetzt

Diese Phase wird im ersten Anlauf **nicht** aktiviert. Sie ist
hier nur dokumentiert, damit klar ist, wie der Pfad aussähe.

## Smoke-Checks (jede Phase)

```bash
# 1. Registry-Status
curl -fsS http://hub:5000/api/workflow_adapters/ | jq '.[] | {id:.adapter_id, status, enabled}'

# 2. Default-off bleibt default-off
#    (kein Profil soll versehentlich Live-Execution bekommen)
curl -fsS http://hub:5000/api/workflow_adapters/ | jq '.[] | select(.enabled==true) | .adapter_id'

# 3. LCG-Tests grün
python -m pytest tests/test_workflow_lc_lg_*.py -q

# 4. Examples validieren weiter
python -m pytest tests/test_workflow_lc_lg_examples.py -q

# 5. Backwards-Compat: pre-LCG Tests laufen
python -m pytest tests/test_workflow_n8n_provider.py \
                tests/test_workflow_provider_contract.py \
                tests/test_workflow_registry.py -q
```

## Rollback

Jede Phase ist umkehrbar, indem das Profil `enabled: false`
setzt und der Hub neu startet. Es gibt keine Schema-Migration,
kein Lock — der Rollback ist ein JSON-Flip.

```bash
# Sofort-Rollback: alle LCG-Provider deaktivieren
jq '.providers.langchain.enabled = false
    | .providers.langgraph.enabled = false' \
   config/profiles/<user>/profile.json > /tmp/profile.json
mv /tmp/profile.json config/profiles/<user>/profile.json
# Hub neu starten
```

## Beobachtung

Pro Task werden folgende Events im Audit-Log
(`artifacts/audit/workflow_audit.jsonl`) erwartet:

- `dry_run_start` (bei `dry_run`-Aufrufen)
- `dry_run_complete`
- `execute_start`
- `tool_checked` (mit `decision.allowed` und `decision.reason`)
- `live_execution_complete` oder `execute_blocked`

Das Format ist `WorkflowAuditLog.snapshot()` — pro Task
isoliert, kein Leak über Tasks hinweg
(getestet in `test_workflow_lc_lg_audit.py`).

## Verwandte Dokumente

- ADR: `docs/decisions/ADR-langchain-langgraph-worker-adapters.md`
- Architektur: `docs/architecture/langchain-langgraph-adapters.md`
- Boundary: `docs/architecture/codecompass-vs-langchain.md`
- Setup: `docs/setup/langchain-langgraph.md`
- Profile-Snippet: `docs/architecture/profile-snippet-langchain-langgraph.json`
- Beispiele: `examples/langchain/*.json`, `examples/langgraph/*.json`
- Verträge: `docs/contracts/langchain-chain-descriptor.schema.json`,
  `docs/contracts/langgraph-graph-descriptor.schema.json`
