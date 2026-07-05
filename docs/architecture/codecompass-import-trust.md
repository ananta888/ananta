# CodeCompass Import-Trust (DD-017)

## Pipeline-Übersicht

```
+-----------------+      +-----------------+      +------------------+
| externe Quelle  | ---> | trusted Worker  | ---> | versioniertes    |
| (CRG JSON/SQL,  |      | im Hub-Container|      | Artefakt         |
|  SPADE CMake,   |      | mit Capability- |      | (GraphStore)     |
|  manuelle       |      | Checks          |      |                  |
|  Fixtures)      |      |                 |      |                  |
+-----------------+      +-----------------+      +------------------+
        |                                                    |
        | Trust-/Evidence-                                   |
        | Invariante am                                      |
        v Import-Rand                                        v
+-----------------+                                +------------------+
| fail-closed     |                                | Hub Policy/      |
| Schema-Check    |                                | Registry:        |
| (RIG-012, COMBO-|                                | - tool-gates     |
|  002)           |                                | - truth-         |
|                 |                                |   precedence     |
+-----------------+                                +------------------+
```

## Import-Rand-Invarianten (COMBO-002)

| Invariante          | Quelle              | Konsequenz bei Verletzung                |
|---------------------|---------------------|------------------------------------------|
| Trust-Level         | `schemas/codecompass.graph-evidence.schema.json` | Import verweigert, `unverified` |
| Source-/Run-IDs     | extern bereitgestellt | fehlende IDs → `unverified/failed`, nie synthetisiert |
| Pfad-Begrenzung     | DD-013              | `path_outside_workspace`                  |
| Größen-Limit        | DD-013 + Worker-Konfig | `payload_too_large`                      |
| Secret-Redaction    | bestehende `_evidence.py` | Excerpts werden vor Persistenz redacted |
| Schema-Version-Pin  | CRG/SPADE-Revision  | `external_graph_incompatible`            |

## Trust-Level

| Level          | Bedeutung                                          | Policy-erlaubt |
|----------------|----------------------------------------------------|----------------|
| `deterministic`| aus Build-Artefakt-Reply mit content_hash          | ja             |
| `extracted`    | aus Tool-Output, versioniert                       | ja             |
| `manual`       | versionierte JSON-Fixture von Mensch               | ja             |
| `inferred`     | heuristisch (z.B. Tree-sitter `maybe_call`)        | nein (nur warnen) |
| `ambiguous`    | nicht eindeutig zuordenbar                          | nein           |

`verification_status`: `verified | unverified | failed`.

## Worker-Lokation (DD-013)

Extraktion läuft in einem **trusted Worker-Prozess im selben Docker-Container
wie der Hub**. Capability-Checks:

- read-only auf `workspace_dir` (kein Schreibzugriff)
- keine Shell-Aufrufe aus Userinput/Fixture-Pfaden
- kein Netzwerk (Upstream-Files kommen über Hub-Artifact-Registry)

Container-Grenzen aus AGENTS.md bleiben erhalten; CLI umgehen den Hub-Task
nicht (COMBO-004).

## Tool-Loop-Konsumenten

| Tool                       | Vertrauen auf Graph-Data              |
|----------------------------|---------------------------------------|
| `codecompass.resolve_context` | buckets: `symbol_neighbors`, `build_test_evidence`, `semantic_chunks`, `policy_evidence` (COMBO-001) |
| `codecompass.blast_radius` | Symbolgraph + CRG-importierte Edges (CRG-005) |
| `codecompass.repository_query` | RIG-RIG-RIG (RIG-005) — vollständig deterministisch erforderlich |
| `codecompass.knowledge_gaps` | Symbolgraph + RIG (CRG-008)          |
| `codecompass.graph_metrics` | Symbolgraph (CRG-007)                 |

## Truth-Precedence (RIG-009)

1. **RIG complete**: autoritativ für Build/Target/Test-Runner
2. **Symbolgraph**: autoritativ für Symbol-Struktur
3. **RAG/Semantic**: heuristisch, markiert als `weak_support` wenn RIG vorhanden
4. **RIG partial/unknown**: fehlende Evidence → `unknown_coverage`, nicht Widerlegung