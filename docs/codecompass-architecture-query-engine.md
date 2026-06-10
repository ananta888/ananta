# CodeCompass Architecture Query Engine

Status: implementiert (CCAQE-Track, 2026-06-10)

Die Architecture Query Engine beantwortet typisierte Architekturfragen auf Basis des
CodeCompass-Graphen. Anders als die Graph-Expansion (Kontextnavigation für die UI)
liefert sie pro Treffer **Evidence-Pfade** mit Knoten, Kanten, Richtung, Tiefe und
Confidence — und kennzeichnet heuristische Hinweise explizit als solche.

## Zweck und Grenzen

- **Zweck:** Fragen wie "Welche Services hängen indirekt an diesem DTO?",
  "Welche Tests decken diesen Controller ab?", "Welche Policy-Regeln betreffen
  dieses Feld?" und "Welche Abhängigkeitskette hat dieser Service?" deterministisch
  und nachvollziehbar beantworten.
- **Grenzen:** Die Engine arbeitet auf dem indexierten Graphen (`cc_graph_index.json`),
  nicht auf dem Compiler-Modell. Kanten wie `calls_probable_target` oder
  `test_calls_endpoint` sind heuristisch und werden mit Warnung ausgegeben.
  Leere Ergebnisse bedeuten "nicht belegt", nicht "existiert nicht".
- **Sicherheit:** Query-Typen sind whitelisted (keine freie Graph-Query-Sprache),
  alle Limits sind hart begrenzt (CCAQE-008), gelesen wird ausschließlich der
  vorhandene Knowledge-Index-Output.

## Query-Typen

| Query-Typ | Frage | Default-Richtung | Primärkanten |
|---|---|---|---|
| `dto-impact` | Wer hängt (indirekt) an diesem DTO? | incoming | field_type_uses, method_param_type_uses, method_return_type_uses, generic_type_uses, mapper_maps_type |
| `controller-test-coverage` | Welche Tests decken diesen Controller ab? | incoming (empfohlen: `both`) | test_targets_type, test_uses_controller, test_calls_endpoint |
| `field-policy-impact` | Welche Policies/Guards betreffen dieses Feld? | incoming | policy_applies_to_field, permission_checks_field, interceptor_guards_method, frontend_guard_refs_field, role_allows_operation |
| `service-dependency-chain` | Welche Abhängigkeiten hat dieser Service? | outgoing | injects_dependency, constructor_injection, declares_bean, service_uses_repository, transactional_boundary |

Ergebnis-Klassifikationen:

- `controller-test-coverage`: `coverage_kind` ∈ direct_controller_test | endpoint_test |
  indirect_evidence | suspected_coverage. Es wird **nie** "covered" behauptet, wenn nur
  heuristische Kanten existieren.
- `field-policy-impact`: `enforcement` ∈ enforced_backend_guard | frontend_reference |
  weak_reference. Frontend-Guards gelten nie als Backend-Enforcement.
- `service-dependency-chain`: `dependency_kind` ∈ direct_dependency | indirect_dependency;
  Zyklen werden in `diagnostics.service_dependency_cycles_detected` gemeldet,
  transaktionale Grenzen erscheinen als `transactional_boundary` im Evidence-Pfad.

## API

```
GET /api/codecompass/query?knowledge_index_id=<id>&type=<query_type>&seed=<symbol-or-node-id>
    [&field=<feldname>][&depth=<1..max>][&direction=outgoing|incoming|both]
```

- Unbekannter `type` → 400 mit Liste gültiger Typen.
- Fehlende `knowledge_index_id` → 400 (wie bei den Graph-Endpunkten).
- Antwortschema: `codecompass_architecture_query_result.v1`
  (vollständige Beispiele: `docs/contracts/codecompass-architecture-query-result.md`).

Limits werden über `agent/config.py` gesteuert:
`CODECOMPASS_QUERY_MAX_DEPTH` (4), `CODECOMPASS_QUERY_MAX_NODES` (200),
`CODECOMPASS_QUERY_MAX_RESULTS` (25), `CODECOMPASS_QUERY_MAX_PATHS_PER_RESULT` (3).
`diagnostics.bounded=true` und `applied_limits` zeigen die angewandten Grenzen.

## Seed-Resolution (CCAQE-005)

Reihenfolge: exakte Node-ID → exakte Record-ID → Feldknoten (`Type.field`) →
exakter Name → case-insensitiver Name → Datei-/Pfadfragment → FTS-Fallback.
Mehrdeutige Seeds liefern mehrere Kandidaten plus `ambiguous_seed`-Warnung;
nicht auflösbare Seeds liefern leere Ergebnisse plus `seed_not_resolved` —
es wird nie geraten.

## Ranking (CCAQE-006)

`path_score = Π(edge_confidence × edge_type_gewicht) × 0.85^(pfadlänge−1)`

- Kürzere Pfade schlagen längere bei gleicher Confidence.
- Harte Kanten (`field_type_uses` = 1.0) schlagen heuristische
  (`calls_probable_target` = 0.5) bei gleicher Tiefe.
- Sortierung ist deterministisch: (−score, result_node_id).

## Datenquellen und Extraktion (CCAQE-013–015)

Die Kanten stammen aus dem rag-helper-Extractor
(`rag-helper/rag_helper/extractors/`):

- **Typed Uses:** Felder → `field_type_uses`, Methodenparameter →
  `method_param_type_uses`, Return-Typen → `method_return_type_uses`,
  Generics (`List<UserDto>`) → `generic_type_uses`.
- **Tests/Controller:** `@WebMvcTest(X.class)` → `test_targets_type`;
  Mapping-Annotationen → `controller_endpoint_declares` (mit `endpoint_path`);
  `mockMvc.perform(get("/x"))` → `test_calls_endpoint` (heuristisch, reduzierte
  Confidence); `@MockBean` → `mock_injects_dependency`; Controller-Felder in
  Testklassen → `test_uses_controller`.
- **Policies:** `@PreAuthorize`/`@Secured`/`@RolesAllowed` →
  `permission_checks_field` (mit `field`/`operation`) bzw. `role_allows_operation`;
  Custom-Guard-Annotationen → `interceptor_guards_method` (heuristisch markiert).
  Frontend-Guards (`frontend_guard_refs_field`) sind im Vertrag vorgesehen;
  eine TS-Extraktion existiert noch nicht (Limitation, siehe Trust-Model).

`build_graph_edges` (rag-helper) löst Symbol-Relationen über FQN- und
Namens-Maps auf Node-IDs auf; nicht auflösbare Referenzen kommen **nicht** in
den Graphen (keine dangling edges). `test_calls_endpoint` wird über die von
`controller_endpoint_declares` deklarierten Endpoint-Pfade aufgelöst.

## Agent-Handoff (CCAQE-019)

`render_query_result_markdown(result)` in
`worker/retrieval/codecompass_architecture_query.py` rendert ein kompaktes
Markdown mit Query, Seed, Ergebnissen, Evidence-Pfaden und Warnungen.
Security-Warnings werden nie gefiltert; leere Ergebnisse erscheinen als
"nicht gefunden / nicht belegt".

## UI (CCAQE-018)

`web/www/codecompass/query.html`: Query-Typ-Dropdown, Seed/Feld/Tiefe/Richtung,
Ergebnisliste mit Rolle, Score, Tiefe, Evidence-Pfaden; Warnungen werden
prominent angezeigt, Fehler (z. B. `seed_not_resolved`) lesbar dargestellt.
Hub-URL und Agent-Token sind konfigurierbar, da die Seite statisch ausgeliefert wird.

## Storage-Backends (CCAQE-021/022/023)

**Default:** JSON-GraphStore (`cc_graph_index.json`), pro Query einmal geladen
und im Store-Objekt gecacht.

**Optional (Design):** `SQLiteGraphStore` mit denselben Vertragsmethoden
(`get_node`, `find_nodes_by_name`, `find_nodes_by_file`, `outgoing_edges`,
`incoming_edges`, `traverse_paths`). Vorgeschlagenes Schema:

```sql
CREATE TABLE cc_graph_nodes (
  node_id   TEXT PRIMARY KEY,
  kind      TEXT NOT NULL,
  name      TEXT,
  file      TEXT,
  record_id TEXT,
  content   TEXT,
  source_record TEXT  -- JSON
);
CREATE INDEX idx_cc_nodes_name ON cc_graph_nodes(name);
CREATE INDEX idx_cc_nodes_file ON cc_graph_nodes(file);
CREATE INDEX idx_cc_nodes_record ON cc_graph_nodes(record_id);

CREATE TABLE cc_graph_edges (
  source_id  TEXT NOT NULL,
  target_id  TEXT NOT NULL,
  edge_type  TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 1.0,
  field      TEXT,
  operation  TEXT,
  heuristic  TEXT,
  provenance TEXT  -- JSON
);
CREATE INDEX idx_cc_edges_source ON cc_graph_edges(source_id, edge_type);
CREATE INDEX idx_cc_edges_target ON cc_graph_edges(target_id, edge_type);
```

Beide Stores erfüllen denselben QueryEngine-Vertrag; die Contract-Tests in
`tests/test_codecompass_graph_store.py` laufen parametrisiert gegen beide.
Der SQLiteGraphStore nutzt nur `sqlite3` aus der Standardbibliothek (keine neue
Runtime-Abhängigkeit) und ist **nicht** Default — Aktivierung nur explizit über
den aufrufenden Code. FTS-Store und Graph-Store können später dieselbe
SQLite-Datei oder getrennte Dateien nutzen.

**Externe Graph-DBs (Neo4j, Memgraph, Kuzu):** nur als optionale spätere Adapter.
Kriterien, ab wann sich das lohnt:

- mehrere Repos in einem gemeinsamen Graphen,
- sehr große Graphen (>10⁶ Kanten), bei denen SQLite-Traversal zu langsam wird,
- interaktive, komplexe Cypher-/GQL-Abfragen durch Menschen,
- persistente Multi-User-Analyse mit Berechtigungen.

Bis dahin gilt: Ananta bleibt lokal ohne zusätzlichen Graph-Service lauffähig;
keine Docker-Compose-Pflicht für CodeCompass.

## Tests

```
pytest -q tests/test_codecompass_architecture_query.py   # Engine, Queries, Handoff
pytest -q tests/test_codecompass_graph_store.py          # Store-Vertrag (JSON + SQLite)
pytest -q tests/test_codecompass_graph_api.py            # REST-API
pytest -q rag-helper/tests/test_architecture_query_edges.py  # Extractor-Kanten
```
