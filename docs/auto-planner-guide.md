# Auto-Planner Guide

## Übersicht

Der Auto-Planner ist ein LLM-gestütztes System zur automatischen Task-Generierung aus High-Level-Zielen (Goals).

## Kernfeatures

| Feature | Beschreibung |
|---------|--------------|
| **Goal → Subtasks** | Zerlegt ein Ziel in strukturierte Teilaufgaben |
| **Templates** | Nutzt vordefinierte Vorlagen für häufige Goal-Typen |
| **Repo-Kontext** | Lädt automatisch relevanten Code-Kontext via Hybrid-RAG |
| **Followup-Erkennung** | Analysiert Task-Outputs auf Folgeaufgaben |
| **Prompt-Schutz** | Validiert Inputs gegen Prompt-Injection |

## API-Endpunkte

### Status abrufen
```bash
GET /tasks/auto-planner/status
```

### Konfigurieren
```bash
POST /tasks/auto-planner/configure
{
  "enabled": true,
  "auto_followup_enabled": true,
  "max_subtasks_per_goal": 10,
  "default_priority": "Medium",
  "auto_start_autopilot": true,
  "llm_timeout": 30
}
```

### Goal planen
```bash
POST /tasks/auto-planner/plan
{
  "goal": "Implementiere User-Login mit JWT",
  "context": "Verwende Flask und PostgreSQL",
  "team_id": "optional-team-id",
  "create_tasks": true
}
```

### Followups analysieren
```bash
POST /tasks/auto-planner/analyze/<task_id>
{
  "output": "Task-Ausgabe...",
  "exit_code": 0
}
```

## Goal-Templates

Der Auto-Planner erkennt automatisch Goal-Typen und nutzt passende Templates:

| Typ | Keywords | Subtasks |
|-----|----------|----------|
| **Bug Fix** | bug, fix, fehler, error | Reproduzieren → Root Cause → Fix → Test → Review |
| **Feature** | feature, implement, add | Anforderungen → Design → Implementierung → Tests → Docs |
| **Refactor** | refactor, cleanup, improve | Analyse → Plan → Durchführen → Tests verifizieren |
| **Test** | test, testing, coverage | Strategie → Unit Tests → Integration Tests → Report |

## Workflow

```
1. User gibt Goal ein
2. Auto-Planner prüft auf Template-Match
3. Falls Template: Nutzt vordefinierte Subtasks
4. Sonst: LLM generiert Subtasks
5. Tasks werden erstellt
6. Autopilot startet automatisch (falls konfiguriert)
7. Nach Abschluss: Followup-Analyse
8. Neue Tasks werden erkannt und erstellt
```

## Frontend-Integration

Die Auto-Planner UI ist unter `/auto-planner` erreichbar und bietet:
- Status-Übersicht
- Konfigurationsformular
- Goal-Eingabe mit Kontext
- Ergebnis-Anzeige

## Best Practices

1. **Klare Goals**: Formuliere Ziele präzise für bessere Subtasks
2. **Kontext nutzen**: Gib Framework-/Stack-Informationen an
3. **Templates bevorzugen**: Standardisierte Goals profitieren von Templates
4. **Followups aktivieren**: Automatische Folgeaufgaben-Suche

## Sicherheit

- Input-Sanitizing gegen Prompt-Injection
- Maximale Goal-Länge: 4000 Zeichen
- Validierung kritischer Patterns
