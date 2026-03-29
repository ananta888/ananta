# Hybrid-RAG Runbook

## Ziel
Dieses Runbook beschreibt Betrieb, Rebuild und Recovery der Hybrid-RAG-Schicht (Aider-Map, agentische Suche, LlamaIndex) fuer grosse Datenmengen.

## Komponenten
- Code-Symbolindex: `RepositoryMapEngine` (in-memory, inkrementell via `mtime/size`)
- Agentische Suche: `AgenticSearchEngine` (Skill-Planer, sichere Allowlist)
- Semantischer Index: `SemanticSearchEngine` (persistiert in `.rag/llamaindex`)
- Artefakt-/Knowledge-Indizes: `rag-helper` ueber `RagHelperIndexService` und `KnowledgeIndexJobService`

## Konfiguration
Relevante Variablen:
- `RAG_ENABLED`
- `RAG_REPO_ROOT`
- `RAG_DATA_ROOTS`
- `RAG_MAX_CONTEXT_CHARS`
- `RAG_MAX_CONTEXT_TOKENS`
- `RAG_MAX_CHUNKS`
- `RAG_AGENTIC_MAX_COMMANDS`
- `RAG_AGENTIC_TIMEOUT_SECONDS`
- `RAG_SEMANTIC_PERSIST_DIR`
- `RAG_REDACT_SENSITIVE`

## Initiale Ingestion
1. `RAG_DATA_ROOTS` auf produktive Dokumentquellen setzen.
2. Einen ersten Request auf `/api/sgpt/context` ausfuehren.
3. Pruefen, dass `.rag/llamaindex/` und `manifest.json` erstellt wurden.

## Knowledge-Index-Profile
Erlaubte Hub-Profile:
- `default`: ausgewogen fuer allgemeine Artefakte
- `fast_docs`: schnell, dokumentzentriert, mit wenig Zusatzmaterial
- `deep_code`: reichhaltiger fuer Code-/Architektur-Artefakte

Profile koennen ueber den Hub abgefragt werden:
- `GET /knowledge/index-profiles`

Artefakt- oder Collection-Laeufe akzeptieren:
- `profile_name`
- optional `async=true` fuer groessere Laeufe

Beispiele:
- `POST /artifacts/<id>/rag-index` mit `{ "profile_name": "deep_code" }`
- `POST /knowledge/collections/<id>/index` mit `{ "profile_name": "fast_docs", "async": true }`

## Rebuild-Strategie
- Code-Symbolindex:
  - Rebuild erfolgt inkrementell bei Dateiaenderungen.
  - Voller Rebuild nur bei Prozessneustart oder erzwungener Neuerstellung.
- Semantischer Index:
  - Fingerprint im Manifest erkennt geaenderte/entfernte Dateien.
  - Bei Fingerprint-Aenderung wird der Index neu erstellt und persistiert.

## Recovery
Bei defektem oder inkonsistentem semantischen Index:
1. Dienst stoppen.
2. Verzeichnis `.rag/llamaindex` loeschen.
3. Dienst starten.
4. Einen Kontext-Request triggern, um Reingest zu erzwingen.

Bei defektem oder haengendem Knowledge-Index-Job:
1. Job-Status ueber `GET /knowledge/index-jobs/<job_id>` oder `GET /artifacts/<id>/rag-jobs/<job_id>` pruefen.
2. Falls `failed`, `error` und `run` im Job-Payload pruefen.
3. Artefakt-/Collection-Lauf mit passendem Profil erneut starten.
4. Bei grossen Code-Artefakten zuerst `fast_docs` oder `default` testen, bevor `deep_code` genutzt wird.

## Monitoring
Prometheus-Metriken:
- `rag_retrieval_duration_seconds`
- `rag_chunks_selected`
- `rag_requests_total{mode="context|execute"}`
- `knowledge_index_runs_total{scope,status,profile}`
- `knowledge_index_duration_seconds{scope,profile}`
- `knowledge_index_active_jobs`
- `knowledge_retrieval_chunks_selected`

Zu beobachten:
- Hohe Latenz in `rag_retrieval_duration_seconds`
- Unerwartet viele oder wenige Chunks
- Stark steigende `execute`-Requests ohne passende Antwortqualitaet

## Sicherheitschecks
- Agentische Shell-Suche nur ueber Allowlist (`rg`, `ls`, `cat`).
- Query-Sanitizing entfernt kritische Shell-Metazeichen.
- Kontext-Redaction ersetzt typische Secrets/PII durch `[REDACTED]`.
