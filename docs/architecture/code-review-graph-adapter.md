# code-review-graph Adapter (CRG-001)

## Position

`code-review-graph` (CRG) ist ein **optionaler, lokaler Symbolgraph-Adapter** für
Ananta CodeCompass. Ananta kopiert CRG nicht; es integriert über einen
neutralen Adapter-Vertrag und nutzt nur die stabilen Ideen:

- Tree-sitter AST als Quelle für Funktionen/Klassen/Imports/Calls/Inheritance
- lokal-first SQLite-Speicherung unter `.code-review-graph/`
- Blast-Radius-Analyse
- Incremental Updates
- Risk-Scoring (versionierte Formel)
- Hub-/Bridge-/Knowledge-Gap-/Surprising-Connection-Konzepte

Native Ananta-Graphfähigkeiten (Blast Radius, Graph Metrics, Graph Diff)
funktionieren auch ohne CRG. CRG ist ein **ImportProvider**, kein
Voraussetzung.

## Upstream-Pin

- Repository: <https://github.com/tirth8205/code-review-graph>
- Version: 2.3.6
- Commit: `b72413cbd34a4ac08cc60dcdd42df1d02f3fc77d`
- Lizenz: MIT
- Status: aktive Entwicklung, keine Garantie für stabile SQLite-Schemata

Floating-`main` wird **nie** konsumiert. Alle Tests verwenden versionierte
Fixtures (siehe `tests/fixtures/codecompass/crg/`).

## Datenmodell-Mapping

CRG liefert (Tree-sitter AST):

| CRG-Konzept            | Ananta-CodeCompass |
|------------------------|--------------------|
| Datei                  | `node.kind=file`   |
| Funktion / Methode     | `node.kind=symbol_function` |
| Klasse                 | `node.kind=symbol_class` |
| Import                 | `edge.kind=imports` |
| Funktionsaufruf        | `edge.kind=calls` |
| Vererbung              | `edge.kind=inherits` |
| Test-Funktion          | `node.kind=symbol_function` + `node.attrs.test_kind=unit|integration|e2e` |
| Test-Coverage-Beziehung| `edge.kind=covers` |

## Felder mit Herkunfts-Markierung

CRG-spezifische Felder werden ausschließlich in `node.attrs`/`edge.attrs`
unter dem Präfix `crg_*` gespeichert. Drei Klassen:

- **normalisiert** (in CodeCompassGraphStore übernommen):
  `crg_confidence_kind`, `crg_risk_score_breakdown`
- **evidence/provenance**: vollständige CRG-Records in
  `edge.attrs.crg_source_record` mit `source='code-review-graph'` und
  `provider_revision` — kein Leak in generische REST-Schemas
- **verworfen**: rohe Tree-sitter-Subtrees, interne CRG-Caches

## Feature-Flags

| Flag                              | Default | Wirkung                                                |
|-----------------------------------|---------|--------------------------------------------------------|
| `codecompass.crg.adapter_enabled` | `false` | aktiviert den CRG-Adapter beim Bootstrap               |
| `codecompass.crg.strict_pinning`  | `true`  | lehnt SQLite-Exporte ab, deren Schema-Revision nicht   |
|                                   |         | zur gepinnten reviewed_revision passt                  |

Siehe `agent/feature_flags/codecompass_crg.py` (DD-015).

## Lizenz- und Vendor-Isolation

CRG wird als ImportQuelle, nicht als Vendor-Library behandelt:

- MIT-Lizenz bleibt in `docs/licenses/CODE_REVIEW_GRAPH_MIT.txt`
- kein direkter Import in Production-Code ohne Adapter-Schicht
- alle Reads sind read-only, pfadbegrenzt (`workspace_dir`)
- keine Shell-Aufrufe aus CRG-Inputs (DD-013)

## Was CRG-001 *nicht* macht

- keine Build/Target-Information (kommt von RIG/SPADE via RIG-003)
- keine Package-/Runner-Wahrheit (RIG-Schicht)
- keine semantische Suche (RAG-Schicht)
- keine Policy-Entscheidungen (Policy-Layer via COMBO-002)