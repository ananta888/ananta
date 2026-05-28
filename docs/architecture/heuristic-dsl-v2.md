# Heuristic DSL v2

## Übersicht

DSL v2 ist eine deklarative, JSON-basierte Sprache für Snake-Heuristiken.
Sie ersetzt die Python-Strategy-Klassen für einfache Entscheidungsregeln.

**Warum LLM nicht direkt steuert:**
- LLM-Latenz (100ms–2s) ist inkompatibel mit dem 16ms/tick Fast Path
- LLM-Ausgaben sind nicht deterministisch — schlechte Basis für Positions-Entscheidungen
- DSL v2 erlaubt es dem LLM, Regeln zu *beschreiben*, die dann deterministisch ausgewertet werden
- Der `HeuristicExperimentRunner` führt LLM-generierte DSLs im Shadow Mode aus — kein UI-Einfluss

## Beispiel DSL v2

```json
{
  "dsl_version": "2.0",
  "observe": {
    "sources": ["tui.semantic", "tui.snapshot"]
  },
  "match": {
    "eq": ["source_surface", "tui_snake"]
  },
  "score": {
    "base": 0.8
  },
  "action": {
    "kind": "follow_artifact",
    "confidence": 0.8
  },
  "safety": {
    "safety_class": "ui_motion_only"
  },
  "provenance": {
    "created_by": "ananta-worker",
    "rationale": "Folgt dem nächsten Artifact auf der TUI"
  }
}
```

## Pflichtfelder

| Feld | Typ | Beschreibung |
|------|-----|--------------|
| `dsl_version` | `"2.0"` | Muss exakt `"2.0"` sein |
| `observe.sources` | `list[str]` | Datenquellen (siehe Allowed Sources) |
| `action.kind` | `str` | Aktion (siehe Allowed Actions) |
| `safety.safety_class` | `str` | `"ui_motion_only"` oder `"readonly"` |
| `provenance.created_by` | `str` | Wer hat die Heuristik erstellt |
| `provenance.rationale` | `str` | Begründung |

## Allowed Sources

- `tui.snapshot` — Vollständiger CellGrid-Snapshot
- `tui.delta` — Änderungen seit letztem Snapshot
- `tui.semantic` — Semantisches Overlay (Panels, Artifacts, Snake)
- `tui.mouse` — Mausposition
- `tui.focus` — Fokussiertes Panel
- `tui.history` — Historische Snapshots

## Allowed Actions

| kind | Beschreibung |
|------|--------------|
| `follow_artifact` | Snake folgt dem nächsten Artifact |
| `suggest_target` | Schlägt Zielposition vor |
| `fast_target` | Schnelles Ansteuern einer Position |
| `smooth_follow` | Sanftes Folgen |
| `lurk_near` | In der Nähe verweilen |
| `explain_target` | Erklärt das Ziel (Chat-Modus) |
| `no_action` | Keine Aktion |

## Verbotene Schlüssel

`inline_code`, `shell_command`, `exec`, `eval`, `import` — werden von `DslValidator` abgelehnt.

## Migration von v1 zu v2

### v1 (Python Strategy)

```python
class MySnakeStrategy(BaseSnakeStrategy):
    def decide(self, ctx):
        return PolicyDecision(allowed=True, reason_code="follow")
```

### v2 (DSL)

```json
{
  "dsl_version": "2.0",
  "observe": {"sources": ["tui.semantic"]},
  "action": {"kind": "follow_artifact", "confidence": 0.8},
  "safety": {"safety_class": "ui_motion_only"},
  "provenance": {"created_by": "migration", "rationale": "Migrated from v1"}
}
```

### Schritte

1. Erstelle DSL v2 JSON in `heuristics/candidates/`
2. Validierung: `DslValidator().validate(dsl)`
3. Simulation: `HeuristicSimulator().simulate(dsl, frames)`
4. Human Approval: `HeuristicActivationGate.activate(heuristic_id)`
5. Registry neu laden: `get_heuristic_registry().reload()`

### Breaking Changes

- `dsl_version` ist jetzt Pflicht (war optional in v1)
- `safety.safety_class` ist jetzt Pflicht
- `provenance.rationale` ist jetzt Pflicht
- Python-Strategien ohne Allowlist-Eintrag werden abgelehnt

## Evaluierungs-Pipeline

```
DSL JSON
  └─ DslValidator.validate()       ← Schema + Capability-Check
       └─ DslEvaluator.evaluate()  ← Deterministisch, kein LLM
            └─ DecisionResult      ← action_kind, confidence, source
```

## Lebenszyklus-Status

| Status | Bedeutung |
|--------|-----------|
| `candidate` | LLM-Vorschlag, noch nicht validiert |
| `experimental_live` | Validiert + simuliert, Shadow Mode (TTL ≤ 20s) |
| `active` | Human-approved, stabil aktiv |
| `rejected` | Abgelehnt nach Simulation oder Review |
| `archived` | Alte Version, nicht mehr aktiv |
