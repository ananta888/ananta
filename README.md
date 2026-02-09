# Ananta 🚀

Ein modulares Multi-Agent-System für AI-gestützte Entwicklung. Ananta ermöglicht die Orchestrierung von unabhängigen Agenten (**Hub** & **Worker**) zur Automatisierung von Entwicklungsaufgaben.

---

## 🏗️ Architektur

Ananta nutzt ein effizientes Hub-Worker-Modell:
- **Angular Frontend**: Zentrale Steuereinheit zur Visualisierung und Task-Verwaltung.
- **AI Agent (Hub)**: Der Koordinator. Verwaltet Tasks, Templates, Teams und delegiert Arbeit an Worker.
- **AI Agent (Worker)**: Die ausführende Kraft. Interagiert mit LLMs und führt Shell-Befehle aus.
- **Persistenz**: Unterstützt PostgreSQL (Produktion) und SQLite (Entwicklung/Einfachheit).

---

## ⚡ Quickstart

Der schnellste Weg zum Starten ist **Docker Compose**:

### 1. Vorbereitung
Kopieren Sie die Beispiel-Konfiguration:
```bash
cp .env.example .env
```
*(Optional: Passen Sie `INITIAL_ADMIN_PASSWORD` in der `.env` an.)*

### 2. Starten

Wählen Sie eine der folgenden Varianten (je nach Bedarf an Datenbank und Features):

#### A. Leichtgewicht-Modus (SQLite) - Ideal für schnelles Testen
Nutzt lokale SQLite-Dateien. Keine Einrichtung von Postgres nötig.
```bash
docker compose -f docker-compose.base.yml -f docker-compose.sqlite.yml up -d
```

#### B. Standard-Modus (Postgres & Redis) - Empfohlen für Entwicklung
Nutzt Postgres für Daten und Redis für Caching/Events.
```bash
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml up -d
```

#### C. Full-Modus (Edge & Monitoring) - Für produktivnahe Umgebungen
Inklusive Nginx-Proxy, SSL (Certbot) und Observability-Stack (Grafana, Loki).
```bash
docker compose -f docker-compose.base.yml -f docker-compose.yml up -d
```
*(Hinweis: Für diesen Modus müssen Sie Profile wie `--profile edge` oder `--profile observability` verwenden, um die Zusatzdienste zu aktivieren.)*

### 3. Zugriff
- **Frontend**: [http://localhost:4200](http://localhost:4200)
- **Hub API**: [http://localhost:5000](http://localhost:5000)
- **Standard-Login**: `admin` / `admin_change_me` (falls kein Passwort gesetzt wurde)

---

## 🛠️ Entwicklung & Qualitätssicherung

### Lokale Ausführung (ohne Docker)
Detaillierte Anleitungen finden Sie in den jeweiligen Modulen:
- [Backend (Python Agent)](agent/README.md)
- [Frontend (Angular)](frontend-angular/README.md)
- [Datenbank-Modelle (Backend)](agent/DATABASE.md)

### Tests ausführen
- **Backend-Tests**: `pytest`
- **Frontend E2E-Tests**: `cd frontend-angular && npm run test:e2e`

### 🛡️ Sicherheit & Authentifizierung
Die API verwendet JWT-basierte Authentifizierung.
- Ein initialer Admin-Account wird beim ersten Start angelegt.
- Passwörter können in den Einstellungen geändert werden.
- Multi-Faktor-Authentifizierung (MFA) wird unterstützt.

---

## 🔍 Fehlerbehebung (Troubleshooting)

### LLM-Verbindungsprobleme (`Connection refused`)
Falls Agenten keine Verbindung zu lokalen LLMs (Ollama/LMStudio) herstellen können:
1. Führen Sie **`setup_host_services.ps1`** mit PowerShell aus.
2. Dies konfiguriert Firewall, Proxy-Einstellungen und optimiert Kernel-Einstellungen (z.B. für Redis) auf dem Windows-Host automatisch.

### Redis Warnung: `vm.overcommit_memory`
Falls Redis im Log warnt, dass `vm.overcommit_memory` deaktiviert ist:
- Dies kann die Stabilität bei Speicherengpässen beeinträchtigen.
- `setup_host_services.ps1` versucht dies automatisch in WSL2 zu beheben.
- Manuell: `wsl -u root sh -c "echo 1 > /proc/sys/vm/overcommit_memory"`

### Docker Hot-Reload unter Windows
Dateisystem-Events werden oft nicht zuverlässig an Container übertragen.
- Das Frontend nutzt Polling zur Erkennung von Änderungen.
- Der Angular-Cache ist deaktiviert, um Build-Inkonsistenzen zu vermeiden.

---

## 📚 Weiterführende Dokumentation

Inhaltlich tiefergehende Informationen finden Sie im `docs/` Verzeichnis:
- [Installation & Betrieb](docs/INSTALL_TEST_BETRIEB.md)
- [API-Spezifikation](api-spec.md)
- [Backend-Architektur & Modelle](docs/backend.md)
- [Entwicklungs-Roadmap](docs/roadmap.md)
- [Coding Conventions](docs/coding-conventions.md)

---

*Ananta - Simplify AI Orchestration.*
