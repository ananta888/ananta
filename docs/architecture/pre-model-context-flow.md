# Pre-Model Context Flow — Ist-Zustand und APMCO-Architektur

*APMCO-001 Bestandsanalyse + APMCO-002 Architektur-Referenz*

---

## Ist-Zustand: ai-snake-chat Kontextflow

### Aktivierte Settings (chat_state.py, _ai_snake_config_helpers.py)

| Setting | Default | Wirkung |
|---|---|---|
| `chat_use_codecompass` | `True` | CodeCompass-Kontext vor Backend-Aufruf einbinden |
| `chat_rag_top_k` | konfigurierbar | Anzahl RAG-Snippets |
| `chat_context_chars` | 4000 | Budget für den Kontext im Prompt |
| `chat_pass_memory_to_worker` | konfigurierbar | Memory an Worker weitergeben |
| `chat_backend` | konfigurierbar | Backend (z.B. LMStudio, OpenAI) |
| `chat_backend_fallback` | konfigurierbar | Fallback-Backend |

### Kontextaufbau — existierender Flow

```
ChatHistoryManagerMixin.build_memory_context()
  → ChatMemoryContext(
        active_target_excerpt,
        rolling_summary,
        recent_turns,
        codecompass_refs,    ← aus CodeCompass/RAG
        rag_snippets,        ← aus RAG-Helper-Index
        runtime_status
    )
  → ChatPromptBuilder.build()
      → PromptBuildResult(messages, prompt_text, worker_v2_payload, worker_v3_payload)
```

**Dateipfade:**
- `client_surfaces/operator_tui/chat_mixin.py` — `_chat_send_message()`, `_tick_chat()`
- `client_surfaces/operator_tui/chat_history_manager.py` — `ChatHistoryManagerMixin`, ruft `build_memory_context()` auf; trägt `codecompass_refs` und `rag_snippets` in `ChatMemoryContext` ein
- `client_surfaces/operator_tui/chat_prompt_builder.py` — `ChatPromptBuilder.build()` — Budget-Policy: active_target → rolling_summary → recent_turns → codecompass → rag
- `client_surfaces/operator_tui/chat_memory.py` — `ChatMemoryContext` dataclass mit `codecompass_refs: list[str]`, `rag_snippets: list[str]`
- `client_surfaces/operator_tui/chat_state.py` — Settings-Defaults (z.B. `chat_use_codecompass: True`)

### Fazit APMCO-001

Der **bestehende Flow ist Surface-spezifisch** und nur in `client_surfaces/operator_tui/` implementiert. Die Funktionen `_rag_context_for_question` und `_chat_codecompass_context_for_question` aus der Dokumentation sind teilweise unter anderen Namen vorhanden (integriert in `ChatHistoryManagerMixin`). Die Logik ist produktiv aktiv, aber **nicht als zentraler Contract** für andere Backends (ananta-worker, OpenCode, Hermes) verfügbar.

---

## APMCO-Architektur (neue Schicht)

### Dateien

| Datei | Rolle |
|---|---|
| `agent/services/pre_model_context_config.py` | Config-Dataclasses, Mode-Konstanten, Heuristik-Klassifikation |
| `agent/services/pre_model_context_ranking.py` | `CandidateScorer` — 9-dimensionales Scoring + Ranking |
| `agent/services/pre_model_context_cache.py` | Disk-Cache mit TTL, manifest_hash-Invalidierung |
| `agent/services/pre_model_context_decision.py` | `DeterministicDecisionEngine` — No-LLM-Antworten |
| `agent/services/pre_model_context_orchestrator.py` | `PreModelContextOrchestrator` — Haupteinstiegspunkt |

### Konfigurationsbeispiel

```json
{
  "pre_model_context": {
    "enabled": false,
    "mode": "disabled",
    "surfaces": {
      "ai_snake_chat": {
        "enabled": true,
        "mode": "prefer_context",
        "reuse_existing_chat_context_flow": true
      },
      "ananta_worker": {
        "enabled": false,
        "mode": "worker_decides"
      }
    }
  }
}
```

### Modi

| Modus | Verhalten |
|---|---|
| `disabled` | Kein Orchestrator. Bestehender Flow unverändert. |
| `observe_only` | Kontext wird berechnet und getrackt, aber nicht injiziert. |
| `worker_decides` | Nur Tool-Catalog; kein automatischer Preflight. |
| `prefer_context` | Hub baut Kontext vorab; Fehler → Fallback auf bestehenden Flow. |
| `context_first` | Kontext vorab; LLM-Direktaufruf möglich wenn kein Kontext nötig. |
| `prefer_deterministic` | Erst deterministische Tools/CodeCompass; bei Unsicherheit → LLM. |
| `deterministic_only` | Kein LLM. Nur deterministische Antworten oder „nicht belegbar". |

### Entscheidungs-Outputs

| Decision | Bedeutung |
|---|---|
| `pass_through` | Caller nutzt originalen Flow (disabled / observe_only / Fehler) |
| `worker_decides` | Worker bekommt Tool-Catalog ohne Preflight-Kontext |
| `use_context` | `ContextPackage` ist bereit und soll dem Worker/LLM übergeben werden |
| `deterministic` | No-LLM-Antwort in `deterministic_answer` — kein Backend-Aufruf nötig |
| `cannot_answer` | `deterministic_only` + keine Evidence — klares „nicht belegbar" |

### Ranking-Scorekomponenten

| Komponente | Gewicht (Default) |
|---|---|
| `embedding_score` | 0.35 |
| `symbol_match_score` | 0.20 |
| `graph_distance_score` | 0.15 |
| `working_file_bonus` | 0.10 |
| `domain_scope_bonus` | 0.08 |
| `test_relation_bonus` | 0.05 |
| `recency_bonus` | 0.05 |
| `policy_penalty` | −0.20 |
| `sensitivity_penalty` | −0.15 |

Tie-breaking: `final_score` desc → `path` asc → `record_id` asc.

### Cache-Key-Komponenten

```
SHA256(repo_commit | manifest_hash | task_hash | working_files_hash | config_hash | mode | surface)
```

Invalidierung bei: neuem `manifest_hash` (KnowledgeIndexRun), geändertem `repo_commit`, relevanten Config-Änderungen.

### Backward-Kompatibilität

- `pre_model_context.enabled = false` (Default) → kein neuer Code läuft
- Bestehende `chat_use_codecompass`, `chat_rag_top_k`, `chat_context_chars` Settings sind unberührt
- ai-snake-chat ChatMixin/PromptBuilder-Flow läuft weiter solange `reuse_existing_chat_context_flow = true`
