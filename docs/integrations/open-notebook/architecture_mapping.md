# OpenNotebook → Ananta Architecture Mapping

Status: aktiv, v1 (2026-07-05)
Todo: `todos/todo.open-notebook-adapter-importer-frontend-backend-integration.json`

Ananta integriert [lfnovo/open-notebook](https://github.com/lfnovo/open-notebook) als
externe, NotebookLM-ähnliche Research-Quelle. OpenNotebook wird **nicht** als
Fremdarchitektur in den Ananta-Kern kopiert, sondern über einen Adapter/Importer
angebunden. Referenzdateien im OpenNotebook-Repo:

- `README.md`
- `docs/7-DEVELOPMENT/architecture.md`
- `open_notebook/domain/notebook.py` (Notebook, Source, Note, SourceInsight)
- `api/sources_service.py` (Source-Lifecycle, Async Processing)
- `open_notebook/graphs/source.py` (Source-Ingestion-Graph, Transformationen)
- `open_notebook/graphs/source_chat.py` (Source-focused Chat)

## Grundsatzentscheidungen

1. **Keine Übernahme der OpenNotebook-DB als Ananta-Primärmodell.**
   OpenNotebook persistiert in SurrealDB mit eigenem Objektmodell. Ananta akzeptiert
   ausschließlich einen versionierten Export-Contract
   (`schemas/integrations/open_notebook_export.v1.json`) und mappt ihn auf
   bestehende Ananta-Modelle. Damit bleibt Ananta unabhängig von OpenNotebook-
   Releases und SurrealDB-Interna.
2. **RagService/RetrievalService bleiben die zentralen Seams.**
   OpenNotebook bringt einen eigenen LangGraph-Chat mit; Ananta nutzt ihn nicht.
   Retrieval läuft über `RetrievalService` (source_type `open_notebook`),
   Kontextaufbau über `RagService.retrieve_context_bundle` und
   `SourceChatContextService`. So gelten Budgetierung, Explainability,
   Redaction und Provenance-Policies unverändert für OpenNotebook-Inhalte.
3. **Bestehende Reliable-Sources-Bausteine werden wiederverwendet:**
   `SourceRegistry` + `schemas/sources/source_descriptor.v1.json` (Descriptor),
   `SourceSnapshotStore` + `schemas/sources/source_snapshot.v1.json` (Snapshots,
   OpenNotebook-Felder im `extensions`-Feld), `citation_formatter` +
   `schemas/sources/source_reference.v1.json` (Zitate), `SourcePackService`
   (Packs), `sources_bp` (API), `commands_sources.py` (TUI).

## Objekt-Mapping

| OpenNotebook | Ananta | Regel |
| --- | --- | --- |
| Notebook | `KnowledgeCollectionDB` / SourcePack / optional Goal-Kontext | Notebook ist ein menschlicher Research-Container und wird als Sammlung importierter Quellen modelliert. |
| Source | `ArtifactDB` + `ArtifactVersionDB` + SourceSnapshot + SourceReference + SourceRegistry-Descriptor | Titel, asset, url, file_path, full_text, topics und Timestamps bleiben erhalten; Rohinhalt wird gehasht und versioniert. |
| SourceEmbedding | KnowledgeIndex-Chunk / CodeCompassBundle-Chunk | Embeddings werden nicht übernommen; Ananta re-indexiert, damit Dimension, Modell und Provenance kontrolliert bleiben. |
| Note | `ArtifactDB` / ResultMemory / TaskMemory | Notes sind menschliche/AI-Notizen (`record_kind='note'`), standardmäßig mit niedrigerer Retrieval-Priorität als Sources. |
| SourceInsight | TransformationArtifact / `record_kind='source_insight'` | Insights behalten `parent_source_ref` und werden als *derived* gekennzeichnet, nie als Primärquelle. |
| ChatSession | Chat-History / Operator-TUI-Session / optional ResultMemory | Chatverläufe werden **nicht** automatisch indexiert; nur nach expliziter Freigabe, mit niedrigem trust_level. |
| Transformation | TransformationTask-Template | Prompts dienen als Vorlage für Ananta-eigene TransformationTasks (optionale Ausbaustufe E06). |

## Ananta-Anschlusspunkte (Seams)

| Baustein | Datei | Rolle für OpenNotebook |
| --- | --- | --- |
| SourceRegistry | `agent/sources/source_registry.py` | Importer registriert pro Import einen Descriptor (`source_type='open_notebook'`); dadurch erscheinen importierte Quellen in `GET /sources`, TUI und UI. |
| SourceSnapshotStore | `agent/sources/source_snapshot_store.py` | Immutable Snapshots, Dedup über `content_hash`; OpenNotebook-Metadaten in `extensions`. |
| IngestionService | `agent/services/ingestion_service.py` | `upload_artifact` persistiert Rohinhalte als Artifact + Version + KnowledgeLink. |
| KnowledgeIndexRetrievalService | `agent/services/knowledge_index_retrieval_service.py` | Importer schreibt `index.jsonl`-Records mit `source_scope='open_notebook'`; der Retrieval-Adapter filtert auf diesen Scope. |
| Adapter-Registry | `agent/services/retrieval_service.py` `_build_source_adapters` | `OpenNotebookKnowledgeSourceAdapter` unter key `open_notebook`. |
| Contract | `agent/services/retrieval_source_contract.py` | `SOURCE_TYPES` + `enabled_source_types_from_settings` erweitert; Flag `rag_source_open_notebook_enabled` (default **False**). |
| Fusion | `agent/services/retrieval_query_builder.py` | Bonus bei retrieval_intent research/notebook/notes; Malus bei task_kind code_change/api_contract. |
| Citation | `agent/sources/citation_formatter.py` | `format_citation` erzeugt citation_label; Referenzen validieren gegen `source_reference.v1.json`. |

## Risiken

| Risiko | Beschreibung | Mitigation |
| --- | --- | --- |
| Lizenz | OpenNotebook-Quellen können fremde Inhalte mit unklarer Lizenz enthalten. | `license_status='unknown'` statt erfundener Lizenz; Anzeige im Citation Panel (T09.03). |
| Datenvolumen | Exporte können große full_texts enthalten. | Budgetierter Kontext (max_chunks, Token-Budget), keine Rohtexte in Worker-Konfiguration. |
| Embedding-Modell-Mismatch | OpenNotebook-Embeddings passen nicht zu Ananta-Modellen. | Embeddings werden nie übernommen; Re-Indexing durch Ananta. |
| Citation-Qualität | Exporte ohne canonical_url/file_path sind schwer zitierbar. | SourceReference mit Fallback auf source_system + import_key; `citation_label` immer erzeugbar. |
| Rohtext-Import | Ungeprüfte Rohtexte könnten Secrets oder private Daten enthalten. | `OpenNotebookImportPolicy` (T09.01) + Redaction (T09.02) laufen vor Persistenz. |
| Chat-Halluzinationen | Indexierte Chatverläufe würden Halluzinationen als Wissen zementieren. | ChatSessions werden per Policy-Default nicht importiert/indexiert. |
