# Frontend

Das Verzeichnis enthält ein Vue-3-Dashboard zur Steuerung und Überwachung der Agenten.

## Architektur

- Vite-Projekt mit Einstiegspunkt `src/main.js`.
- `App.vue` bietet eine Tab-Navigation und bindet die Komponenten `Pipeline.vue`, `Agents.vue`, `Tasks.vue` und `Templates.vue` ein.
- Jede Komponente kommuniziert per `fetch` mit dem Flask-Controller.
- Nach `npm run build` werden die Dateien in `dist/` erzeugt und vom Controller unter `/ui` ausgeliefert.

## API-Endpunkte

Das Dashboard nutzt folgende HTTP-Schnittstellen des Controllers:

| Endpoint | Methode | Zweck |
|----------|--------|------|
| `/config` | GET | Aktuelle Konfiguration laden. |
| `/config/api_endpoints` | POST | LLM-Endpunkte inklusive Modell-Liste aktualisieren. |
| `/` | POST | Formularaktionen für Pipeline, Tasks oder Templates auslösen. |
| `/agent/<name>/toggle_active` | POST | Aktiv-Status eines Agents ändern. |
| `/agent/<name>/log` | GET | Logdatei eines Agents abrufen. |
| `/controller/status` | GET | Controller-Log abrufen. |
| `/stop` | POST | Laufende Agenten stoppen. |
| `/restart` | POST | `stop.flag` entfernen und Neustart veranlassen. |
| `/export` | GET | Logs und Konfigurationen als ZIP herunterladen. |

Jeder API-Endpoint speichert `type`, `url` und eine Liste `models` der verfügbaren Modelle und kann über die Komponente **Endpoints** bearbeitet werden.

## Befehle

```bash
npm run dev    # Entwicklungsserver starten
npm run build  # Produktions-Bundle erstellen
```

Die gebauten Dateien werden vom Flask-Controller unter `/ui` ausgeliefert.
