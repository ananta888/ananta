# Dashboard-Architektur und API-Übersicht

Dieses Dokument fasst die Gesamtarchitektur des Ananta-Dashboards zusammen und listet die wichtigsten HTTP-Endpunkte auf. Die Plattform besteht aus drei Hauptkomponenten:

- **Controller** – Flask-Server, der Konfigurationen in PostgreSQL verwaltet und Endpunkte bereitstellt.
- **AI-Agent** – Python-Skript, das Aufgaben pollt, Prompts generiert und Kommandos ausführt.
- **Vue-Dashboard** – Browseroberfläche zur Anzeige von Logs und Steuerung der Agenten.

## Architektur

1. Der AI-Agent fragt den Controller periodisch über `/next-config` nach neuer Konfiguration.
2. Basierend auf dieser Konfiguration erstellt der Agent Prompts und sendet Ergebnisse über `/approve` zurück.
3. Das Vue-Dashboard ruft Controller-Endpunkte wie `/config` oder `/agent/<name>/log` sowie den Agent-Endpunkt `/agent/config` auf, um Statusinformationen aus der Datenbank anzuzeigen.
4. Der Controller stellt nach `npm run build` das gebaute Dashboard unter `/ui` bereit.

## Wichtige API-Endpunkte

| Endpoint | Methode | Beschreibung |
| -------- | ------- | ------------ |
| `/next-config` | GET | Liefert die nächste Agenten-Konfiguration inkl. Aufgaben & Templates. |
| `/config` | GET | Gibt die vollständige Controller-Konfiguration aus PostgreSQL zurück. |
| `/config/api_endpoints` | POST | Aktualisiert LLM-Endpunkte inklusive Modell-Liste. |
| `/agent/config` | GET | Liefert die Agent-Konfiguration aus dem Schema `agent`. |
| `/approve` | POST | Validiert und führt Agenten-Vorschläge aus. |
| `/issues` | GET | Holt GitHub-Issues und reiht Aufgaben ein. |
| `/set_theme` | POST | Speichert das Dashboard-Theme im Cookie. |
| `/agent/<name>/toggle_active` | POST | Schaltet `controller_active` eines Agents um. |
| `/agent/<name>/log` | GET/DELETE | Liefert oder löscht Logeinträge eines Agents aus der Datenbank. |
| `/stop`, `/restart` | POST | Setzt bzw. entfernt Stop-Flags in der Datenbank. |
| `/export` | GET | Exportiert Logs und Konfigurationen als ZIP. |
| `/ui`, `/ui/<pfad>` | GET | Serviert das gebaute Vue-Frontend. |
| `/controller/status` | GET/DELETE | ControllerAgent-Log einsehen oder leeren. |
| `/controller/models` | GET/POST | Übersicht und Registrierung von LLM-Modell-Limits. |

Jeder Eintrag in `api_endpoints` enthält die Felder `type`, `url` und eine Liste `models` der verfügbaren LLM-Modelle.

## Environment Setup

> Requires Node.js 18+ and npm.

```bash
# install dependencies
npm install

# install browsers for Playwright
npx playwright install

# set environment variables
cp .env.example .env   # adjust API URL if needed

# optional: specify controller URL
echo "VITE_API_URL=http://localhost:8081" >> .env

# run e2e tests
npm test
```

Der Entwicklungsserver läuft auf `http://localhost:5173` und erwartet, dass der Controller unter `http://localhost:8081` erreichbar ist.

## API Examples

```bash
# fetch controller config
curl http://localhost:8081/config
curl http://localhost:8081/health


# toggle an agent
curl -X POST http://localhost:8081/agent/Architect/toggle_active

# submit approval
curl -X POST http://localhost:8081/approve -H "Content-Type: application/json" -d "{}"

# fetch agent logs
curl http://localhost:8081/agent/Architect/log
```

## Entwicklungsbefehle

```bash
npm run dev    # Entwicklungsserver starten
npm run build  # Produktions-Bundle erstellen
```

Nach dem Build werden die Dateien in `dist/` erzeugt und vom Controller unter `/ui` ausgeliefert.


## Task-Status und Monitoring (neu)

- Persistente Aufgabenverwaltung optional aktivierbar über Umgebungsvariable: `TASK_STATUS_MODE=enhanced`.
- Statusmodell: `queued` → `in_progress` → `done`/`failed` → optional `archived`.
- Audit-Log: Jede Aufgabe hat ein JSON-Logfeld (`log`) sowie Audit-Felder (`created_by`, `picked_by`, `picked_at`, `completed_at`, `fail_count`).

### Neue/erweiterte Endpunkte
- POST `/agent/add_task`
  - Body: `{ "task": string, "agent"?: string, "template"?: string, "created_by"?: string }`
  - Legt eine Aufgabe mit Status `queued` an und schreibt einen `created`-Logeintrag.
- GET `/tasks/next`
  - Legacy (Default): antwortet `{ "task": string|null }` und löscht die Aufgabe (wie bisher).
  - Enhanced (`TASK_STATUS_MODE=enhanced`): antwortet `{ "task": string|null, "id"?: number }`, setzt Status auf `in_progress` und protokolliert `picked`.
- POST `/tasks/<id>/status`
  - Body: `{ "status": "done"|"failed"|"queued"|"archived", "message"?: string, "agent"?: string }`
  - Aktualisiert den Status und hängt einen Logeintrag an. Bei `failed` wird `fail_count` erhöht, bei `done` `completed_at` gesetzt.
- GET `/tasks/stats`
  - Optionaler Query-Parameter `?agent=Name`. Liefert Zähler pro Status: `{ counts: {queued: n, ...}, total: N }`.
- GET `/agent/<name>/tasks`
  - Enthält nun zusätzlich `status` und blendet `archived`-Tasks aus.

Hinweis: Es gibt weiterhin eine Sichtbarkeitsverzögerung (`TASK_CONSUME_DELAY_SECONDS`, Standard 8s), damit Aufgaben kurz im UI sichtbar bleiben, bevor ein Agent sie konsumiert.
