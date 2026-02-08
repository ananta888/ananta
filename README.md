# Ananta

Ein modulares Multi-Agent-System für AI-gestützte Entwicklung. Ananta besteht aus unabhängigen Agenten, die entweder als zentrale Steuereinheit (**Hub**) oder als ausführende Einheiten (**Worker**) fungieren können.

## High-Level Architektur

Die Architektur wurde auf ein Hub-Worker-Modell vereinfacht:
- **Angular Frontend**: Single Page App zur Visualisierung und Steuerung.
- **AI Agent (Hub)**: Verwaltet Tasks, Templates, Teams und Rollen und orchestriert die Worker. Nutzt eine SQL-Datenbank (Postgres/SQLite) für Persistenz.
- **AI Agent (Worker)**: Führt Shell-Befehle aus und interagiert mit LLMs.
- **SQL DB**: Zentrale Datenbank für Hub und Worker (Postgres empfohlen, SQLite als Fallback).

## Quickstart

Der einfachste Weg, Ananta zu starten, ist über Docker Compose:

1. **Konfiguration vorbereiten:**
   Kopieren Sie die `.env.example` Datei nach `.env` und passen Sie diese bei Bedarf an.
   ```bash
   cp .env.example .env
   ```

2. **Starten:**
   ```bash
   # Standard-Start (erfordert funktionierendes Docker-Netzwerk für Postgres)
   docker-compose up -d
   ```

# Falls Netzwerkfehler (IPv6) beim Herunterladen von Postgres auftreten:
   ```bash
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
3. Dies konfiguriert automatisch die Firewall, den IP-Hilfsdienst und den Netzwerk-Proxy auf Ihrem Windows-Host. Das Skript erkennt nun auch automatisch die korrekten IPs Ihrer Dienste.
4. Die Agenten verfügen zudem über einen automatischen Fallback auf das Netzwerk-Gateway, falls `host.docker.internal` nicht auflösbar ist.

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

Persistenz erfolgt primär in der Datenbank. Im `data/` Verzeichnis liegen weiterhin:
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
- [docs/backend.md](docs/backend.md) – Backend-Übersicht, Modelle und Auth.
- [docs/coding-conventions.md](docs/coding-conventions.md) – Coding Conventions.
- [docs/extensions.md](docs/extensions.md) – Extensions und Custom Roles.
- [docs/beta-feedback.md](docs/beta-feedback.md) – Beta-Feedback-Plan.
- [agent/README.md](agent/README.md) – Handbuch für den AI-Agent.
