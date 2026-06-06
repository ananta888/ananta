# CodeCompass Retrieval Profile & Source Policy

## Ziel

Dieses Dokument beschreibt den vollständigen AI-Snake-Retrieval-Flow: vom Nutzer-Prompt bis zum grounded Prompt an den Worker. Es dokumentiert Ist-Zustand, bekannte Fehlfälle und den neuen RetrievalProfile-basierten Pfad.

---

## CRPS-001: Ist-Zustand (vor dem Refactor)

### Call-Chain

```
_build_grounded_snake_prompt(user_text)          # agent/routes/snakes.py
  → get_rag_service().build_execution_context()  # agent/services/rag_service.py
    → retrieve_context_bundle()
      → retrieve_context()                        # agent/services/retrieval_service.py
        → _source_selection_policy()
        → _task_profile_for_fusion()
        → ArtifactKnowledgeSourceAdapter / RepoRetrievalSourceAdapter / ...
        → _rerank_candidates()
    → ContextBundler.build_bundle()               # agent/services/context_bundle_service.py
    → ContextBundler.build_grounded_prompt()
  → generate_text(grounded_prompt)
```

### Bekannte Engpässe

| # | Datei / Funktion | Problem |
|---|---|---|
| 1 | `snakes.py::_build_grounded_snake_prompt` | `chat_use_codecompass=true` → nur `source_types=['artifact']`, nie `['repo', 'artifact']` |
| 2 | `snakes.py::_build_grounded_snake_prompt` | `retrieval_intent='chat_codecompass_overview'` hartcodiert — unabhängig vom User-Intent |
| 3 | `context_bundle_service.py::build_grounded_prompt` | `del chunks` — alle Chunk-Metadaten (source_type, score, line_range) werden verworfen |
| 4 | `context_bundle_service.py::build_bundle` | Grobe Sortierung: nur `architecture` → wiki_first, sonst repo_first |
| 5 | `snakes.py` (context_summary) | Zählt nach `chunk['source']`-Pfad, nicht nach `source_type` |

### Bekannter Fehlfall (Regression Case)

**Frage:** "den codecompass der schon implementiert ist erklären"

**Beobachtetes Verhalten (alt):**
- Kontext enthält: `docs/ananta-game/book-of-ananta.md`, `docs/snake_tutor.md`, `terminal-header-logo-renderer.md`, `markdown_mermaid_document_view.py`
- Grund: `source_types=['artifact']` → nur Knowledge-Index, kein Repo-Code

**Erwartetes Verhalten (neu):**
- Kontext enthält: `agent/services/rag_service.py`, `agent/services/retrieval_service.py`, `agent/services/context_bundle_service.py`, `agent/routes/snakes.py`
- `intent=implemented_code_explanation` → Profil `codecompass/implemented_code_explanation` → `source_types=['repo','artifact']`

---

## Neue Architektur: RetrievalProfile-Flow

### Neuer Call-Chain

```
_build_grounded_snake_prompt(user_text)
  → resolve_profile(query, cfg)                  # agent/services/retrieval_profile_service.py
    → classify_retrieval_intent(query, cfg)       # deterministisch, kein LLM
    → _PROFILE_TABLE lookup
    → _apply_ui_source_constraints()
  → get_rag_service().build_execution_context(retrieval_profile=profile)
    → source_types = profile.source_types
    → retrieval_intent = profile.retrieval_intent
    → retrieve_context(source_types, ..., retrieval_profile=profile)
      → source_type_weights aus Profil übernehmen
      → apply_profile_source_constraints() nach Dedup
    → build_bundle(..., retrieval_profile=profile)
      → context_policy enthält profile_id, domain, intent
    → build_grounded_prompt(..., chunks=..., retrieval_profile=profile)
      → chunks werden NICHT mehr verworfen
      → strukturierter Prompt mit source_type/score/Dateiname
```

---

## RetrievalProfile Schema

```python
@dataclass
class RetrievalProfile:
    profile_id: str                     # z.B. "codecompass/implemented_code_explanation"
    domain: str                         # codecompass | ai_snake | worker | ananta_game | operator_tui | ops | generic
    intent: str                         # implemented_code_explanation | architecture_overview | ...
    source_types: list[str]             # ["repo", "artifact"] — nach UI-Constraints bereinigt
    source_type_weights: dict[str, float]  # {"repo": 1.45, "artifact": 1.05, "wiki": 0.3}
    retrieval_intent: str               # z.B. "code_explanation_with_codecompass"
    negative_source_patterns: list[str] # z.B. ["book-of-ananta", "snake_tutor"]
    feature_flag: str                   # "auto" | "legacy" | "repo_first" | "docs_first"
    warnings: list[str]                 # z.B. ["source_type_disabled_by_ui_config:wiki"]
```

---

## Domain / Intent Tabelle

| domain | intent | source_types | retrieval_intent |
|---|---|---|---|
| `codecompass` | `implemented_code_explanation` | repo, artifact | code_explanation_with_codecompass |
| `codecompass` | `architecture_overview` | repo, artifact, wiki | architecture_codecompass_overview |
| `codecompass` | `mermaid_request` | artifact, repo | mermaid_diagram_request |
| `worker` | `implemented_code_explanation` | repo, artifact | worker_code_explanation |
| `worker` | `architecture_overview` | repo, artifact | worker_architecture_overview |
| `ai_snake` | `implemented_code_explanation` | repo, artifact | snake_code_explanation |
| `ai_snake` | `generic_chat` | artifact, repo | chat_codecompass_overview |
| `ananta_game` | `tutorial_help` | artifact, wiki | game_tutorial_docs |
| `ananta_game` | `game_design` | artifact, wiki | game_design_docs |
| `ops` | `ops_runbook` | artifact, repo | ops_runbook |
| `operator_tui` | `implemented_code_explanation` | repo, artifact | tui_code_explanation |

---

## Konfliktregeln (Priorität)

```
globale rag_source_*_enabled Settings
  > UI-Scope (chat_use_codecompass / chat_include_local_project / chat_include_wikipedia)
    > Profil-Gewichte (source_type_weights)
      > Query-Heuristik (classify_retrieval_intent)
```

**Sicherheitsgrenze:** `SourceSelectionPolicy` (in `retrieval_source_contract.py`) bleibt unangetastet. Profile können Quellen priorisieren oder anfordern, aber nie global deaktivierte Quellen aktivieren.

---

## Negative Source Patterns

Bei `implemented_code_explanation`-Profilen werden folgende Quellen gefiltert/penalisiert:

- `book-of-ananta`, `book_of_ananta` — Game-Lore-Doku
- `snake_tutor` — Tutorial-Doku
- `terminal-header-logo-renderer` — TUI-Renderer-Doku
- `markdown_mermaid` — Mermaid-Renderer (irrelevant für Code-Erklärungen)

Wenn nach dem Filter keine positiven Quellen übrig bleiben, enthält das Bundle `strategy.fusion.profile_constraints.insufficient_positive_sources: true`.

---

## Feature Flag: `chat_retrieval_profile`

| Wert | Verhalten |
|---|---|
| `"auto"` | Vollständiger Profile-Resolver (Standard) |
| `"repo_first"` | Gewichte auf repo=1.4 gepatch, repo als ersten source_type erzwungen |
| `"docs_first"` | Gewichte auf artifact=1.3, wiki=1.2 gepatch |
| `"legacy"` | Generischer Fallback, altes UI-Flag-Verhalten |
| `"disabled"` | Generischer Fallback, keine Domain-Logik |

In `user.json` konfigurierbar als `chat_retrieval_profile`.

---

## Prompt-Rendering (nach CRPS-011)

`build_grounded_prompt` rendert strukturierten Kontext wenn Chunks vorhanden:

```
Frage:
{user_query}

Kontext ({profile_id} | domain={domain} | intent={intent}):
[1] {source_type} | {source} | score={score} | {line_range}
{content_excerpt}

[2] ...

Regel: Antworte nur auf Basis der aufgeführten Quellen. Nenne konkrete Dateien/Funktionen.
```

Ohne Chunks: Fallback auf `context_text`-Format wie bisher.

---

## Konfiguration

Neue Felder in `user.json` / `ai_snake_config.py`:

| Key | Default | Beschreibung |
|---|---|---|
| `chat_retrieval_profile` | `"auto"` | Feature Flag für den Resolver |
| `chat_retrieval_domain_hint` | `""` | Expliziter Domain-Override (z.B. `"ananta_game"`) |
| `chat_code_questions_repo_first` | `false` | Shortcut: setzt `chat_retrieval_profile="repo_first"` |

---

## Relevante Dateien

| Datei | Rolle |
|---|---|
| `agent/services/retrieval_profile_service.py` | RetrievalProfile, classify_retrieval_intent, resolve_profile, apply_profile_source_constraints |
| `agent/routes/snakes.py` | _build_grounded_snake_prompt — ruft resolve_profile auf |
| `agent/services/rag_service.py` | build_execution_context — akzeptiert retrieval_profile |
| `agent/services/retrieval_service.py` | retrieve_context — übernimmt source_type_weights aus Profil |
| `agent/services/context_bundle_service.py` | build_bundle / build_grounded_prompt — profile-aware |
| `agent/routes/ai_snake_config.py` | chat_retrieval_profile, chat_retrieval_domain_hint |
| `agent/services/retrieval_source_contract.py` | SourceSelectionPolicy — Sicherheitsgrenze |
| `tests/test_retrieval_profile_service.py` | Unit-Tests für Resolver + Classifier |
| `tests/test_snake_ask_retrieval_profile.py` | Regressionstests AI-Snake |
| `tests/test_retrieval_service_profiles.py` | Ranking-Tests mit Profil-Gewichten |

---

## Warnhinweis: `user.json` im Repo

`user.json` ist eine lokale Runtime-Config und sollte NICHT ins Git-Repo eingecheckt werden (enthält personalisierte Chat-Einstellungen). Sie ist in `.gitignore` eingetragen — prüfen bevor Commits.

---

Details zum CodeCompass-Snippet-Handoff an den Worker: [docs/codecompass-relevant-snippet-handoff.md](codecompass-relevant-snippet-handoff.md)
