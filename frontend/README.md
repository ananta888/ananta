# Frontend
# Ananta-Frontend (Vue 3)

Dieses Verzeichnis enthält die Frontend-Anwendung für das Ananta-System, entwickelt mit Vue 3.

## Entwicklung

### Installation der Abhängigkeiten

```bash
npm install
```

### Starten der Entwicklungsumgebung

```bash
npm run dev
```

### Erstellen des Produktions-Builds

```bash
npm run build
```

## Umgebung

- Node.js ≥ 18
- `VITE_API_URL` – Basis-URL des Controllers (Standard: `http://localhost:8081`)
- `PLAYWRIGHT_BASE_URL` – Basis-URL für E2E-Tests

## Struktur

- `src/` - Quellcode der Vue-Anwendung
- `public/` - Statische Dateien
- `dist/` - Kompilierte Anwendung (wird vom Controller-Dienst bedient)

## Integration mit dem Controller

Der Controller ist so konfiguriert, dass er die kompilierte Vue-Anwendung aus dem `dist/`-Verzeichnis bereitstellt. Nach einem erfolgreichen Build kann die Anwendung unter `http://localhost:8081/ui` aufgerufen werden.

## Docker-Integration

Bei der Verwendung von Docker wird das Frontend automatisch im Container-Build-Prozess erstellt. Die Dockerfile-Konfiguration stellt sicher, dass alle notwendigen Abhängigkeiten installiert und das Frontend kompiliert wird, bevor der Controller-Dienst gestartet wird.
Das Verzeichnis enthält ein Vue-3-Dashboard zur Steuerung und Überwachung der Agenten.

## Architektur

- Vite-Projekt mit Einstiegspunkt `src/main.js`.
- `App.vue` bietet eine Tab-Navigation und bindet die Komponenten `Pipeline.vue`, `Agents.vue`, `Tasks.vue` und `Templates.vue` ein.
- Jede Komponente kommuniziert per `fetch` mit dem Flask-Controller.
- Nach `npm run build` werden die Dateien in `dist/` erzeugt und vom Controller unter `/ui` ausgeliefert.
- Zustand wird mit der Vue 3 Composition API (`ref`, `reactive`) verwaltet. Für globale State-Verwaltung kann bei Bedarf [Pinia](https://pinia.vuejs.org/) integriert werden.

## API-Endpunkte

Das Dashboard nutzt folgende HTTP-Schnittstellen des Controllers sowie des AI-Agenten:

| Endpoint | Methode | Zweck |
|----------|--------|------|
| `/config` | GET | Aktuelle Controller-Konfiguration laden. |
| `/config/api_endpoints` | POST | LLM-Endpunkte inklusive Modell-Liste aktualisieren. |
| `/agent/config` | GET | Agent-Konfiguration laden. |
| `/` | POST | Formularaktionen für Pipeline, Tasks oder Templates auslösen. |
| `/agent/<name>/toggle_active` | POST | Aktiv-Status eines Agents ändern. |
| `/agent/<name>/log` | GET/DELETE | Logeinträge eines Agents abrufen oder löschen. |
| `/controller/status` | GET/DELETE | Controller-Log abrufen oder löschen. |
| `/stop` | POST | Laufende Agenten stoppen. |
| `/restart` | POST | `stop.flag` entfernen und Neustart veranlassen. |
| `/export` | GET | Logs und Konfigurationen als ZIP herunterladen. |

### Beispielanfrage

```bash
curl -H "X-API-Key: <key>" http://localhost:8081/config
# Antwort
{
  "agents": {},
  "api_endpoints": []
}
```

Jeder API-Endpoint speichert `type`, `url` und eine Liste `models` der verfügbaren Modelle und kann über die Komponente **Endpoints** bearbeitet werden.

## Befehle

```bash
npm run dev    # Entwicklungsserver starten
npm run build  # Produktions-Bundle erstellen
```

Die gebauten Dateien werden vom Flask-Controller unter `/ui` ausgeliefert.

## Theme & Accessibility

- Dark-/Light-Theme kann über die Einstellung im Dashboard gewechselt werden.
- Orientierung an WCAG 2.1: ausreichende Kontraste, Fokus-Styles und ARIA-Labels.

### Lighthouse-Audit

```bash
npm run build
npx lighthouse http://localhost:8081/ui --view
```

Überprüfen Sie regelmäßig die Ergebnisse und beheben Sie gemeldete Probleme.
