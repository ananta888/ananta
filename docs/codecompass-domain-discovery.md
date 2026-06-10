# CodeCompass Domain Discovery

Track: `todos/todo.codecompass-domain-discovery.json` (CCDD)

CodeCompass/RAG-Helper erzeugt bereits Index-, Detail-, Relation-, Graph- und
Manifest-Ausgaben. Domain Discovery ist eine nachgelagerte, deterministische
Analyse-Stufe, die daraus **fachliche Domänen- bzw. Bounded-Context-Kandidaten**
ableitet und als eigenständige Artefakte schreibt:

| Artefakt | Inhalt |
| --- | --- |
| `domains.detected.json` | Domain-Kandidaten mit Evidenz, Metriken, Warnungen |
| `domain_boundaries.jsonl` | Eine Zeile pro Boundary-Warnung |
| `domain_coupling.json` | Kopplungsmatrix zwischen Domains |
| `domain_descriptor_suggestions/<id>/domain.json` | Optionale Descriptor-Vorschläge (Opt-in) |

Domain-Kandidaten sind **Vorschläge mit Begründung — keine automatische
Runtime-Freigabe**. Die Domain Foundation (siehe
`docs/architecture/domain_integration_foundation.md`) bleibt unberührt:
Descriptor Loading, Policy, Approval und Retrieval Routing sind Hub-owned,
und Runtime Readiness wird nie aus Descriptor-Präsenz abgeleitet.

## 1. Ist-Stand: Was CodeCompass heute liefert (CCDD-001)

### 1.1 `gem_partitions.py` erzeugt technische Kategorien, keine Bounded Contexts

`rag_helper/application/gem_partitions.py` kennt die Modi `domain` und
`domain-rich`. Die Funktion `_classify_domain()` klassifiziert Records jedoch
nach **technischen Layern**: `configuration`, `data-model`, `architecture`,
`docs`, `api`, `service`, `integration`. Beispiele:

- `kind == "jpa_entity_chunk"` → `data-model`
- `role_labels` enthält `controller` → `api`
- `role_labels` enthält `service` → `service`
- `kind in {"properties_entry", "yaml_entry"}` → `configuration`

Das sind Layer-Signale (welche *Art* von Baustein), keine fachlichen Domänen
(welcher *Geschäftsbereich*). Ein `UserService` und ein `BillingService`
landen beide in `service`, obwohl sie zu verschiedenen Bounded Contexts
gehören. Domain Discovery nutzt diese Klassifikation deshalb nur als
Zusatzsignal `technical_layers` pro Domain-Kandidat (CCDD-011) und deutet sie
nicht zu Domänen um.

### 1.2 `project_processor.py` ist die Quelle aller Analyse-Inputs

`process_project()` erzeugt in-memory `all_index`, `all_details`,
`all_relations` und daraus `graph_nodes`/`graph_edges` und schreibt abhängig
von den Modi: `index.jsonl`, `details.jsonl`, `relations.jsonl`,
`embedding.jsonl`, `context.jsonl`, `graph_nodes.jsonl`, `graph_edges.jsonl`
und `manifest.json`.

Für Domain Discovery relevante Manifest-Felder:

- `package_type_index`: Java-Package → sortierte Liste der Typnamen
  (aus `build_package_type_index()`; C#-Namespaces werden dort als
  `known_namespace_types` erfasst, landen aber aktuell nicht im Manifest —
  Namespace-Signale müssen daher aus den Records selbst gelesen werden).
- `record_counts_by_kind`, `extension_stats`: Größen-/Verteilungssignale.
- `partitioned_outputs`, `graph_node_count`, `graph_edge_count`: welche
  Outputs vorhanden sind.

### 1.3 Graph-Ausgabe: genutzte Felder

`output_formats.py::build_graph_nodes()` (Modus `jsonl`) liefert pro Knoten:

```json
{"id": "...", "kind": "java_type", "file": "src/main/java/...", "parent_id": "...",
 "role_labels": ["service"], "importance_score": 0.7, "generated_code": false}
```

`build_graph_edges()` liefert zwei Kantensorten, die Domain Discovery
**getrennt** behandelt:

1. **Struktur-Kanten** `parent_child` (`kind: "parent_child"`): Datei-/
   Typ-Hierarchie. Werden für Strukturzuordnung genutzt, zählen aber
   **nicht** als Kopplung.
2. **Relations-Kanten** (`kind: "relation"`): `type` aus den Extraktoren
   (`field_type_uses`, `injects_dependency`, `extends`, `implements`,
   `declares_bean`, `jpa_relation`, …) mit optionalen `confidence` und
   `heuristic`. Nur diese gehen in Kopplungs- und Boundary-Metriken ein.

Genutzte Felder pro Kante: `source`, `target`, `type`, `kind`, `confidence`,
`heuristic`.

In diesem Task (CCDD-001) wurde keine Runtime-Logik geändert.

## 2. Vertrag: `domains.detected.json` (CCDD-002)

Schema-Identifier: **`codecompass_domain_analysis.v1`**.

Top-Level-Felder:

| Feld | Typ | Bedeutung |
| --- | --- | --- |
| `schema` | string | immer `codecompass_domain_analysis.v1` |
| `project_root` | string | analysiertes Projekt |
| `generated_at` | string (ISO-8601) | Erzeugungszeitpunkt |
| `inputs` | object | welche Input-Dateien geladen wurden (Datei → Record-Zahl) |
| `domains` | array | Domain-Kandidaten, stabil sortiert nach `domain_id` |
| `unassigned_records` | array | Record-IDs ohne eindeutige Zuordnung |
| `warnings` | array of string | Analyse-Warnungen (fehlende Inputs etc.) |

Jeder Eintrag in `domains`:

| Feld | Typ | Bedeutung |
| --- | --- | --- |
| `domain_id` | string | stabiler Slug, aus Root-Pfad oder Descriptor übernommen |
| `display_name` | string | menschenlesbarer Name |
| `confidence` | number 0..1 | Vertrauenswert aus Signalstärke |
| `root_paths` | array | Pfad-Wurzeln der Domäne |
| `package_prefixes` | array | Java-Packages / C#-Namespaces der Mitglieder |
| `technical_layers` | array | Layer aus `gem_partitions`-Klassifikation |
| `core_records` | array | wichtigste Record-IDs (nach `importance_score`/Grad) |
| `record_count` | number | Anzahl zugeordneter Records |
| `metrics` | object | `internal_edge_count`, `inbound_edge_count`, `outbound_edge_count`, `edge_type_counts`, `external_domain_refs` |
| `boundary_warnings` | array | Warnungen, die diese Domäne betreffen |
| `evidence` | object | Provenance pro Signal: `path_signal`, `package_signal`, `graph_signal`, `descriptor_signal`, … |

Beispiel:

```json
{
  "schema": "codecompass_domain_analysis.v1",
  "project_root": "/repo",
  "generated_at": "2026-06-10T16:00:00Z",
  "inputs": {"index.jsonl": 412, "graph_nodes.jsonl": 380, "graph_edges.jsonl": 512, "manifest.json": 1},
  "domains": [
    {
      "domain_id": "rag-helper",
      "display_name": "Rag Helper",
      "confidence": 0.92,
      "root_paths": ["rag-helper/rag_helper"],
      "package_prefixes": ["rag_helper"],
      "technical_layers": ["architecture", "service", "data-model", "configuration"],
      "core_records": ["py_module:rag-helper/rag_helper/application/project_processor.py"],
      "record_count": 120,
      "metrics": {
        "internal_edge_count": 120,
        "inbound_edge_count": 7,
        "outbound_edge_count": 14,
        "edge_type_counts": {"field_type_uses": 40, "injects_dependency": 12},
        "external_domain_refs": {"agent-services": 9}
      },
      "boundary_warnings": [],
      "evidence": {
        "path_signal": {"root": "rag-helper/rag_helper", "file_count": 118},
        "package_signal": {"prefixes": ["rag_helper"], "type_count": 96},
        "descriptor_signal": null
      }
    }
  ],
  "unassigned_records": ["txt_file:notes/scratch.txt"],
  "warnings": ["details.jsonl not found in out_dir; detail-based signals skipped"]
}
```

`domain_boundaries.jsonl` enthält pro Zeile:

```json
{"source_domain": "billing", "target_domain": "identity", "warning_type": "mutual_coupling",
 "severity": "warning", "evidence": {"a_to_b_edges": 9, "b_to_a_edges": 7}}
```

`domain_coupling.json` enthält die gerichtete Kopplung als Liste:

```json
{"schema": "codecompass_domain_coupling.v1",
 "pairs": [{"source": "billing", "target": "identity", "edge_count": 9,
            "edge_type_counts": {"injects_dependency": 5, "calls_probable_target": 4}}]}
```

## 3. Signalmodell (CCDD-003)

Signale in absteigender Priorität. Deterministische Signale (1–6) entscheiden
über Cluster-Bildung; nachgelagerte Signale (7–8) liefern nur Anreicherung.

| # | Signal | Quelle | Gewicht | Rolle |
| --- | --- | --- | --- | --- |
| 1 | `descriptor_signal` | `domains/<id>/domain.json` `source_paths` | bindend für Benennung | Vorhandene Descriptoren benennen Cluster; Widerspruch → Warnung, kein Überschreiben |
| 2 | `path_signal` | Datei-Pfade der Records | stark | Primäres Cluster-Kriterium (Root-Pfad-Kandidaten) |
| 3 | `package_signal` / `namespace_signal` | `manifest.package_type_index`, Record-Felder | stark | Bestätigt/verfeinert Pfad-Cluster (Java/C#) |
| 4 | `graph_signal` | Relations-Kanten aus `graph_edges.jsonl` | mittel | Ordnet isolierte Records eindeutig gekoppelten Clustern zu; liefert Kopplungsmetriken |
| 5 | `role_label_signal` | `role_labels` der Nodes | schwach | Identifiziert Kern-Records (z.B. service/controller) innerhalb eines Clusters |
| 6 | `docs_signal` | adoc/md-Records unter Domain-Pfaden | schwach | Liefert display_name/Beschreibungs-Hinweise |
| 7 | `technical_layer_signal` | `gem_partitions._classify_domain` | nur Anreicherung | Füllt `technical_layers`; **nie** Cluster-Kriterium |
| 8 | `embedding_signal` | embeddings (optional, später) | nur Erklärung | Darf Namen/Zusammenfassungen verbessern, nie Grenzen behaupten |

**Warum deterministische Signale Vorrang haben:** Pfade, Packages, Graph-Kanten
und Descriptoren sind nachprüfbar und byte-stabil reproduzierbar. Embeddings
können semantisch ähnliche, aber organisatorisch getrennte Module falsch
zusammenclustern und liefern keine zitierfähige Evidenz.

**Schutzregel gegen Layer-Verwechslung:** Ein Cluster, dessen einzige
Gemeinsamkeit ein technischer Layer ist (alle `service`, alle `api`), wird
nicht als Domäne ausgegeben. `technical_layers` ist ein beschreibendes Feld
pro Domäne; eine Domäne enthält typischerweise *mehrere* Layer.

**Akzeptanzbeispiele** (unterschiedlich zu behandelnde Kandidaten):

- `rag-helper/rag_helper` → eigene Domäne: eigener Pfad-Root, eigenes
  Python-Package `rag_helper`, dichte interne Kopplung.
- `agent/services` → Sub-Root-Kandidat: `agent/` ist zu groß und heterogen
  (services, routes, cli, bootstrap je eigene dichte Bereiche); der
  Root-Finder steigt eine Ebene ab, wenn der Eltern-Pfad kaum eigene Dateien
  hat, aber mehrere große Kinder.
- `client_surfaces` → bleibt zunächst ein Kandidat bzw. zerfällt nur dann in
  `client_surfaces/<surface>`, wenn die Kinder jeweils die Mindestgröße
  erreichen — sonst ein Sammel-Kandidat mit Warnung `heterogeneous_root`.
- `domains/<id>` → Descriptor-Signal hat Vorrang: vorhandene
  `domain.json`-Verträge benennen den Kandidaten.

### Root-Pfad-Findung (deterministisch)

1. Baue einen Pfad-Präfixbaum mit Dateizahlen aus allen Record-Dateipfaden.
2. Beginne beim Repo-Root. Für jeden Kind-Knoten mit `file_count >= min_files`
   (Default 3):
   - Hat das Kind genau ein dominantes Unterverzeichnis (≥ 80 % der Dateien),
     steige dorthin ab (`rag-helper` → `rag-helper/rag_helper`).
   - Hat das Kind kaum eigene Dateien (< `min_files`), aber ≥ 2 Kinder mit
     `file_count >= min_files`, und ist die Tiefe < `max_root_depth`
     (Default 2), werden die Kinder zu Root-Kandidaten (`agent` →
     `agent/services`, `agent/routes`, …).
   - Sonst ist das Kind selbst Root-Kandidat.
3. Gleicher Input ⇒ byte-stabil sortierte, identische Kandidatenliste.

### Confidence-Modell

`confidence = 0.5·path + 0.25·package + 0.15·graph_cohesion + 0.10·descriptor`

- `path`: Anteil der Cluster-Records unter dem Root-Pfad (i.d.R. 1.0).
- `package`: 1.0 wenn dominantes Package-Präfix existiert, sonst anteilig.
- `graph_cohesion`: interne Kanten / (interne + externe Kanten); 0.5 wenn
  keine Kanten vorhanden.
- `descriptor`: 1.0 wenn ein Descriptor den Cluster bestätigt, 0.5 wenn
  keiner existiert, 0.0 bei Widerspruch (zusätzlich Warnung).

Records mit mehrdeutiger Zuordnung bleiben in `unassigned_records` oder
erhalten low confidence — es wird nicht geraten (CCDD-DD-004).

## 4. Nutzung

```bash
# Analyse als Teil eines RAG-Helper-Laufs (basic: Pfad/Package/Graph-Signale)
cd rag-helper
python codecompass_rag.py --root /pfad/zum/projekt --out-dir out \
  --graph-export-mode jsonl --domain-discovery-mode basic

# rich: zusätzlich Descriptor-Abgleich, technical_layers und core_records
python codecompass_rag.py --root /pfad/zum/projekt --out-dir out \
  --graph-export-mode jsonl --domain-discovery-mode rich
```

Bei aktivem Modus entstehen `domains.detected.json`,
`domain_boundaries.jsonl` und `domain_coupling.json` neben den bestehenden
Outputs; `manifest.json` erhält einen `domain_discovery`-Block mit
Summary-Zahlen. Bei `--domain-discovery-mode off` (Default) ändert sich
nichts an bestehenden Ausgaben.

### Boundary-Warnungen auswerten

`warning_type`-Werte:

- `mutual_coupling`: zwei Domains haben in beide Richtungen ≥ Schwellwert
  Relations-Kanten — Hinweis auf fehlende oder falsch gezogene Grenze.
- `layer_spans_domains`: ein technischer Layer verbindet ≥ 3 Domains quer —
  Hinweis auf eine geteilte technische Plattform-Schicht, die ggf. als
  eigene (generische) Komponente gehört.
- `heterogeneous_root`: ein Root-Kandidat bündelt viele kleine, untereinander
  unverbundene Bereiche.
- `descriptor_mismatch`: ein vorhandener Domain-Descriptor nennt Pfade, die
  nicht zur erkannten Code-Struktur passen.

**Vom Befund zum Descriptor (menschlicher Schritt):** Ein Mensch prüft die
Evidenz (`root_paths`, `core_records`, Kopplungsmetriken), entscheidet über
den Schnitt und übernimmt dann — bewusst — einen Vorschlag aus
`domain_descriptor_suggestions/<id>/domain.json` nach `domains/<id>/`.
Vorschläge nutzen `lifecycle_status: foundation_only` und
`runtime_status: descriptor_only` und behaupten keine Runtime-Fähigkeit.

## 5. Tests

Die Analysebibliothek liegt in `rag-helper/rag_helper/domain_discovery/` und
importiert keine Hub-/Agent-Runtime-Services. Tests liegen — wie alle
RAG-Helper-Tests — unter `rag-helper/tests/` und laufen aus dem
`rag-helper/`-Verzeichnis (das Root-`tests/`-Verzeichnis kann `rag_helper`
nicht importieren):

```bash
cd rag-helper
python -m pytest tests/ -k codecompass_domain -q
```
