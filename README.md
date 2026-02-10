# Ananta 🚀

Ein modulares Multi-Agent-System für AI-gestützte Entwicklung. Ananta ermöglicht die Orchestrierung von unabhängigen Agenten (**Hub** & **Worker**) zur Automatisierung von Entwicklungsaufgaben.

---

## 🏗️ Architektur

Ananta nutzt ein effizientes Hub-Worker-Modell:
- **Angular Frontend**: Zentrale Steuereinheit zur Visualisierung und Task-Verwaltung.
- **AI Agent (Hub)**: Der Koordinator. Verwaltet Tasks, Templates, Teams und delegiert Arbeit an Worker.
- **AI Agent (Worker)**: Die ausführende Kraft. Interagiert mit LLMs und führt Shell-Befehle aus.
- **Persistenz**: Unterstützt PostgreSQL (Produktion) und SQLite (Entwicklung/Einfachheit).

Detaillierte Architektur-Infos finden Sie unter [Backend-Architektur & Modelle](docs/backend.md).

---

## Begriffe

- **Hub**: Zentraler Agent, der Tasks, Teams, Templates und die Agenten-Registry verwaltet.
- **Worker**: Ausführender Agent, der LLM-gestützte Vorschläge erzeugt und Shell-Kommandos ausführt.
- **Task**: Ein Arbeitspaket mit Status, Priorität und History.
- **Template**: Prompt-Vorlage für wiederkehrende Aufgaben.
- **Team**: Gruppe von Agenten mit Rollen und optionalen Template-Zuordnungen.

---

## ⚡ Quickstart (Docker)

Der schnellste Weg zum Starten ist **Docker Compose**:

### 1. Vorbereitung
```bash
cp .env.example .env
```
*(Passen Sie `INITIAL_ADMIN_PASSWORD` in der `.env` an.)*

### 2. Starten

| Modus | Beschreibung | Befehl |
| :--- | :--- | :--- |
| **SQLite** | Leichtgewicht, ideal für schnelles Testen. | `docker compose -f docker-compose.base.yml -f docker-compose.sqlite.yml up -d` |
| **Standard** | Postgres & Redis, empfohlen für Entwicklung. | `docker compose -f docker-compose.base.yml -f docker-compose-lite.yml up -d` |
| **Standard (Windows/Rancher robust)** | Nutzt WSL-Pfadkonvertierung und weicht bei Port-Konflikten auf freie Ports aus. | `powershell -ExecutionPolicy Bypass -File devtools/compose-lite.ps1 -Action up -Build` |
| **Full** | Edge (Nginx) & Observability (Grafana). | `docker compose -f docker-compose.base.yml -f docker-compose.yml --profile edge --profile observability up -d` |

### 3. Zugriff
- **Frontend**: [http://localhost:4200](http://localhost:4200)
- **Hub API**: [http://localhost:5000](http://localhost:5000)
- **Login**: `admin` / (Ihr gesetztes Passwort)

---

## 🛠️ Entwicklung & Qualitätssicherung

### Lokale Ausführung (ohne Docker)
Anleitungen zur manuellen Installation finden Sie hier:
- [Backend (Python Agent)](agent/README.md)
- [Frontend (Angular)](frontend-angular/README.md)
- [Gesamtsystem-Installation](docs/INSTALL_TEST_BETRIEB.md)

### Tests ausführen
- **Backend-Tests**: `pytest`
- **Frontend E2E-Tests**: `cd frontend-angular && npm run test:e2e`
- **Hinweis E2E-Isolation**: Der E2E-Runner erwartet standardmäßig isolierte Backend-Prozesse und bricht ab, wenn bereits Dienste auf `5000/5001/5002` laufen. Reuse nur bewusst mit `ANANTA_E2E_USE_EXISTING=1`.

### Linting
- **Backend (flake8)**: `python -m flake8 agent tests`
- **Frontend**: `cd frontend-angular && npm run lint`

---

## 🔍 Troubleshooting

### LLM-Verbindung (`Connection refused`)
Falls Agenten keine Verbindung zu Ollama/LMStudio herstellen können:
1. **`setup_host_services.ps1`** mit PowerShell ausführen. Dies konfiguriert Firewall und Proxy automatisch.
2. Sicherstellen, dass Ollama auf `0.0.0.0` lauscht (`OLLAMA_HOST=0.0.0.0`).

### Redis Warnung: `vm.overcommit_memory`
- `setup_host_services.ps1` versucht dies automatisch zu beheben.
- Manuell: `wsl -u root sh -c "echo 1 > /proc/sys/vm/overcommit_memory"`

---

## 📚 Weiterführende Dokumentation

- [Installation & Betrieb](docs/INSTALL_TEST_BETRIEB.md)
- [API-Spezifikation](api-spec.md)
- [Backend & Datenmodelle](docs/backend.md)
- [Entwicklungs-Roadmap](docs/roadmap.md)
- [Coding Conventions](docs/coding-conventions.md)

---

*Ananta - Simplify AI Orchestration.*
