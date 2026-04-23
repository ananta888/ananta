# Hybrid-RAG Runbook

## Ziel
Dieses Runbook beschreibt Betrieb, Rebuild und Recovery der Hybrid-RAG-Schicht (Aider-Map, agentische Suche, LlamaIndex) fuer grosse Datenmengen.

## Komponenten
- Code-Symbolindex: `RepositoryMapEngine` (in-memory, inkrementell via `mtime/size`)
- Agentische Suche: `AgenticSearchEngine` (Skill-Planer, sichere Allowlist)
- Semantischer Index: `SemanticSearchEngine` (persistiert in `.rag/llamaindex`)
- Artefakt-/Knowledge-Indizes: `rag-helper` ueber `RagHelperIndexService` und `KnowledgeIndexJobService`

## Hub-owned Orchestrierungsvertrag
- Hub-Endpunkte triggern Import/Index/Query-Flows (`/artifacts/*`, `/knowledge/*`, `/api/sgpt/*`).
- Worker fuehren delegierte Index-/Retrieval-Arbeit aus, orchestrieren aber keine weiteren Worker.
- Laufen asynchron ueber Job-Statuspfade, damit Zustand und Fehlerbilder auditable bleiben.
- Source-Policies sind zentral im Hub konfiguriert und werden fail-closed ausgewertet.
- Vertrag maschinenlesbar:
  - `GET /artifacts/orchestration-contract`
  - `GET /knowledge/orchestration-contract`

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
- `RAG_SOURCE_REPO_ENABLED`
- `RAG_SOURCE_ARTIFACT_ENABLED`
- `RAG_SOURCE_TASK_MEMORY_ENABLED`
- `RAG_SOURCE_WIKI_ENABLED`

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

## Source-aware Retrieval Policy
- Optionales Query-Feld `source_types` ist additiv verfuegbar auf:
  - `POST /api/sgpt/context`
  - `POST /api/sgpt/execute` (bei `use_hybrid_context=true`)
  - `POST /knowledge/collections/<id>/search`
- Erlaubte Source-Typen:
  - `repo`
  - `artifact`
  - `task_memory`
  - `wiki`
- Ungueltige Source-Typen oder komplett deaktivierte Source-Matrix werden fail-closed behandelt.

## Source Preflight Diagnostics
Hub-Endpunkte fuer Source-Readiness:

- `GET /artifacts/retrieval-preflight`
- `GET /knowledge/retrieval-preflight`

Diagnostik trennt:
- Source-spezifische Issues (`repo`, `artifact`, `wiki`, `task_memory`)
- globale Source-Policy (`enabled/requested/effective`)
- Gesamtstatus (`ok|degraded|error`)

## Source-agnostische Index-Pipeline
- Der Hub kann strukturierte Source-Records direkt indexieren:
  - `POST /knowledge/sources/index-records`
  - optional asynchron mit `{ "async": true }`
- Pipeline bleibt deterministisch (`json_sort_keys`) und erzeugt reproduzierbare `manifest.json` + `index.jsonl`.
- Unterstuetzte `source_scope` fuer diesen Pfad: `artifact`, `wiki`.

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

## Laptop-basierte Limits (empfohlene Startwerte)
- `RAG_MAX_CHUNKS=12` (nicht sofort erhoehen, zuerst Qualität messen)
- `RAG_MAX_CONTEXT_TOKENS=3000`
- `RAG_MAX_CONTEXT_CHARS=12000`
- Knowledge-Index-Profil zuerst `fast_docs`, danach `default`, erst zuletzt `deep_code`
- Fuer erste Rollouts pro Collection nur wenige Artefakte indexieren (z. B. 10-30 Dateien), dann schrittweise erweitern
- Reproduzierbarer Referenz-Corpus fuer kleine Offline-Wiki-Laeufe:
  - `data_test/wiki_mvp_corpus/articles.jsonl` (klein, lokal, ohne Internet)
  - empfohlen: zuerst nur diesen Corpus indexieren und Query-Mix pruefen (`repo + wiki`)
  - typische Groessenordnung fuer den Start: wenige MB Index-Output

## Offline Mixed-Retrieval Demo (repo + wiki)
1. Wiki-Records lokal indexieren:
   - `POST /knowledge/sources/index-records`
   - Payload-Beispiel:
     ```json
     {
       "source_scope": "wiki",
       "source_id": "wiki-mvp",
       "records": [
         {
           "kind": "wiki_section",
           "file": "wiki/payment.md",
           "article_title": "Payment retries",
           "section_title": "Timeout handling",
           "language": "en",
           "content": "Workers retry payment after timeout."
         }
       ]
     }
     ```
2. Gemischte Query fahren:
   - `POST /api/sgpt/context` mit `"source_types": ["repo", "wiki"]`
3. In der Antwort pruefen:
   - `metadata.source_type` unterscheidet `repo` vs `wiki`
   - `metadata.citation.article_title/section_title` ist fuers Wiki gesetzt
   - `selection_trace.fusion.source_type_contributions_*` zeigt den Mix explizit

## Golden Path: Offline wiki-backed help
1. Wiki-Quelle aktivieren:
   - `RAG_SOURCE_WIKI_ENABLED=true`
2. Reproduzierbaren MVP-Corpus importieren:
   - `POST /knowledge/wiki/import`
   - `{"corpus_path":"data_test/wiki_mvp_corpus/articles.jsonl","source_id":"wiki-mvp"}`
3. Hilfe-Query ueber den Hauptpfad:
   - `POST /api/sgpt/context` mit `{"query":"Wie behandeln wir Timeout-Retries?","source_types":["wiki","repo"]}`
4. Ergebnis pruefen:
   - `chunks[*].metadata.citation` enthaelt fuer Wiki `article_title`, `section_title`, `revision`
   - `chunks[*].metadata.source_type` bleibt explizit (`wiki|repo`)

Wofuer die Wiki-Quelle gedacht ist:
- stabile, kuratierte Offline-Wissensseiten mit klarer Provenance.

Wofuer sie nicht gedacht ist:
- unkontrollierte Web-Suche oder unkuratiertes Echtzeit-Internet-Retrieval.

## Container- und Runtime-Annahmen
- Hub und Worker laufen in getrennten Containern; Orchestrierung bleibt im Hub.
- Persistente Pfade muessen container-uebergreifend stabil gemountet sein:
  - Repo-Semantikindex: `.rag/llamaindex`
  - Knowledge-Indizes: `<DATA_DIR>/knowledge_indices/<source_scope>/<knowledge_index_id>/<run_id>`
- Retrieval darf nicht von implizitem In-Memory-Shared-State zwischen Hub/Worker abhaengen.
- Reindex und Recovery werden ueber Hub-APIs/Jobruns gesteuert, nicht ueber direkte Worker-zu-Worker-Pfade.
