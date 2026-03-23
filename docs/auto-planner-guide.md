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
| **Prompt-Schutz** | Sanitisiert Inputs, filtert verdaechtige Task-Inhalte und klassifiziert Parse-Fehler |

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

Erweiterte Flags:
- `use_template`: `true|false` (Template-basierte Zerlegung aktivieren/deaktivieren)
- `use_repo_context`: `true|false` (RAG-Kontext aktivieren/deaktivieren)
- `parent_task_id`: Optional, erzeugt Ableitungen unter einem Root-Task

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

## Robustheit bei LLM-Ausgaben

- JSON wird auch aus Markdown-Fences oder umgebendem Freitext extrahiert.
- Subtasks werden auf ein Mindest-Schema (`title`, `description`, `priority`) normalisiert.
- Verdaechtige Inhalte wie `system:` oder `ignore previous` werden verworfen.
- Bei unstrukturierten Antworten liefert die API einen klaren Fallback mit `error_classification`.

## First-Goal E2E mit lokalem LMStudio

Voraussetzungen:
- Compose Lite Umgebung laeuft (`docker-compose-lite.yml`).
- LMStudio ist erreichbar, z. B. `http://192.168.96.1:1234/v1`.

Beispiel Goal:
- `Erstelle eine einfache VWL simulation mit python als backend und angular als frontend`

Empfohlener API-Flow:
1. Auto-Planner mit lokalem Provider konfigurieren.
2. `POST /tasks/auto-planner/plan` mit `use_template=false`, damit Subtasks vom lokalen LLM erzeugt werden.
3. Autopilot starten oder ticken, damit Subtasks rollenbasiert abgearbeitet werden.
4. Fortschritt mit `GET /tasks`, `GET /tasks/timeline` und `GET /tasks/<id>/tree` pruefen.

Beispielkonfiguration:
```bash
POST /config
{
  "llm_config": {
    "provider": "lmstudio",
    "base_url": "http://192.168.96.1:1234/v1",
    "model": "local-model",
    "api_key": "lm-studio"
  }
}
```

## Task Management APIs fuer Hierarchien, Eingriffe und Archivierung

- `GET /tasks/<task_id>/tree`: rekursiver Ableitungsbaum
- `GET /tasks/hierarchy/view/<task_id>`: Baum plus UI-Aktionen (`assign`, `pause`, `cancel`, `retry`, `archive`)
- `POST /tasks/<task_id>/pause|resume|cancel|retry`: manuelle Eingriffe mit Audit-Trail
- `POST /tasks/archive/batch`: Batch-Archivierung mit Filtern
- `POST /tasks/archived/restore/batch`: Batch-Restore mit Filtern
- `POST /tasks/archive/retention/apply`: Retention-Bereinigung im Archiv
- `POST /tasks/derivation/backfill`: Backfill fuer `source_task_id`, `derivation_reason`, `derivation_depth`

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
- Followup-Analysen akzeptieren nur normalisierte JSON-Strukturen und ignorieren verdaechtige Eintraege
