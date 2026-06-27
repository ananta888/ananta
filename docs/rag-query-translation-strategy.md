# RAG Query Translation Strategy (DE↔EN)

> Implements: RTS-001, RCFG-004/005/006

## Übersicht

Ananta unterstützt bidirektionale Query-Expansion für RAG-Retrieval.
Die Originalquery wird **niemals** verworfen — Übersetzung erzeugt ausschließlich zusätzliche Suchvarianten.

## Funktionsweise

```
Userquery
    │
    ▼
normalize_query_from_settings(query)
    │
    ├─► [original query]            ← immer erste Variante
    ├─► [DE→EN expansion]           ← wenn de_to_en aktiv
    ├─► [mixed-code expansion]      ← wenn mixed_code_query aktiv
    └─► [EN→DE expansion]           ← wenn en_to_de aktiv (optional)
    │
    ▼
HybridOrchestrator: collect_context_chunks() für jede Variante
    │
    ▼
Deduplizierung (engine + source + content[:120])
    │
    ▼
Reranking (Originalquery behält Priorität)
```

## Übersetzungsrichtungen

| Richtung | Wann nützlich | Default |
|----------|---------------|---------|
| `de_to_en` | Deutsche Fragen → englische Code-Symbole/Dateinamen | ✓ |
| `mixed_code_query` | Gemischte Queries ("welche tasks in autopilot_tick_engine.py") | ✓ |
| `en_to_de` | Englische Fragen → deutsche Doku/Kommentare/Todos | optional |

## Konfiguration

```env
# .env / docker/old_way/docker-compose.yml
RAG_QUERY_NORMALIZE_MODE=keyword    # off | keyword | llm
RAG_QUERY_NORMALIZE_LANG=de,en      # Sprachhints
RAG_QUERY_TRANSLATION_DIRECTIONS=de_to_en,mixed_code_query
```

## Modi

### `off`
Keine Expansion. Exakt das bisherige Retrieval-Verhalten. Empfohlen wenn alle Queries englisch sind.

### `keyword` (Default)
Offline, lokal, kein LLM erforderlich. Nutzt eine Keyword-Mapping-Tabelle:
- Deutsche Verben → englische Code-Tokens (`funktioniert` → `works process function`)
- Deutsche Substantive → englische Retrieval-Tokens (`datei` → `file`)
- EN→DE für deutsche Doku-Suche (wenn aktiviert)

**Keine Netzwerkverbindung, keine Secrets, reproduzierbar.**

### `llm` (reserviert)
Fällt aktuell auf `keyword` zurück. Wenn zukünftig implementiert: sendet nur die Query (keine Artefakte, keinen Kontext) an ein lokales Modell mit Prompt `English keywords only, no sentences`.

## Regeln

- Originalquery ist immer die erste Retrievalvariante
- Übersetzung ist Expansion, kein Ersatz
- Code-Tokens (`snake_case`, `CamelCase`, `ENV_VAR`, Dateinamen) werden nicht übersetzt
- Duplicate Varianten werden per case-insensitive Vergleich entfernt
- Bei Fehler im Normalizer fällt Retrieval auf Originalquery zurück

## Grenzen

Keyword-/Glossary-Strategien sind bewusst grob. Sie erfinden keine fachliche Semantik und sind nicht kontextsensitiv. Für präzisere Übersetzung ist der `llm`-Modus vorgesehen.

## Overlapping Tasks: RCFG-005 vs. RTS-003

RCFG-005 (Basis-DE→EN) und RTS-003 (vollständige DE↔EN + mixed_code) implementieren denselben Code-Pfad in `agent/rag_query_normalizer.py`. RTS-003 ist die vollständigere Implementierung und ersetzt RCFG-005. RCFG-005 wird als "done via RTS-003" markiert. Siehe auch `todos/todo.master-implementation-roadmap.json` ROADMAP-003.
