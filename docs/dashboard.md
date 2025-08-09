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

```bash
# install dependencies
npm install

# set environment variables
cp .env.example .env   # adjust API URL if needed

# optional: specify controller URL
echo "VITE_API_URL=http://localhost:8081" >> .env
```

Der Entwicklungsserver läuft auf `http://localhost:5173` und erwartet, dass der Controller unter `http://localhost:8081` erreichbar ist.

## API Examples

```bash
# fetch controller config
curl http://localhost:8081/config

# toggle an agent
curl -X POST http://localhost:8081/agent/Architect/toggle_active

# fetch agent logs
curl http://localhost:8081/agent/Architect/log
```

## Entwicklungsbefehle

```bash
npm run dev    # Entwicklungsserver starten
npm run build  # Produktions-Bundle erstellen
```

Nach dem Build werden die Dateien in `dist/` erzeugt und vom Controller unter `/ui` ausgeliefert.
