# OpenNotebook Research Source Pack

Pack-Datei: `sources/source-packs/open-notebook.source-pack.json`
Descriptor: `sources/open_notebook/source_descriptor.json`
RAG-Profil: `domains/open_notebook/rag_sources/open_notebook_source.profile.json`

Das Pack bündelt die OpenNotebook-Integration als kontrollierte Research-Quelle:

- **Trust-Level:** `user_managed_research` — unterhalb `official_vendor_project`,
  oberhalb bzw. gleichauf mit `user_supplied_private` (Policy-abhängig).
- **Priorität:** Lokale Projektquellen vor OpenNotebook; OpenNotebook vor dem
  allgemeinen Wiki-Fallback, sofern `retrieval_intent` research/notebook/notes ist
  (siehe `extensions.retrieval_priority_policy`).
- **Provenance-Policy:** Importierte Inhalte sind default `local_only`
  (`llm_scope`), ChatSessions werden nicht indexiert, unbekannte Lizenzen werden
  als `unknown` ausgewiesen statt erfunden.

## Bootstrap (offline)

1. Export importieren: `POST /sources/import/open-notebook` mit
   `tests/fixtures/open_notebook/complex_export.json` (JSON-Body oder multipart).
2. Alternativ Operator-TUI: `:sources import-open-notebook tests/fixtures/open_notebook/complex_export.json`.
3. Snapshots prüfen: `GET /sources/{registry_source_id}/snapshots`.
4. Retrieval testen mit `source_types=['open_notebook']` — Voraussetzung:
   `RAG_SOURCE_OPEN_NOTEBOOK_ENABLED=true` (default ist disabled).

Alle Tests laufen offline mit den Fixtures unter `tests/fixtures/open_notebook/`;
eine laufende OpenNotebook-Instanz ist nie erforderlich.
