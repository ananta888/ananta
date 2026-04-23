# Hybrid-RAG Developer Guide

## Multi-source target model (CodeCompass)
Der Retrieval-Kern ist ein **typed multi-source core** mit klarer Trennung:

- **Shared core**: Fusion, Deduping, Reranking, Diversity, Budgeting, Redaction, Explainability.
- **Source adapters**: Repo/Code, Artifact/Knowledge, Task-Memory und spaeter Wiki.

Ziel ist additive Erweiterung ohne Bruch fuer bestehende Repo/Code-Caller.

## Architekturvertrag
`HybridOrchestrator.get_relevant_context(query)` liefert:
- `strategy`: Engine-/Policy-Signale
- `policy_version`: Routing-Policy
- `chunks`: gerankte Evidence-Objekte
- `context_text`: prompt-fertiger Kontext
- `token_estimate`: Budget-Schaetzung

Der Shared Core in `RetrievalService` vereinheitlicht danach alle Quellen.

## Source adapter contract
Gemeinsame Adapter-Schnittstelle in `agent/services/retrieval_source_contract.py`:

- `RetrievalSourceAdapter` (narrow contract fuer Search-Provider)
- `SourceSelectionPolicy` (enabled/requested/effective Source-Typen)
- Source-Typen: `repo`, `artifact`, `task_memory`, `wiki`

Explizite Adapter-Implementierungen liegen in
`agent/services/retrieval_source_adapters.py`:
- `RepoRetrievalSourceAdapter`
- `ArtifactKnowledgeSourceAdapter`
- `WikiKnowledgeSourceAdapter`
- `TaskMemorySourceAdapter`

Fail-closed Verhalten:
- Ungueltige `source_types` werden abgewiesen.
- Wenn nach Policy kein Source-Typ aktiv ist, wird kein Retrieval gestartet.

## Normalisiertes Chunk-Metadatenmodell
Jeder Retrieval-Chunk bekommt im Shared Core additive, source-uebergreifende Felder:

- `source_type`
- `source_id`
- `chunk_id`
- `citation`
- `provenance`

Damit bleiben Herkunft, Zitierbarkeit und spaetere Read-Model-Ausgaben stabil ueber alle Quellen.

## Source-aware Query API (additiv)
Retrieval-Endpunkte akzeptieren optional `source_types`:

- `POST /api/sgpt/context`
- `POST /api/sgpt/execute` (bei `use_hybrid_context=true`)
- `POST /knowledge/collections/<id>/search`

Felder sind optional und brechen Legacy-Clients nicht.

## Hybrid ranking across source types
Die Fusion bewertet nicht nur Engines, sondern auch Source-Typen:

- `engine_weights`
- `source_type_weights`
- Source-Typ-Diversitaet via `max_per_source_type`

Selection traces enthalten:
- `source_type_contributions_before`
- `source_type_contributions_after_dedupe`
- `source_type_contributions_final`

Damit bleibt Ranking-Policy evolvierbar, ohne Source-Adapter umzubauen (OCP).

## Context policy for source budgets
Die Context-Policy begrenzt Retrieval nun auf mehreren Ebenen:

- pro konkreter Source (`max_per_source`)
- pro Source-Typ (`max_per_source_type`)
- pro Engine (`max_per_engine`)

So verhindert der Core, dass einzelne Source-Klassen den Kontext ueberfluten.

## Preflight diagnostics
Source-Readiness kann hub-seitig abgefragt werden:

- `GET /artifacts/retrieval-preflight`
- `GET /knowledge/retrieval-preflight`

Die Ausgabe trennt source-spezifische Probleme von globalen Orchestrierungs-/Policy-Signalen.

## Hub-Orchestrierungsvertrag (maschinell)
Hub-seitige Vertragsendpunkte:
- `GET /artifacts/orchestration-contract`
- `GET /knowledge/orchestration-contract`

Der Vertrag dokumentiert:
- hub-owned State-Machine (`queued -> running -> completed|failed`)
- worker-ausgefuehrte Schritte ohne Worker-zu-Worker-Orchestrierung
- explizite Retry-Koordination durch den Hub

## Shared indexing pipeline fuer typed sources
- `RagHelperIndexService.index_artifact(...)` bleibt artifact-kompatibel.
- `RagHelperIndexService.index_source_records(...)` bietet denselben kontrollierten Pipeline-Rahmen fuer strukturierte Source-Records (z. B. `wiki`).
- Persistenzlayout ist source-scope getrennt:
  - `<DATA_DIR>/knowledge_indices/<source_scope>/<knowledge_index_id>/<run_id>`

## Wiki MVP adapter + importer design
- Wiki bleibt ein dedizierter Adapter (`WikiKnowledgeSourceAdapter`) auf dem gemeinsamen Source-Contract.
- Offline-Import erfolgt ueber JSONL (`/knowledge/wiki/import`) und normalisiert auf:
  - `article_title`
  - `section_title`
  - `language`
  - `revision/import_revision`
  - `import_metadata`
- Wiki-Chunking bleibt deterministisch:
  - sentence-basierte Chunks mit stabilen `chunk_id`-Hashes
  - deterministische Sortierung vor Persistenz
  - gleiche Eingabe + gleiche Konfiguration => gleiche Chunk-IDs/Index-Outputs

## Future source roadmap (nach MVP, ohne Big-Bang)
Die aktuelle Source-Abstraktion ist bewusst nicht wiki-spezifisch und kann additive Adapter aufnehmen:
- FreeCAD/STEP-Dokumente (CAD-Struktur + Baugruppenbeziehungen)
- KiCad-Projekte (Schaltplan-/Netzlisten-Kontext)
- Blender-Assets (Szenen-/Objekt-/Material-Kontext)
- Team-Handbuecher/Runbooks (strukturierte Betriebsdokumentation)

Regel: neue Quellen nur als Adapter + Normalisierungsschicht, nie als Sonderfall direkt im Shared Core.

## Neue Sprache fuer Repo-Adapter
1. Datei-Erweiterung in `RepositoryMapEngine.CODE_EXTENSIONS` aufnehmen.
2. Mapping in `TREE_SITTER_LANGUAGE_BY_EXT` ergaenzen.
3. Falls keine Tree-Sitter-Unterstuetzung: Regex-Fallback verbessern.
4. Retrieval-Test mit einer Beispiel-Datei anlegen.
5. Support-Matrix ueber `RepositoryMapEngine.language_support_matrix()` pruefen und dokumentieren.

## Neue Skills fuer Agentic-Adapter
1. Neuen `SearchSkill` mit `priority`, `trigger`, `build_command` definieren.
2. Command muss durch Allowlist laufen.
3. Budget beachten:
   - `max_commands`
   - `command_timeout_seconds`
   - `max_output_chars`
4. Bei Bedarf dedizierten Testfall ergaenzen.

## Decision-Policy erweitern
1. `ContextManager.route` anpassen.
2. `policy_version` erhoehen (z. B. `v2`).
3. Regressionstests fuer neue Routing-Regeln hinzufuegen.

## Sicherheitsregeln
- Keine Shell-Ausfuehrung ausserhalb der Allowlist.
- Kein `shell=True` fuer agentische Commands.
- Immer Redaction vor Prompt-Zusammenbau aktiv lassen, ausser in isolierten Testfaellen.

## Testempfehlungen
- Unit:
  - Routing-Quoten
  - Reranking + Diversity
  - Source-Policy und Metadatennormalisierung
- Integration:
  - `/api/sgpt/context` mit und ohne `source_types`
  - `/api/sgpt/execute` mit `use_hybrid_context=true`
