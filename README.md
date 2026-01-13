# Ananta

Ein modulares Multi-Agent-System für AI-gestützte Entwicklung. Ananta besteht aus unabhängigen Agenten, die entweder als zentrale Steuereinheit (**Hub**) oder als ausführende Einheiten (**Worker**) fungieren können.

## High-Level Architektur

Die Architektur wurde auf ein Hub-Worker-Modell vereinfacht:
- **Angular Frontend**: Single Page App zur Visualisierung und Steuerung.
- **AI Agent (Hub)**: Verwaltet Tasks, Templates und orchestriert die Worker. Nutzt eine Postgres-Datenbank für Persistenz.
- **AI Agent (Worker)**: Führt Shell-Befehle aus und interagiert mit LLMs.
- **Postgres DB**: Zentrale Datenbank für Hub und Worker.

## Quickstart

Der einfachste Weg, Ananta zu starten, ist über Docker Compose:

```bash
# Standard-Start (erfordert funktionierendes Docker-Netzwerk für Postgres)
docker-compose up -d

# Falls Netzwerkfehler (IPv6) beim Herunterladen von Postgres auftreten:
docker-compose -f docker-compose.sqlite.yml up -d
```

Frontend: `http://localhost:4200` | Hub: `http://localhost:5000` | Worker: `http://localhost:5001`

**Initialer Login (Standard):**
- **Benutzer:** `admin`
- **Passwort:** `admin`

*Hinweis: Bitte ändern Sie das Passwort nach dem ersten Login in den Einstellungen.*

Für die lokale Entwicklung siehe [frontend-angular/README.md](frontend-angular/README.md) und [agent/README.md](agent/README.md).

### Fehlerbehebung LLM-Verbindung
Falls die Agenten keine Verbindung zu Ollama oder LMStudio herstellen können (`Connection refused`):
1. Öffnen Sie den Projektordner.
2. Klicken Sie rechts auf **`setup_host_services.ps1`** -> "Mit PowerShell ausführen".
3. Dies konfiguriert automatisch die Firewall und den Netzwerk-Proxy auf Ihrem Windows-Host.

## Struktur

- `agent/` – Python-Code für den AI-Agent (Hub & Worker).
- `frontend-angular/` – Angular Dashboard.
- `data/` – Lokale Persistenz (Tasks, Templates, Konfiguration, Logs).
- `docs/` – Weiterführende Dokumentation.
- `api-spec.md` – Detaillierte API-Spezifikation.

## Komponenten

### AI-Agent (`agent/ai_agent.py`)
Der Agent ist ein Flask-basierter API-Server. Je nach Konfiguration (`ROLE=hub` oder `ROLE=worker`) stellt er unterschiedliche Funktionen bereit:
- **Worker**: Bietet Endpunkte für `/step/propose` (LLM-Vorschlag) und `/step/execute` (Shell-Ausführung).
- **Hub**: Erweitert den Worker um Task-Management, Template-Verwaltung und Weiterleitung von Anfragen an Worker.

### Frontend (`frontend-angular/`)
Ein modernes Angular-Dashboard zur:
- Anzeige und Verwaltung von Agenten.
- Erstellung und Überwachung von Tasks.
- Bearbeitung von Prompt-Templates.
- Echtzeit-Einsicht in Terminal-Logs.

## API-Dokumentation

Eine detaillierte Beschreibung aller verfügbaren Endpunkte finden Sie in:
- [api-spec.md](api-spec.md) – Allgemeine Übersicht.
- [agent/README.md](agent/README.md) – Spezifische Details zum Agent-Server.

## Persistenz

Alle Daten werden im `data/` Verzeichnis gespeichert:
- `tasks.json`: Aufgabenliste.
- `templates.json`: Prompt-Vorlagen.
- `config.json`: Agent-spezifische Einstellungen.
- `terminal_log.jsonl`: Verlauf aller Terminal-Ausgaben.

## Entwicklung & Qualitätssicherung

### Tests
- **Python**: `python -m unittest discover tests`
- **Frontend**: `npm test` (Playwright E2E-Tests)

### Linting & Typ-Check
- **Python**: `flake8 .` und `mypy agent`
- **Frontend**: `npm run lint`

## Dokumentation

- [docs/INSTALL_TEST_BETRIEB.md](docs/INSTALL_TEST_BETRIEB.md) – **Installations-, Test- und Betriebsanleitung** (Deutsch). Inklusive Troubleshooting für Netzwerk und LLM-Verbindungen.
- [docs/roadmap.md](docs/roadmap.md) – Geplante Features und Meilensteine.
- [docs/dashboard.md](docs/dashboard.md) – Details zum Angular Frontend.
- [agent/README.md](agent/README.md) – Handbuch für den AI-Agent.
