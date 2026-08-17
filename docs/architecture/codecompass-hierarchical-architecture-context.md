# CodeCompass Hierarchical Architecture Context

## Übersicht

Dieses Dokument beschreibt das semantische Architektur-Knotenmodell und die Beziehungstypen für die hierarchische Architektursicht in CodeCompass (HAC-002).

## Ziel

Agenten wie AI-Snake und Ananta-Worker benötigen für unterschiedliche Aufgaben verschiedene Granularitätsebenen:
- **Verständnis**: System- und Subsystem-Übersicht
- **Navigation**: Komponenten und Dateien
- **Modifikation**: Konkrete Symbole und Code-Stellen

Eine reine Trefferliste von Dateien erklärt nicht die Rolle einer Komponente im Gesamtsystem.

## Hierarchie-Level

Das Modell definiert 5 explizite Hierarchielevel:

| Level | Beschreibung | Beispiel | Typische Token |
|-------|-------------|----------|----------------|
| `system` | Gesamtsystem mit Hauptziel | Ananta Platform | 100-300 |
| `subsystem` | Große Funktionsbereiche | CodeCompass, Worker, UI | 50-200 |
| `component` | Implementierungseinheiten | Context Planner, Graph Store | 30-150 |
| `file` | Konkrete Quelldateien | `codecompass_context_planner_service.py` | 10-50 |
| `symbol` | Funktionen, Klassen, Methoden | `plan_context()`, `GraphStore` | 5-20 |

## Beziehungstypen

### Initiale Beziehungstypen

| Relationship | Beschreibung | Richtung | Beispiel |
|-------------|--------------|----------|----------|
| `contains` | Strukturelle Enthaltensein | Parent → Child | System enthält Subsystem |
| `uses` | Nutzung zur Laufzeit | User → Used | Component A uses Component B |
| `calls` | Funktionsaufruf | Caller → Callee | Funktion ruft andere Funktion |
| `depends_on` | Compile-/Import-Abhängigkeit | Dependent → Dependency | Modul importiert anderes Modul |
| `provides_context_to` | Kontextbereitstellung | Provider → Consumer | Context Planner provides context to Agent |
| `implements` | Implementierungsbeziehung | Component → File | Komponente wird durch Datei implementiert |
| `exposes_tool` | Tool-Exposition | Service → Tool | Service stellt Tool bereit |
| `stores` | Datenpersistenz | Writer → Store | Service schreibt in Store |
| `retrieves_from` | Datenabruf | Reader → Store | Service liest aus Store |
| `governed_by` | Governance/Policy | Subject → Policy | Component governed by Security Policy |
| `extends` | Vererbung/Erweiterung | Child → Parent | Klasse erweitert Basisklasse |
| `imports` | Import-Beziehung | Importer → Imported | Modul importiert Symbol |
| `exports` | Export-Beziehung | Exporter → Exported | Modul exportiert Symbol |

### Beziehungskategorien

1. **Strukturell** (deterministisch):
   - `contains`, `implements`, `extends`
   - Aus Build-Artefakten oder AST extrahiert

2. **Laufzeit** (extrahiert/inferiert):
   - `uses`, `calls`, `provides_context_to`
   - Aus Traces, Logs oder statischer Analyse

3. **Datenfluss** (extrahiert):
   - `stores`, `retrieves_from`
   - Aus Code-Analyse oder Datenbank-Schema

4. **Governance** (manuell/extrahiert):
   - `governed_by`, `exposes_tool`
   - Aus Konfiguration oder Manual Fixtures

## Semantische Kurzbeschreibung

Jeder Knoten enthält eine `short_summary` (max. 500 Zeichen, 1-3 Sätze), die:
- Die **Verantwortlichkeit** des Knotens beschreibt
- Aus **Evidence** (Code, Docs, Graph) abgeleitet ist
- **Revisionsgebunden** gespeichert wird (bei Änderung des Codes muss Summary aktualisiert werden)

### Beispiel: Ananta → CodeCompass → Context Planner

```json
{
  "nodes": [
    {
      "id": "sys-ananta",
      "level": "system",
      "title": "Ananta Platform",
      "short_summary": "Intelligente Entwicklungsplattform mit Agenten-Orchestrierung, Code-Verständnis und Workflow-Automatisierung für softwareentwicklungs-Teams.",
      "responsibilities": [
        "Agenten-Runtime für autonome und assistierte Entwicklung bereitstellen",
        "Semantisches Code-Verständnis über CodeCompass ermöglichen",
        "Workflow-Automatisierung über visuelle Prozess-Editoren"
      ],
      "source_refs": [
        {"path": "README.md", "line_start": 1, "line_end": 20},
        {"path": "docs/architecture/ananta-roadmap.md", "line_start": 1, "line_end": 50}
      ],
      "trust": "manual",
      "confidence": 1.0
    },
    {
      "id": "ss-codecompass",
      "level": "subsystem",
      "title": "CodeCompass",
      "short_summary": "Kontext-Engine für semantisches Code-Verständnis, Retrieval und Budgetierung. Liefert Agenten maßgeschneiderten Kontext aus Symbolgraph, RAG und Repository Intelligence.",
      "responsibilities": [
        "Symbolgraph aus Code extrahieren und pflegen",
        "Semantische Suche über Embeddings und FTS",
        "Kontext-Budgetierung für LLM-Prompts"
      ],
      "source_refs": [
        {"path": "docs/codecompass.md", "line_start": 1, "line_end": 100}
      ],
      "parent_id": "sys-ananta",
      "trust": "extracted",
      "confidence": 0.95
    },
    {
      "id": "comp-context-planner",
      "level": "component",
      "title": "Context Planner Service",
      "short_summary": "Wählt basierend auf Query, Agent-Profil und Budget einen relevanten Kontext-Slice aus dem CodeCompass-Graph aus und rankt Kandidaten.",
      "responsibilities": [
        "Query parsen und Intent erkennen",
        "Kandidaten aus Multi-Channel-Retrieval sammeln",
        "Ranking anwenden und Budgets durchsetzen"
      ],
      "source_refs": [
        {"path": "agent/services/codecompass_context_planner_service.py", "line_start": 1, "line_end": 100}
      ],
      "parent_id": "ss-codecompass",
      "trust": "extracted",
      "confidence": 0.9
    }
  ],
  "edges": [
    {
      "source_id": "sys-ananta",
      "target_id": "ss-codecompass",
      "relationship": "contains",
      "description": "Ananta enthält CodeCompass als Kern-Subsystem für Code-Verständnis.",
      "confidence": 1.0
    },
    {
      "source_id": "ss-codecompass",
      "target_id": "comp-context-planner",
      "relationship": "contains",
      "description": "CodeCompass enthält Context Planner als zentrale Komponente für Kontext-Auswahl.",
      "confidence": 0.95
    }
  ]
}
```

## Deterministische vs. Inferierte Beziehungen

| Beziehungstyp | Herkunft | Deterministisch? | Confidence |
|--------------|----------|------------------|------------|
| `contains` (System→Subsystem) | Manuelle Architektur-Docs | Ja | 1.0 |
| `contains` (Subsystem→Component) | Verzeichnisstruktur + Code-Organisation | Ja | 0.9-1.0 |
| `implements` | Datei enthält Hauptlogik der Komponente | Ja | 0.9-1.0 |
| `imports` | AST / Import-Statements | Ja | 1.0 |
| `calls` | Statische Analyse (Tree-sitter) | Nein (maybe_call) | 0.6-0.9 |
| `uses` | Laufzeit-Traces oder heuristisch | Nein | 0.5-0.8 |
| `provides_context_to` | Aus Config oder manueller Zuordnung | Teilweise | 0.7-0.9 |

## Evidence-Anforderungen pro Level

| Level | Minimale Evidence | Trust-Level |
|-------|-------------------|-------------|
| `system` | Architektur-Docs, README | `manual` oder `extracted` |
| `subsystem` | Doku + Verzeichnisstruktur | `extracted` |
| `component` | Hauptdatei + Docstrings | `extracted` |
| `file` | Existierende Datei im Repo | `deterministic` |
| `symbol` | AST-Node mit Position | `deterministic` |

## Revision-Bindung

Semantische Zusammenfassungen (`short_summary`, `responsibilities`) sind revisionsgebunden:
- Bei Änderung der referenzierten Source-Dateien muss die Summary validiert werden
- Cache-Key beinhaltet Revision-Hash
- TTL für Summaries: 24h oder bei nächstem Index-Update

## Erweiterung des Bestehenden Modells

Dieses Modell erweitert `docs/codecompass.md` um:
1. Explizite 5-Level-Hierarchie (vorher nur flache Graph-Nodes)
2. Semantische Verantwortungserklärungen pro Knoten
3. Budgetierte Slices statt vollständiger Graph
4. Push/Pull-Dualismus (initialer Kontext vs. on-demand Expansion)

## Nächste Schritte

- HAC-003: Budget-Policies definieren
- HAC-004: Evidence-/Security-Regeln anwenden
- HAC-005: Hierarchische Projektion implementieren
