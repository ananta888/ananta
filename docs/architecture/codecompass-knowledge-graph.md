# CodeCompass Live Knowledge Graph — Roadmap (COSMOS-008)

## Ziel

CodeCompass entwickelt sich von einem dateibasierten Retrieval-System zu einem fortlaufend
aktualisierten Knowledge Graph. Dieser Graph erfasst Beziehungen zwischen Dateien, Symbolen,
Services und Domänen und ermöglicht strukturierte Abfragen statt reiner Vektorsuche.

---

## Knoten-Typen

Jeder Knoten hat das folgende Basis-Schema:

```python
@dataclass
class GraphNode:
    node_id: str          # stabil, z. B. "repo:ananta/file:src/hub.py"
    node_type: NodeType
    path: str | None      # Dateipfad (für file, module, function, class, ...)
    name: str             # lesbarer Name
    metadata: dict        # typ-spezifische Felder
    freshness: datetime   # letzter Scan-Timestamp
    confidence: float     # 0.0–1.0
```

| node_type        | Beschreibung                                  | Wichtige metadata-Felder             |
|------------------|-----------------------------------------------|--------------------------------------|
| repository       | Wurzel eines Repos                            | url, default_branch                  |
| module           | Python-Package / JS-Modul                     | namespace, language                  |
| file             | Einzelne Quelldatei                           | language, size_bytes, last_modified  |
| class            | Klasse / Interface                            | bases, is_abstract                   |
| function         | Freistehende Funktion                         | signature, is_async                  |
| method           | Methode innerhalb einer Klasse                | class_node_id, visibility            |
| api_endpoint     | HTTP-Route oder RPC-Methode                   | method, path_pattern, auth_required  |
| database_table   | Datenbanktabelle oder -entity                 | schema, columns[]                    |
| event_topic      | Message-Queue-Topic oder Event-Bus-Kanal      | broker, payload_schema_ref           |
| config_key       | Konfigurationsschlüssel (ENV, YAML, .toml)    | default_value, required              |
| test_case        | Einzelner Test oder Test-Suite                | framework, covers_node_ids[]         |
| domain_concept   | Fachlicher Begriff aus der Domain Map         | description, aliases[]               |
| policy_rule      | Explizite Policy-Regel (Default-Deny, Gate)   | scope, enforcement_level             |

---

## Kanten-Typen

Jede Kante hat das folgende Basis-Schema:

```python
@dataclass
class GraphEdge:
    edge_id: str
    source_id: str
    target_id: str
    edge_type: EdgeType
    evidence: list[EvidenceSource]  # Herkunft: AST | heuristisch | manuell
    confidence: float               # 0.0–1.0
    freshness: datetime             # letzter Scan-Timestamp
```

| edge_type            | Semantik                                                  |
|----------------------|-----------------------------------------------------------|
| imports              | Modul/Datei importiert ein anderes                        |
| calls                | Funktion ruft eine andere auf                             |
| implements           | Klasse implementiert Interface / abstrakte Klasse         |
| reads                | Funktion/Klasse liest Datenbanktabelle oder Config-Key    |
| writes               | Funktion/Klasse schreibt Datenbanktabelle                 |
| tests                | test_case deckt function / class / api_endpoint ab        |
| configures           | config_key steuert function / class / api_endpoint        |
| publishes            | Code-Pfad publiziert auf event_topic                      |
| subscribes           | Code-Pfad konsumiert von event_topic                      |
| owns_domain          | Modul / Klasse ist verantwortlich für domain_concept      |
| requires_permission  | api_endpoint / function erfordert policy_rule             |
| deprecated_by        | Knoten wurde durch anderen Knoten ersetzt                 |

---

## Inkrementelles Update

Der Graph wird **nicht** bei jedem Request komplett neu gescannt.

```
Dateiänderung erkannt (Watcher / Git-Hook)
  → betroffene Nodes invalidieren (anhand Pfad-Index)
  → nur für betroffene Dateien: AST-Analyse neu ausführen
  → abgeleitete Kanten (calls, imports) für diese Dateien neu berechnen
  → Freshness-Timestamp der geänderten Nodes + Kanten aktualisieren
  → nicht betroffene Knoten und Kanten bleiben unverändert
```

Voll-Rebuild nur bei: erstem Scan, Schema-Migration, manuellem Trigger.

---

## Migration bestehender Daten

Bestehende Domain Map und Funktionsgraph-Einträge werden **nicht verworfen**.

Migrationspfad:
1. Bestehende Einträge erhalten eine stabile `node_id` (Format: `legacy:<type>:<original_key>`).
2. Kanten werden aus vorhandenen Analysen abgeleitet (z. B. domain_map → `owns_domain`-Kanten).
3. Confidence bestehender Kanten startet bei `0.6` (heuristisch), bis ein AST-Nachweis vorliegt.
4. Ein Migrations-Script schreibt Ergebnisse in die Graph-Datenbank; Original-Daten bleiben erhalten.

---

## Evidence pro Kante

```python
@dataclass
class EvidenceSource:
    source_type: Literal["ast_analysis", "heuristic", "manual"]
    source_file: str | None
    line: int | None
    description: str
    confidence: float  # 0.9 für AST, 0.6 für heuristisch, 1.0 für manuell
```

Regeln:
- AST-Nachweis überschreibt Heuristik nicht — beide werden gespeichert.
- Manuelle Evidence hat Priorität bei Konflikten, ist aber auditierbar (Autor, Datum, Grund).
- Fehlender Nachweis → Kante existiert nicht; keine Kante mit `confidence=0.0`.

---

## Schema-Versionierung

```json
{
  "graph_schema_version": "1.0.0",
  "created_at": "2026-07-01T00:00:00Z",
  "migration_history": [
    { "from": null, "to": "1.0.0", "script": "migrations/graph_init_v1.py" }
  ]
}
```

Migrationsskripte liegen unter `codecompass/graph/migrations/`. Jede Schema-Version ist
abwärtskompatibel lesbar bis zur explizit dokumentierten Mindestversion.

---

## Graph-Abfragen im Tooling

CodeCompass exponiert Graph-Abfragen als strukturierte Tool-Calls:

- `graph_neighbors(node_id, edge_types, depth)` — direkte Nachbarn eines Knotens
- `graph_path(source_id, target_id, max_depth)` — Pfad zwischen zwei Knoten
- `graph_subgraph(root_ids, edge_types)` — Teilgraph für Kontext-Curation
- `graph_search(query, node_types)` — Volltext + Typ-Filter

---

## Tests

| Test                          | Beschreibung                                                       |
|-------------------------------|--------------------------------------------------------------------|
| `test_graph_build_fixture`    | Kleines Fixture-Projekt vollständig indizieren, Node/Edge prüfen   |
| `test_incremental_update`     | Dateiänderung → nur betroffene Nodes/Edges aktualisiert            |
| `test_migration_legacy_data`  | Bestehende Domain-Map-Daten korrekt als graph_nodes importiert     |
| `test_query_neighbors`        | `graph_neighbors` gibt korrekte Nachbarn zurück                    |
| `test_evidence_confidence`    | AST-Kante hat confidence >= 0.9, Heuristik-Kante < 0.9            |
| `test_schema_version_header`  | Graph-Header enthält schema_version und migration_history          |
