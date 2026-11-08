# Installations-, Test- und Betriebsanleitung

Dieses Dokument beschreibt die Schritte zur Installation, zum Testen und zum Betrieb des Ananta Multi-Agent-Systems.

## 1. Schnellstart

Es gibt zwei offizielle Startpfade:

1. CLI-first lokal ohne Docker (fuer Goal-/CLI-Workflows).
2. Full-Stack mit Docker Compose und Web-UI.

### Voraussetzungen
- **Docker & Docker Compose**: Empfohlen fuer den schnellsten Start.
- **Python 3.10+**: Fuer lokale Tools, Tests und manuelle Agent-Starts.
- **Node.js 20.19+ & npm**: Fuer die manuelle Frontend-Entwicklung.
- **Postgres 16**: Nur noetig, wenn nicht der Compose-Stack genutzt wird.

### A. CLI-first ohne Docker
1. Runtime-Profil erzeugen:
   ```bash
   ananta init --yes --runtime-mode local-dev --llm-backend ollama --model ananta-default
   ```
2. Readiness pruefen:
   ```bash
   ananta first-run
   ananta status
   ananta doctor
   ```
3. Erstes Goal starten:
   ```bash
   ananta plan "Analysiere dieses Repository und schlage die naechsten Schritte vor"
   ```

Weiterfuehrung: `docs/setup/quickstart.md`, `docs/cli/commands.md`.

### B. Schnellstart mit Docker (Empfohlen fuer UI + Full-Stack)
1. Automatisches Setup ausfuehren:
   ```powershell
   .\setup.ps1
   ```
   Alternativ `.env.example` nach `.env` kopieren und `INITIAL_ADMIN_PASSWORD` setzen.

2. Compose-next-Quickstart starten:
   ```bash
   docker compose --env-file .env -f docker/compose-next/compose.stack.quickstart.yml up -d --build
   ```

3. Browser oeffnen:
   - Frontend: `http://localhost:4200`
   - Hub-Agent: `http://localhost:5000`

4. Einloggen:
   - Benutzer: `admin`
   - Passwort: Wert aus `INITIAL_ADMIN_PASSWORD` in `.env`

5. Im Dashboard ein erstes Ziel eingeben oder die Demo-Vorschau oeffnen.

### B2. Compose-next Fullstack

Wenn Hub, zwei Worker, Angular, PostgreSQL und Redis zusammen laufen sollen:

```bash
docker build -f docker/compose-next/Dockerfile.quickstart-no-ollama -t ananta-quickstart-no-ollama:local .
POSTGRES_PASSWORD=... docker compose --env-file .env -f docker/compose-next/compose.stack.full.yml up -d --build
```

Provider-Varianten:

```bash
# OpenAI
POSTGRES_PASSWORD=... DEFAULT_PROVIDER=openai OPENAI_API_KEY=<SECRET> \
docker compose --env-file .env -f docker/compose-next/compose.stack.full.yml up -d --build

# LM Studio
POSTGRES_PASSWORD=... DEFAULT_PROVIDER=lmstudio LMSTUDIO_URL=http://host.docker.internal:1234/v1 \
docker compose --env-file .env -f docker/compose-next/compose.stack.full.yml up -d --build
```

Hinweise:
- Bei `DEFAULT_PROVIDER=openai` muss `OPENAI_API_KEY` gesetzt sein.
- Ollama ist in diesem Pfad nicht erforderlich.
- Rollen bleiben logisch getrennt (separate Container), verwenden aber dasselbe Image-Artefakt.

### C. Welche Compose-Variante passt?

| Ziel | Variante | Empfehlung |
| --- | --- | --- |
| Demo ansehen oder erstes Goal planen | `compose.stack.quickstart.yml` | Standard fuer neue Nutzer. |
| Lokal mit LM Studio entwickeln | `compose.dev.lmstudio.yml` | Bind-Mounts und Reloads. |
| Lokal mit Ollama entwickeln | `compose.dev.ollama.yml` | Lokale Ollama-Runtime und Bind-Mounts. |
| Persistenter Fullstack | `compose.stack.full.yml` | PostgreSQL und Redis. |
| Worker-Verteilung pruefen | `compose.stack.distributed.yml` | Fuer Routing- und Mehr-Worker-Tests. |
| Automatisierte Browsertests | `docker/old_way/` | Bestehender Legacy-Testpfad. |

### D. Weitere Compose-Befehle

   Fuer den Compose-Ollama-Service:
   ```bash
   POSTGRES_PASSWORD=... docker compose --env-file .env -f docker/compose-next/compose.dev.ollama.yml up -d --build
   ```
   Auf Windows mit Rancher/WSL-Kontext empfiehlt sich stattdessen:
   ```powershell
   powershell -ExecutionPolicy Bypass -File devtools/compose-lite.ps1 -Action up -Build
   ```

### E. Manuelle Installation (Entwicklung)

#### AI-Agent (Hub oder Worker)
1. In das Verzeichnis `agent/` wechseln.
2. Abhängigkeiten installieren: `pip install -r requirements.txt`.
3. Starten:
   ```bash
   # Als Hub (Standard-Port 5000)
   ROLE=hub 
```

## Hindsight (optionaler Erfahrungsspeicher)

Hindsight ist ein optionaler Erfahrungsspeicher für Ananta und wird nur bei nachgewiesenem Mehrwert integriert. Die Integration muss die folgenden Punkte nachvollziehbar dokumentieren.

### Upstream-Version, Lizenz und API-Verträge
- **Upstream-Commit oder Release**: Exakten Commit-SHA oder Release-Tag festhalten (z. B. `hindsight@vX.Y.Z` oder `commit: <sha>`).
- **Lizenzdateien**: Lizenztext und -bedingungen des Upstream-Projekts beibehalten; bei kommerzieller Nutzung prüfen, ob die Lizenz Ananta-Deployment erlaubt.
- **API-Verträge**: Offizielle API-Spezifikation (OpenAPI/REST) dokumentieren; Abhängigkeiten von OpenAI-Kompatibilität nicht als einzige Grundlage für lokale Providerfähigkeit verwenden.

### LLM-, Embedding- und Reranking-Aufgaben getrennt
| Aufgabe | Zweck | Lokale Providerfähigkeit | Ressourcenbedarf |
| --- | --- | --- | --- |
| **LLM** (retain/reflect) | Erfahrungsspeicherung, Zusammenfassung, Reflexion | Ollama, LM Studio, lokale LLM-Runtimes; OpenAI nur als optionaler Cloud-Provider | GPU/CPU, Modellgröße, Kontextfenster |
| **Embedding** (recall) | Vektorisierung von Erfahrungen für Suche | Lokale Embedding-Modelle (z. B. via Ollama/LM Studio); keine OpenAI-Kompatibilität voraussetzen | Speicher für Indexe, Batch-Größe, Latenz |
| **Reranking** (recall) | Sortierung relevanter Erinnerungen | Lokales Reranker-Modell oder heuristischer Score; API-Abhängigkeit vermeiden | Zusätzliche Laufzeit, Modellgröße |

### Token-, Speicher- und Laufzeitkosten
- **retain**: Token-Kosten für Speicherverarbeitung (Zusammenfassung/Extraktion) pro Erfahrungseintrag.
- **recall**: Token-Kosten für Embedding-Erzeugung und Reranking bei Abfragen; Speicherbedarf für Vektorindexe (z. B. FAISS, pgvector).
- **reflect**: Token-Kosten für periodische Reflexion/Zusammenfassung von Erfahrungen.
- **Hintergrundkonsolidierung**: Laufzeitkosten für asynchrone Konsolidierungs-Jobs (Speicher, CPU/GPU, Token); Skalierung bei wachsendem Erfahrungsspeicher prüfen.

### Datenbank-Erweiterungen
- Bei Integration neue Tabellen/Indizes für Hindsight-Daten dokumentieren (z. B. `hindsight_experiences`, `hindsight_vectors`).
- Migrationen mit Versionierung nachvollziehbar festhalten; Rollback-Strategie definieren.

### Betrieb und Evaluation
- Hindsight als optionalen Service betreiben; bei deaktiviertem Zustand muss Ananta ohne zusätzliche Kosten laufen.
- Vor Integration Mehrwert messen (z. B. Reduktion von Wiederholungsfragen, bessere Kontextrelevanz) und Ergebnisse in `docs/reviewed-commit-backlog.md` oder einem separaten Evaluation-Dokument festhalten.
- Fehlversuche und Retrospektiven mit zeitlichen Änderungen nachvollziehbar auswerten; vorhandene MemoryTree-, CodeCompass- und Hub-Governance-Mechanismen wiederverwenden.

## 2. Test

### Selbsttests
```bash
python -m pytest -q --confcutdir=tests/self_development -o addopts= -o cache_dir=/tmp/ananta-self-dev-cache tests/self_development
```

### LLM-Benchmarks
- API: `/llm/benchmarks/record` (POST) und `/llm/benchmarks` (GET).
- Admin-Rechte erforderlich; Provider und Modell müssen angegeben werden.
- Zeitreihen: `/llm/benchmarks/timeseries?provider=...&model=...&task_kind=...&bucket=day&days=30`

### CodeCompass / Malware-Analyse
- API-Classification: `agent/codecompass/x86/malware/api_classifier.py`.
- LLM-Assist-Policy: `agent/codecompass/x86/malware/llm_assist.py` (nur defensive Vorschläge: name/comment/summary).

## 3. Betrieb

### Monitoring
- Hub-Agent: `http://localhost:5000`
- Frontend: `http://localhost:4200`
- Logs: `docker compose logs -f` oder lokale Logdateien.

### Backup und Restore
- PostgreSQL-Backup vor Datenbank-Erweiterungen (z. B. Hindsight).
- Redis-Daten bei Bedarf sichern; Volatile Caches können verloren gehen.

### Sicherheit
- Zero-Trust-Agent-Memory: Siehe `docs/security/zero-trust-agent-memory.md`.
- LLM-Benchmarks nur für Admins;
- Hindsight-Daten als vertraulich behandeln; Zugriff kontrollieren.

## 4. Fehlerbehebung

```bash
ananta doctor
ananta status
```

Bei Compose-Problemen:
```bash
docker compose ps
docker compose logs --tail=100
```
