# Repository Intelligence Graph (RIG) — Domänenmodell

## Position

Der **Repository Intelligence Graph (RIG)** ist eine eigene, von Symbolgraph
und RAG getrennte Schicht. RIG liefert deterministische, evidence-basierte
Wahrheit über Build-Targets, Test-Runner, externe Packages und Coverage.
Seine Beweisqualität ist eine andere als AST/Symbolgraph (heuristisch) oder
RAG (semantisch). Daher strikte Trennung (CCRIG-DD-002).

## Entities

| Entity              | Beschreibung                                      |
|---------------------|---------------------------------------------------|
| `package_manager`   | Cargo, npm, Maven, Gradle, CMake, Go modules, … |
| `external_package`  | Eine konkrete Version einer externen Abhängigkeit |
| `buildable_component` | Build-Target (CMake-Target, crate, npm-paket, …) |
| `aggregator`        | Container mehrerer Komponenten (z.B. Maven-Modul) |
| `runner`            | Konkreter Test-Runner (CTest, cargo test, jest, …) |
| `test`              | Eine einzelne Test-Definition                     |

## Edges

| Edge                 | Bedeutung                                                 |
|----------------------|-----------------------------------------------------------|
| `depends_on`         | `buildable_component` -> `external_package`               |
| `aggregates`         | `aggregator` -> `buildable_component`                     |
| `built_by`           | `buildable_component` -> `buildable_component` (intern)   |
| `tested_by`          | `buildable_component` -> `runner`                         |
| `runs`               | `runner` -> `test`                                        |
| `covers`             | `test` -> `buildable_component` (Coverage)                |

Jede Edge trägt:

- `evidence`: `source_file`, `source_kind`, `source_record_id`
  oder `reason='manual_fixture'`
- `confidence`: 0.0..1.0
- `provenance`: `extractor_id`, `extractor_version`, `build_system`

## Snapshot-Metadaten

Jeder RIG-Snapshot trägt:

- `snapshot_id` (deterministisch aus content_hash)
- `extractor_id` + `extractor_version`
- `build_system` (cmake|maven|npm|cargo|gradle|go|manual)
- `coverage_status`: `complete | partial | unknown`
- `unsupported_features`: Liste bekannter Lücken
- `generated_at`

## Coverage-Wahrheit (CCRIG-DD-008)

RIG ist **nur innerhalb `coverage_status=complete` und der ausgewiesenen
Buildsystem-Abdeckung autoritativ**. Abwesenheit von Knoten bei
`partial` oder `unknown` ist **keine negative Evidence** — Queries geben
`unknown_coverage` zurück.

## Mapping zum CodeCompassGraphStore

| RIG-Entity        | CodeCompass-Slot                              |
|-------------------|-----------------------------------------------|
| RIG nodes         | `rig_nodes[]` + SQLite-Tabelle `rig_nodes`    |
| RIG edges         | `rig_edges[]` + SQLite-Tabelle `rig_edges`    |
| Coverage-Status   | `diagnostics.repository_intelligence.coverage_status` |
| Provenance        | `node.provenance.extractor_id` etc.           |

Bestehende Symbolgraph-Edges (`nodes`, `edges`) bleiben unangetastet
(CCRIG-DD-006).

## Extractor-Familie

Geplant (siehe DD-012):

- **M2/M3**: CMake via SPADE File API + CTest (`codecompass_rig_cmake_adapter`)
- **bis M7**: manuelle JSON-Fixtures via `scripts/import_repository_intelligence_graph.py`
  für npm, maven, gradle, cargo, go (Escape-Valve)
- **M8+**: vollautomatische Extractoren sobald ein Upstream-Tool stabile Releases liefert

Jeder Extractor implementiert denselben `CodeCompassGraphImportProvider`-Port
(CRG-002).

## Was RIG-001 *nicht* macht

- keine Symbol-/AST-Analyse (Symbolgraph via CRG-Adapter)
- keine semantische Suche (RAG)
- keine Security-/Policy-Entscheidungen (Policy-Layer via COMBO-002)