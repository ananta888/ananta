# Hybrid-RAG Developer Guide

## Architekturvertrag
`HybridOrchestrator.get_relevant_context(query)` liefert:
- `strategy`: Engine-Quoten
- `policy_version`: Routing-Policy
- `chunks`: gerankte Evidence-Objekte
- `context_text`: prompt-fertiger Kontext
- `token_estimate`: Budget-Schaetzung

Neue Engines muessen kompatible `ContextChunk`-Daten liefern.

## Neue Sprache fuer Engine A
1. Datei-Erweiterung in `RepositoryMapEngine.CODE_EXTENSIONS` aufnehmen.
2. Mapping in `TREE_SITTER_LANGUAGE_BY_EXT` ergaenzen.
3. Falls keine Tree-Sitter-Unterstuetzung: Regex-Fallback verbessern.
4. Retrieval-Test mit einer Beispiel-Datei anlegen.
5. Support-Matrix ueber `RepositoryMapEngine.language_support_matrix()` pruefen und dokumentieren.

## Neue Skills fuer Engine B
1. Neuen `SearchSkill` mit `priority`, `trigger`, `build_command` definieren.
2. Command muss durch Allowlist laufen.
3. Budget beachten:
   - `max_commands`
   - `command_timeout_seconds`
   - `max_output_chars`
4. Bei Bedarf dedizierten Testfall ergaenzen.

## Neue Datenquellen fuer Engine C
1. Dateiendungen in `TEXT_EXTENSIONS` ergaenzen.
2. Eignung fuer Chunking/Indexing pruefen (Dateigroesse, Parsing-Qualitaet).
3. Manifest-Rebuild-Verhalten testen.

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
  - Redaction-Pattern
- Integration:
  - `/api/sgpt/context`
  - `/api/sgpt/execute` mit `use_hybrid_context=true`
