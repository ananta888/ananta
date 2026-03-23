# DeerFlow Integration

Dieses Dokument beschreibt den ersten Integrationspfad fuer `DeerFlow` als externes Research-Backend innerhalb von `ananta`.

## Zielbild

- `ananta` bleibt die Control Plane fuer Tasks, Routing, Review und Persistenz.
- `DeerFlow` wird als spezialisierter Backend-Typ fuer Rechercheaufgaben genutzt.
- Die erste Integrationsstufe laeuft ueber einen CLI-Runner.
- Ergebnisse werden in `ananta` als strukturierte Research-Artefakte abgelegt.

## Konfiguration

Die Integration wird ueber `AGENT_CONFIG.research_backend` gesteuert:

```json
{
  "research_backend": {
    "provider": "deerflow",
    "enabled": true,
    "mode": "cli",
    "command": "uv run main.py {prompt}",
    "working_dir": "/path/to/deer-flow",
    "timeout_seconds": 900,
    "result_format": "markdown"
  }
}
```

Wichtige Felder:

- `provider`: derzeit `deerflow`
- `enabled`: schaltet das Backend frei
- `mode`: aktuell nur `cli`
- `command`: Kommando fuer den DeerFlow-Aufruf; `{prompt}` wird ersetzt
- `working_dir`: Arbeitsverzeichnis des DeerFlow-Repositories
- `timeout_seconds`: Timeout fuer lange Research-Laeufe
- `result_format`: aktuell `markdown`

## Routing

Der erste Routing-Pfad ist ueber `sgpt_routing.task_kind_backend.research=deerflow` vorgesehen.

Beispiele fuer automatische Zuordnung zu `research`:

- Prompts mit `research`
- Prompts mit `investigate`
- Prompts mit `compare`
- Prompts mit `sources`
- Prompts mit `report`

## Laufzeitverhalten

Bei erfolgreichem DeerFlow-Lauf erzeugt `ananta` ein strukturiertes Research-Artefakt mit:

- `summary`
- `report_markdown`
- `sources`
- `backend_metadata`

Diese Daten landen derzeit im Task-Vorschlag und in der Task-Historie; bei der Ausfuehrung kann ein reiner Research-Vorschlag ohne Shell-Command direkt als abgeschlossen persistiert werden.

Zusaetzlich werden jetzt fuer Proposal und Execution strukturierte `trace`-Metadaten erzeugt, damit Routing-Entscheidung, Backend und Policy-Version nachvollziehbar bleiben.

## Review und Approval

Research-Berichte aus `deerflow` werden standardmaessig als review-pflichtig behandelt.

- Proposal enthaelt `review.required=true`
- Status startet bei `review.status=pending`
- Ausfuehrung eines reinen Research-Artefakts wird blockiert, solange kein Review erfolgt ist
- Freigabe oder Ablehnung laeuft ueber `POST /tasks/<id>/review`

Beispiel:

```json
{
  "action": "approve",
  "comment": "Report freigegeben"
}
```

## Preflight

`GET /api/sgpt/backends` liefert fuer DeerFlow zusaetzlich:

- `preflight.research_backends.deerflow.provider`
- `preflight.research_backends.deerflow.command`
- `preflight.research_backends.deerflow.binary_available`
- `preflight.research_backends.deerflow.working_dir`
- `preflight.research_backends.deerflow.working_dir_exists`
- `preflight.research_backends.deerflow.timeout_seconds`

## Bekannte Grenzen

- Es gibt noch keinen nativen HTTP-Adapter; der erste Pfad ist bewusst CLI-basiert.
- Output wird derzeit als Markdown-orientierter Report interpretiert.
- Quellen werden aktuell heuristisch aus URLs im Output extrahiert.
- Human-Review fuer Research-Berichte ist noch nicht integriert.

## Empfohlene Reihenfolge

1. DeerFlow lokal lauffaehig machen.
2. `research_backend` konfigurieren.
3. `GET /api/sgpt/backends` auf DeerFlow-Preflight pruefen.
4. Research-Prompt ueber `/api/sgpt/execute` oder Task-Propose testen.
5. Danach den HTTP-Adapter und den Review-Flow ergaenzen.
