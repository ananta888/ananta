# CodeCompass

Ananta CodeCompass is the combined context layer that feeds the
worker tool loop. It is **not** a copy of any external tool — it
integrates over a small set of contracts and lets the Hub/Worker
plumbing stay versioned and reproducible.

## Composition

| Layer | Source | Contract |
|-------|--------|----------|
| Symbolgraph (functions, classes, imports, calls, inheritance, tests) | optional CRG v2.3.6 (commit `b72413cbd34a4ac08cc60dcdd42df1d02f3fc77d`) | `codecompass.graph_artifact.v1` |
| Repository Intelligence (build targets, runners, tests, packages, coverage) | CMake File API via SPADE ref-impl `6306e203732f7c4553d1564c5250396b7f84a315` (pinned) + manual fixtures | `codecompass.repository-intelligence.v1` |
| Semantic / RAG (snippets, docs, embeddings) | existing CodeCompass retrieval | `codecompass.semantic_translation_graph.v1` |
| Policy / Trust / Evidence | in-house | `codecompass.graph-evidence.v1` |

## Revisionsfeste Indexierung und Editor-Assistent

Der produktive Indexlauf ist ein Hub-eigener Task. Der Hub authentifiziert
Principal und Tenant, friert Workspace-Revision und Snapshot-Policy ein und
bleibt Eigentümer von Idempotenz, Status, Retry und atomarer Veröffentlichung.
Ein Worker enumeriert den materialisierten Snapshot, extrahiert Records und
liefert ausschließlich gehashte Artefakt- und Coverage-Referenzen zurück. Hub
und Worker setzen dabei kein gemeinsam beschreibbares Dateisystem voraus.

Die relevanten versionierten Verträge sind:

| Vertrag | Zweck |
| --- | --- |
| `schemas/worker/codecompass_snapshot_manifest.v1.json` | vollständige Klassifikation jedes Snapshot-Pfads als indexed, excluded, unsupported oder failed |
| `schemas/worker/knowledge_index_job.v1.json` | begrenzter Hub-zu-Worker-Auftrag |
| `schemas/worker/knowledge_index_job_result.v1.json` | content-freies Worker-Ergebnis und Publish-Hashes |
| `schemas/source/source_catalog.v2.json` | revisions- und tenantgebundener autoritativer Source-Katalog |
| `schemas/source/source_ref.v2.json` | unveränderte, vom Hub bereitgestellte Source-Identität |
| `schemas/codecompass.agentic-retrieval.v1.json` | gemeinsamer Agent/MCP/n8n-Retrieval-Vertrag; siehe [Hybrid Retrieval](architecture/agent-codecompass-hybrid-retrieval.md) |
| `schemas/codecompass.hierarchical-architecture-context.v1.json` | budgetierter Architektur-Slice System→Symbol; siehe [Hierarchical Context](architecture/codecompass-hierarchical-architecture-context.md) |
| `schemas/worker/codecompass_duckdb_snapshot_manifest.v1.json` | optionaler DuckDB-Snapshot-Pointer; siehe [DuckDB Backend](architecture/duckdb-codecompass-backend.md) |

Große, nicht unterstützte oder budgetüberschreitende Dateien verschwinden
nicht still: Manifest und Coverage-Gate führen sie mit stabilem Reason-Code.
Die produktive Suche komponiert injizierbare FTS-, Vector-, Symbol- und
Graph-Provider. Ein unverdrahteter oder leerer Produktionskanal ist
`degraded`/`no_results`, niemals implizit `current`.

Für den Visual Process Editor erweitert
`CodeCompassContextPlannerService` den bestehenden Retrieval-Pfad um die
typisierten Intents `node_explanation`, `field_effect`, `io_contract`,
`validation_issue`, `runtime_error`, `dependency` und `safe_change`.
Strukturelle Registry-, Node-, Feld-, Contract-, Symbol- und
Nachbarschaftsdaten bilden die Query; Nutzersprache bleibt ein begrenztes
Zusatzsignal. `preview` liest kein Repository und ruft kein Modell auf.
`selected` und `conversation` erzwingen getrennte Range-, Zeilen-, Evidence-
und Tokenbudgets und protokollieren jedes verworfene Element content-frei.

## Source-Autorität und Evidence-Release

Record-ID, Pfad, Hash, Task-ID und Listenposition sind keine Source-Identität.
Nur eine bereits im autoritativen Hub-Katalog vorhandene und für Tenant,
Scope, Revision, Manifest und Allowlist freigegebene Kennung darf unverändert
als verifizierte Evidence weitergegeben werden. Fehlt diese Autorität, bleibt
der Treffer `unverified` oder `failed`; CodeCompass erzeugt keinen Ersatzwert.

Vor einem Assistant-Prompt durchläuft jeder Retrievalblock Access-Policy,
Redaction und Injection-Scan. Secrets, Credential-Inhalte, fremde Tenants,
stale Revisionen und High-Risk-Injection werden nicht als Inhalt freigegeben.
Explizit deklarierte Widersprüche zwischen Dokumentation und aktuellem
Code/Schema werden mit beiden zugelassenen Quellen als `evidence_conflict`
angezeigt; keine Seite wird heuristisch bevorzugt.

Der reproduzierbare technische Lauf liegt in
`artifacts/test-gates/codecompass-e2e.json`. Ohne extern bereitgestellte
autoritative Source-Evidence darf er die Ingestion-, Such- und negativen
Security-Stufen nachweisen, setzt aber `release_allowed=false` und gibt keine
grounded Claims frei. Das ist ein absichtlicher Release-Blocker.

## Position relative to CRG and RIG/SPADE

| Aspect | Ananta CodeCompass | CRG v2.3.6 | SPADE ref-impl |
|--------|--------------------|-----------|---------------|
| Native AST graph | yes (RIG-001 + CRG-001 docs) | yes | no |
| Build/Target/Runner truth | yes (RIG-001 + RIG-003) | no | yes |
| Build-system coverage | CMake (M2/M3); npm/maven/gradle/cargo/go via RIG-012 manual fixtures (M7); full automation in M8+ | n/a | CMake only |
| Evidence model | trust_level + verification_status + source_id provenance (COMBO-002) | heuristic confidence_kind | reply ids |
| Failure model | fail-closed at import edge (DD-013/CCRIG-DD-007) | exception | exception |

## What Ananta does NOT do

* does **not** vendor CRG or SPADE
* does **not** call upstream floating-main revisions
* does **not** use a free-form graph query language (CCRIG-DD-004)
* does **not** treat missing evidence at partial/unknown coverage as
  negative evidence (CCRIG-DD-008)
* does **not** synthesise source / run IDs (AGENTS.md)

## Architecture Documents

* `architecture/code-review-graph-adapter.md` — CRG-001
* `architecture/repository-intelligence-graph.md` — RIG-001
* `architecture/codecompass-import-trust.md` — DD-017 + DD-013 + DD-016
* `architecture/codecompass-file-type-support.md` — canonical file-type registry, capability levels, rollout and audit
* `codecompass-tools.md` — public tool registry
* `codecompass-n8n-workflows.md` — n8n workflow extraction (records, relations, redaction; opt-in via `--extensions json`)
* `ci/ananta-codecompass-review.md` — CRG-011 CI workflow

## Backlog

See `todos/todo.codecompass-crg-rig-spade-integration.json`.

## Status

The M1–M5 milestones are complete; M6/M7 close out the integration
with the worker tool loop, e2e fixtures, metrics, and a final security
audit. See the backlog for individual task status.
